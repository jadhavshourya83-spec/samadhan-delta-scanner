import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

BASE = "https://api.india.delta.exchange"
S = requests.Session()
S.headers.update({"Accept": "application/json", "User-Agent": "SamadhanDeltaScanner/1.0"})

st.set_page_config(page_title="Samadhan Delta Scanner", page_icon="📡", layout="wide")
st.title("📡 SAMADHAN DELTA SCANNER — SIGNAL ➜ MOMENTUM")
st.caption("Stage 1: Signal stocks (ticker data) | Stage 2: Momentum (candles) | Public data only | No auto-orders")

with st.sidebar:
    scan_limit = st.slider("Max signal stocks to send to Stage 2", 5, 40, 20, 5)
    min_signal = st.slider("Minimum SIGNAL score (Stage 1)", 40, 90, 55, 5)
    min_score = st.slider("Minimum MOMENTUM score (Stage 2)", 60, 90, 75, 5)
    min_turnover = st.number_input("Minimum 24h turnover", 0, value=10000, step=10000)
    refresh = st.selectbox("Refresh (seconds)", [30, 60, 90, 120], index=1)
    st.caption("Final result = Signal direction == Momentum direction. Nahi tar NO TRADE.")


def get(path, params=None):
    r = S.get(BASE + path, params=params, timeout=12)
    r.raise_for_status()
    j = r.json()
    if isinstance(j, dict) and j.get("success") is False:
        raise RuntimeError(str(j.get("error", "API error")))
    return j.get("result", j.get("data", j)) if isinstance(j, dict) else j


def f(d, *keys, default=0.0):
    for k in keys:
        try:
            if d.get(k) not in (None, ""):
                return float(d[k])
        except (ValueError, TypeError, AttributeError):
            pass
    return default


def pct(now, before):
    return (now / before - 1) * 100 if before else 0.0


# ───────────────────────── STAGE 1 : SIGNAL FORMULA ─────────────────────────
# Fakt ticker data vaparto (candle call nahi) mhanun sagle symbols var fast chalto.
#
# SIGNAL SCORE (0-100) =
#     30 * turnover_percentile        (baki coins peksha kiti jast activity)
#   + 25 * min(1, |24h change| / 4%)  (24h madhe kiti move zala)
#   + 25 * min(1, |OI change 6h| / 5%) (open interest kiti badalla)
#   + 20 jar price 24h range chya top 20% kinva bottom 20% madhe asel
#
# DIRECTION:
#   UP   = 24h change > 0 ani price range chya vartchya arddhat
#   DOWN = 24h change < 0 ani price range chya khalchya arddhat
def signal_score(t, turn_pct):
    price = f(t, "close", "mark_price")
    hi, lo, op = f(t, "high"), f(t, "low"), f(t, "open")
    chg24 = f(t, "ltp_change_24h", "mark_change_24h", default=None)
    if chg24 is None:
        chg24 = pct(price, op)
    rng_pos = (price - lo) / (hi - lo) if hi > lo else 0.5
    oi_val = f(t, "oi_value_usd")
    oi_chg = f(t, "oi_change_usd_6h")
    oi_pct = oi_chg / oi_val * 100 if oi_val > 0 else 0.0

    score = (
        30 * turn_pct
        + 25 * min(1.0, abs(chg24) / 4)
        + 25 * min(1.0, max(0.0, oi_pct) / 5)
        + (20 if (rng_pos >= 0.8 or rng_pos <= 0.2) else 0)
    )
    if chg24 > 0 and rng_pos >= 0.5:
        direction = "UP"
    elif chg24 < 0 and rng_pos <= 0.5:
        direction = "DOWN"
    else:
        direction = "WAIT"
    return {
        "Signal Score": round(score, 1),
        "Signal Dir": direction,
        "24h Change %": chg24,
        "OI Change 6h %": oi_pct,
        "Range Pos": rng_pos,
    }


@st.cache_data(ttl=20, show_spinner=False)
def market_snapshot(min_turnover, scan_limit, min_signal):
    products = get("/v2/products", {"contract_types": "perpetual_futures", "states": "live", "page_size": 200})
    tickers = get("/v2/tickers", {"contract_types": "perpetual_futures"})
    if isinstance(products, dict):
        products = products.get("result", products.get("products", []))
    if isinstance(tickers, dict):
        tickers = list(tickers.values())
    products = products if isinstance(products, list) else []
    tickers = tickers if isinstance(tickers, list) else []

    by_symbol = {}
    for t in tickers:
        if isinstance(t, dict):
            sym = t.get("symbol") or t.get("product_symbol")
            if sym:
                by_symbol[sym] = t
    symbols = [p.get("symbol") for p in products if isinstance(p, dict) and p.get("symbol")]
    if not symbols:
        symbols = list(by_symbol)

    pool = []
    for sym in symbols:
        t = by_symbol.get(sym)
        if not t:
            continue
        turnover = f(t, "turnover_usd", "turnover", "volume")
        if turnover >= min_turnover:
            pool.append((sym, t, turnover))

    # turnover percentile (0..1)
    order = sorted(pool, key=lambda x: x[2])
    n = max(len(order) - 1, 1)
    rank = {sym: i / n for i, (sym, _, _) in enumerate(order)}

    signals = []
    for sym, t, turnover in pool:
        s = signal_score(t, rank[sym])
        s["Turnover"] = turnover
        if s["Signal Score"] >= min_signal and s["Signal Dir"] != "WAIT":
            signals.append((sym, t, s))
    signals.sort(key=lambda x: x[2]["Signal Score"], reverse=True)
    return signals[:scan_limit], len(symbols), len(tickers), len(pool)


# ───────────────────────── STAGE 2 : MOMENTUM FORMULA ─────────────────────────
# Fakt Stage 1 madhe ale tya symbols var 5m candles ghetle jatat.
#
# MOMENTUM SCORE (UP) =
#     25 EMA20 > EMA50
#   + 25 price pichlya 20 candle chya high chya var (breakout)
#   + 20 volume >= 1.5x median ani 15m change > 0
#   + 15 6-candle change > 0.15%
#   + 10 price > EMA20
# (DOWN sathi ulta)
def candles(symbol, resolution="5m", bars=100):
    end = int(time.time())
    sec = {"1m": 60, "5m": 300, "15m": 900}[resolution]
    rows = get("/v2/history/candles", {"resolution": resolution, "symbol": symbol,
                                       "start": end - sec * bars, "end": end - sec})
    if not isinstance(rows, list) or not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).rename(columns={
        "o": "open", "h": "high", "l": "low", "c": "close", "v": "volume",
        "t": "time", "timestamp": "time"
    })
    if not all(c in df for c in ("open", "high", "low", "close")):
        return pd.DataFrame()
    for c in ("open", "high", "low", "close", "volume"):
        if c not in df:
            df[c] = 0
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["open", "high", "low", "close"]).sort_values(
        "time" if "time" in df else "close").reset_index(drop=True)


def momentum(symbol, sig, threshold):
    try:
        d = candles(symbol)
        if len(d) < 40:
            return None
        close, high, low, vol = d.close, d.high, d.low, d.volume.fillna(0)
        e20 = close.ewm(span=20, adjust=False).mean()
        e50 = close.ewm(span=50, adjust=False).mean()
        price = float(close.iloc[-1])
        chg3 = pct(price, float(close.iloc[-4]))   # 3 x 5m = 15 min
        chg6 = pct(price, float(close.iloc[-7]))   # 6 x 5m = 30 min
        prev_hi = float(high.iloc[-21:-1].max())
        prev_lo = float(low.iloc[-21:-1].min())
        baseline = float(vol.iloc[-21:-1].median())
        vr = float(vol.iloc[-1]) / baseline if baseline > 0 else 0
        uptrend, downtrend = e20.iloc[-1] > e50.iloc[-1], e20.iloc[-1] < e50.iloc[-1]
        upbreak, downbreak = price > prev_hi, price < prev_lo

        up = (25 if uptrend else 0) + (25 if upbreak else 0) + (20 if vr >= 1.5 and chg3 > 0 else 0) \
            + (15 if chg6 > .15 else 0) + (10 if price > e20.iloc[-1] else 0)
        dn = (25 if downtrend else 0) + (25 if downbreak else 0) + (20 if vr >= 1.5 and chg3 < 0 else 0) \
            + (15 if chg6 < -.15 else 0) + (10 if price < e20.iloc[-1] else 0)
        mom_dir = "UP" if up >= threshold and up > dn else ("DOWN" if dn >= threshold and dn > up else "WAIT")

        return {
            "Symbol": symbol, "Price": price,
            "Signal Score": sig["Signal Score"], "Signal Dir": sig["Signal Dir"],
            "24h Change %": sig["24h Change %"], "OI Change 6h %": sig["OI Change 6h %"],
            "Momentum Dir": mom_dir, "Momentum Score": max(up, dn),
            "15m Change %": chg3, "Volume x Median": vr,
            "FINAL": mom_dir if (mom_dir != "WAIT" and mom_dir == sig["Signal Dir"]) else "NO",
            "Reason": f"EMA20/50 {'UP' if uptrend else 'DOWN' if downtrend else 'FLAT'}; "
                      f"breakout {'UP' if upbreak else 'DOWN' if downbreak else 'NO'}; volume {vr:.2f}x",
        }
    except Exception:
        return None


# ───────────────────────── UI ─────────────────────────
st.markdown(f'<meta http-equiv="refresh" content="{refresh}">', unsafe_allow_html=True)
try:
    with st.spinner("Stage 1: Signal stocks shodhat ahe..."):
        signals, n_products, n_tickers, n_pool = market_snapshot(min_turnover, scan_limit, min_signal)

    st.caption(f"Scan time: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')} | Products: {n_products} | "
               f"Tickers: {n_tickers} | Turnover filter pass: {n_pool} | Signal stocks: {len(signals)}")

    if not signals:
        st.warning("Stage 1 madhe ekahi signal stock nahi — NO TRADE. Min signal score kami karun bagha.")
        st.stop()

    st.subheader("🎯 STAGE 1 — SIGNAL STOCKS")
    sig_df = pd.DataFrame([{"Symbol": s, **v} for s, _, v in signals])
    st.dataframe(sig_df[["Symbol", "Signal Dir", "Signal Score", "24h Change %", "OI Change 6h %", "Turnover"]],
                 hide_index=True, use_container_width=True)

    rows = []
    progress = st.progress(0)
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs = {pool.submit(momentum, sym, sig, min_score): sym for sym, _, sig in signals}
        for i, future in enumerate(as_completed(jobs), 1):
            r = future.result()
            if r:
                rows.append(r)
            progress.progress(i / len(jobs))
    progress.empty()

    df = pd.DataFrame(rows)
    if df.empty:
        st.warning("Candle data purese nahi. Pudhchya refresh madhe punha tapasa.")
        st.stop()

    final = df[df.FINAL.isin(["UP", "DOWN"])].sort_values("Momentum Score", ascending=False).head(2)
    waiting = df[(df.FINAL == "NO")].sort_values(["Signal Score", "Momentum Score"], ascending=False).head(5)

    a, b, c = st.columns(3)
    a.metric("Signal stocks", len(signals))
    b.metric("Momentum checked", len(df))
    c.metric("FINAL (Signal + Momentum)", len(df[df.FINAL.isin(["UP", "DOWN"])]))

    st.subheader("🏆 STAGE 2 — SIGNAL + MOMENTUM (FINAL)")
    if final.empty:
        st.warning("Signal ani Momentum dogh ekach direction made nahit — NO TRADE.")
    else:
        for _, r in final.iterrows():
            with st.container(border=True):
                x, y, z, w = st.columns(4)
                x.metric("Asset", r.Symbol)
                y.metric("Direction", "🟢 UP" if r.FINAL == "UP" else "🔴 DOWN")
                z.metric("Signal / Momentum", f'{r["Signal Score"]:.0f} / {int(r["Momentum Score"])}')
                w.metric("Volume vs median", f'{r["Volume x Median"]:.2f}x')
                st.write(f'Price: **{r.Price}** · 15m change: **{r["15m Change %"]:.2f}%** · '
                         f'24h: **{r["24h Change %"]:.2f}%** · OI 6h: **{r["OI Change 6h %"]:.2f}%**')
                st.caption(r.Reason)

    st.subheader("👀 WATCHLIST — Signal ahe, Momentum adhun nahi")
    if waiting.empty:
        st.info("Watchlist rikami.")
    else:
        st.dataframe(waiting[["Symbol", "Signal Dir", "Signal Score", "Momentum Dir", "Momentum Score",
                              "15m Change %", "Volume x Median", "Reason"]],
                     hide_index=True, use_container_width=True)

    with st.expander("All analyzed symbols (debug)"):
        st.dataframe(df.sort_values(["FINAL", "Momentum Score"], ascending=False),
                     hide_index=True, use_container_width=True)

    st.info("Signal score he proxy ahe (turnover + 24h move + OI change + range position); "
            "operator kon ahe he public data sidh karat nahi. Real paisa lavnyaadhi paper-trade ani backtest kara. "
            "Konatehi orders place hot nahit.")
except requests.RequestException as e:
    st.error(f"Delta API network error: {e}")
except Exception as e:
    st.error(f"Scanner error: {e}")
    st.exception(e)
