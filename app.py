import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone

import pandas as pd
import requests
import streamlit as st

BASE = "https://api.india.delta.exchange"
S = requests.Session()
S.headers.update({"Accept": "application/json", "User-Agent": "SamadhanDeltaScanner/2.0"})

st.set_page_config(page_title="Samadhan Delta Scanner v2", page_icon="📡", layout="wide")
st.title("📡 SAMADHAN DELTA SCANNER v2 — EARLY SIGNAL ➜ FRESH MOMENTUM")
st.caption("Aadhi move zalele coins nako | Ata sutnara momentum pahije | Public data only | No auto-orders")

with st.sidebar:
    max_move = st.slider("Max 24h move % (jyat aadhich move zali te kadha)", 2, 10, 5, 1)
    scan_limit = st.slider("Max signal stocks to send to Stage 2", 5, 40, 20, 5)
    min_signal = st.slider("Minimum SIGNAL score (Stage 1)", 40, 90, 55, 5)
    min_score = st.slider("Minimum MOMENTUM score (Stage 2)", 50, 90, 70, 5)
    min_turnover = st.number_input("Minimum 24h turnover", 0, value=10000, step=10000)
    refresh = st.selectbox("Refresh (seconds)", [30, 60, 90, 120], index=1)
    st.caption("FINAL = Signal dir == Momentum dir AND move aajun EXTENDED nahi.")


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


# ───────────────────────── STAGE 1 : EARLY SIGNAL FORMULA ─────────────────────────
# Dhyey: aadhi motha move zalela coin NAKO. Activity vadhat ahe pan price ajun jast halla nahi.
#
# GATE: |24h change| > max_move  ->  EXTENDED, direct baad.
#
# SIGNAL SCORE (0-100) =
#     30 * turnover percentile                 (baki coins peksha jast activity)
#   + 30 * min(1, max(0, OI change 6h) / 3%)  (nave positions ugadat ahet -> OI VADHLA tarach)
#   + 25 * (1 - |24h change| / max_move)       (kiti "taaja" ahe; jitka kami move titka jast)
#   + 15 jar price 24h range chya edge javal pan edge war nahi:
#         UP   -> range position 0.60 .. 0.92
#         DOWN -> range position 0.08 .. 0.40
def signal_score(t, turn_pct, max_move):
    price = f(t, "close", "mark_price")
    hi, lo, op = f(t, "high"), f(t, "low"), f(t, "open")
    chg24 = f(t, "ltp_change_24h", "mark_change_24h", default=None)
    if chg24 is None:
        chg24 = pct(price, op)
    rng_pos = (price - lo) / (hi - lo) if hi > lo else 0.5
    oi_val = f(t, "oi_value_usd")
    oi_chg = f(t, "oi_change_usd_6h")
    oi_pct = oi_chg / oi_val * 100 if oi_val > 0 else 0.0

    fresh = 1 - min(1.0, abs(chg24) / max_move)
    if chg24 > 0 and rng_pos >= 0.55:
        direction = "UP"
    elif chg24 < 0 and rng_pos <= 0.45:
        direction = "DOWN"
    else:
        direction = "WAIT"
    approach = (direction == "UP" and 0.60 <= rng_pos <= 0.92) or \
               (direction == "DOWN" and 0.08 <= rng_pos <= 0.40)

    score = 30 * turn_pct + 30 * min(1.0, max(0.0, oi_pct) / 3) + 25 * fresh + (15 if approach else 0)
    if abs(chg24) > max_move:
        direction = "WAIT"          # already moved -> skip
    return {"Signal Score": round(score, 1), "Signal Dir": direction,
            "24h Change %": chg24, "OI Change 6h %": oi_pct, "Range Pos": rng_pos}


@st.cache_data(ttl=20, show_spinner=False)
def market_snapshot(min_turnover, scan_limit, min_signal, max_move):
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

    order = sorted(pool, key=lambda x: x[2])
    n = max(len(order) - 1, 1)
    rank = {sym: i / n for i, (sym, _, _) in enumerate(order)}

    signals, extended = [], 0
    for sym, t, turnover in pool:
        s = signal_score(t, rank[sym], max_move)
        s["Turnover"] = turnover
        if abs(s["24h Change %"]) > max_move:
            extended += 1
        if s["Signal Score"] >= min_signal and s["Signal Dir"] != "WAIT":
            signals.append((sym, t, s))
    signals.sort(key=lambda x: x[2]["Signal Score"], reverse=True)
    return signals[:scan_limit], len(symbols), len(tickers), len(pool), extended


# ───────────────────────── STAGE 2 : FRESH MOMENTUM FORMULA ─────────────────────────
# Dhyey: breakout ATTA zala pahije (shevtchya 3 candle madhe), tyachya aadhi price shant (squeeze) hota,
# ani volume ATTA vadhla pahije. Jar price already far geli asel -> EXTENDED -> reject.
#
# MOMENTUM SCORE (UP) =
#     30 FRESH BREAKOUT : shevtchya 3 candle madhe 20-candle high todla ani price tyachya var tikun ahe
#   + 20 SQUEEZE        : breakout adhichi volatility (ATR) 50-candle ATR peksha <= 0.85x hoti
#   + 20 VOLUME NOW     : shevtchya 2 candle madhe volume >= 1.5x median ani candle direction barobar
#   + 15 price > EMA20 ani EMA20 var chadhat ahe
#   + 15 price > EMA50
# (DOWN sathi ulta)
#
# EXTENDED GATE (score = 0, state = EXTENDED) jar:
#     |price - EMA20| > 2.5 ATR    kinva   |30 min move| > 4 ATR   kinva   breakout level pasun > 2 ATR dur
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
        if len(d) < 60:
            return None
        close, high, low, vol = d.close, d.high, d.low, d.volume.fillna(0)
        e20 = close.ewm(span=20, adjust=False).mean()
        e50 = close.ewm(span=50, adjust=False).mean()
        price = float(close.iloc[-1])

        prev_c = close.shift(1)
        tr = pd.concat([high - low, (high - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
        atr_long = float(tr.iloc[-50:].mean())
        atr_before = float(tr.iloc[-23:-3].mean())
        if atr_long <= 0:
            return None
        squeeze_ratio = atr_before / atr_long
        squeeze = squeeze_ratio <= 0.85

        ref_hi = float(high.iloc[-24:-4].max())
        ref_lo = float(low.iloc[-24:-4].min())
        fresh_up = float(close.iloc[-3:].max()) > ref_hi and price > ref_hi
        fresh_dn = float(close.iloc[-3:].min()) < ref_lo and price < ref_lo

        baseline = float(vol.iloc[-23:-3].median())
        vr = float(vol.iloc[-2:].max()) / baseline if baseline > 0 else 0
        chg3 = pct(price, float(close.iloc[-4]))

        ext_e20 = abs(price - float(e20.iloc[-1])) / atr_long
        ext_30m = abs(price - float(close.iloc[-7])) / atr_long
        ext_brk = (price - ref_hi) / atr_long if fresh_up else ((ref_lo - price) / atr_long if fresh_dn else 0)
        extended = ext_e20 > 2.5 or ext_30m > 4 or ext_brk > 2

        e20_up = e20.iloc[-1] > e20.iloc[-4]
        e20_dn = e20.iloc[-1] < e20.iloc[-4]
        up = (30 if fresh_up else 0) + (20 if squeeze else 0) + (20 if vr >= 1.5 and chg3 > 0 else 0) \
            + (15 if price > e20.iloc[-1] and e20_up else 0) + (15 if price > e50.iloc[-1] else 0)
        dn = (30 if fresh_dn else 0) + (20 if squeeze else 0) + (20 if vr >= 1.5 and chg3 < 0 else 0) \
            + (15 if price < e20.iloc[-1] and e20_dn else 0) + (15 if price < e50.iloc[-1] else 0)

        if extended:
            state, up, dn = "EXTENDED", 0, 0
        elif fresh_up or fresh_dn:
            state = "FRESH"
        elif squeeze and ((ref_hi - price) / atr_long <= 0.5 or (price - ref_lo) / atr_long <= 0.5):
            state = "FORMING"          # shant ahe, breakout javal ahe, ajun zala nahi
        else:
            state = "NONE"

        mom_dir = "UP" if up >= threshold and up > dn else ("DOWN" if dn >= threshold and dn > up else "WAIT")
        final = mom_dir if (state == "FRESH" and mom_dir != "WAIT" and mom_dir == sig["Signal Dir"]) else "NO"

        return {
            "Symbol": symbol, "Price": price, "State": state,
            "Signal Score": sig["Signal Score"], "Signal Dir": sig["Signal Dir"],
            "24h Change %": sig["24h Change %"], "OI Change 6h %": sig["OI Change 6h %"],
            "Momentum Dir": mom_dir, "Momentum Score": max(up, dn),
            "15m Change %": chg3, "Volume x Median": vr, "Squeeze (ATR ratio)": squeeze_ratio,
            "Extension (ATR)": ext_e20, "FINAL": final,
            "Reason": f"breakout {'UP' if fresh_up else 'DOWN' if fresh_dn else 'NO'}; "
                      f"squeeze {squeeze_ratio:.2f}; volume {vr:.2f}x; ext {ext_e20:.1f} ATR",
        }
    except Exception:
        return None


# ───────────────────────── UI ─────────────────────────
st.markdown(f'<meta http-equiv="refresh" content="{refresh}">', unsafe_allow_html=True)
try:
    with st.spinner("Stage 1: Early signal stocks shodhat ahe..."):
        signals, n_products, n_tickers, n_pool, n_ext = market_snapshot(min_turnover, scan_limit, min_signal, max_move)

    st.caption(f"Scan time: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')} | Products: {n_products} | "
               f"Tickers: {n_tickers} | Turnover pass: {n_pool} | Aadhi move zalele (kadhle): {n_ext} | "
               f"Early signal stocks: {len(signals)}")

    if not signals:
        st.warning("Stage 1 madhe ekahi early signal nahi — NO TRADE.")
        st.stop()

    st.subheader("🎯 STAGE 1 — EARLY SIGNAL STOCKS")
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
    forming = df[df.State == "FORMING"].sort_values("Signal Score", ascending=False).head(5)

    a, b, c, d_ = st.columns(4)
    a.metric("Early signals", len(signals))
    b.metric("FRESH momentum (FINAL)", len(df[df.FINAL.isin(["UP", "DOWN"])]))
    c.metric("FORMING (breakout javal)", len(df[df.State == "FORMING"]))
    d_.metric("EXTENDED (late, baad)", len(df[df.State == "EXTENDED"]))

    st.subheader("🏆 FRESH MOMENTUM — ata suru zala")
    if final.empty:
        st.warning("Ata taajya breakout cha setup nahi — NO TRADE.")
    else:
        for _, r in final.iterrows():
            with st.container(border=True):
                x, y, z, w = st.columns(4)
                x.metric("Asset", r.Symbol)
                y.metric("Direction", "🟢 UP" if r.FINAL == "UP" else "🔴 DOWN")
                z.metric("Signal / Momentum", f'{r["Signal Score"]:.0f} / {int(r["Momentum Score"])}')
                w.metric("Volume vs median", f'{r["Volume x Median"]:.2f}x')
                st.write(f'Price: **{r.Price}** · 24h: **{r["24h Change %"]:.2f}%** · '
                         f'OI 6h: **{r["OI Change 6h %"]:.2f}%** · Extension: **{r["Extension (ATR)"]:.1f} ATR**')
                st.caption(r.Reason)

    st.subheader("⏳ FORMING — shant ahe, breakout javal ahe (alert sathi watch kara)")
    if forming.empty:
        st.info("Ata kahi forming nahi.")
    else:
        st.dataframe(forming[["Symbol", "Signal Dir", "Signal Score", "Squeeze (ATR ratio)",
                              "Volume x Median", "OI Change 6h %", "Reason"]],
                     hide_index=True, use_container_width=True)

    with st.expander("All analyzed symbols (debug)"):
        st.dataframe(df.sort_values(["FINAL", "Momentum Score"], ascending=False),
                     hide_index=True, use_container_width=True)

    st.info("Koni pan formula future predict karu shakat nahi; he fakt 'ata suru zalela' setups filter karto ani "
            "aadhich dhavlele coins kadhto. Real paisa lavnyaadhi paper-trade ani backtest kara. Orders place hot nahit.")
except requests.RequestException as e:
    st.error(f"Delta API network error: {e}")
except Exception as e:
    st.error(f"Scanner error: {e}")
    st.exception(e)
