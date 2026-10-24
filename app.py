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
st.title("📡 SAMADHAN DELTA OPERATOR + MOMENTUM SCANNER")
st.caption("EARLY WATCH + CONFIRMED STRONG | Public market data only | No auto-orders")

with st.sidebar:
    scan_limit = st.slider("Liquid candidates to scan", 10, 60, 30, 5)
    min_score = st.slider("Minimum confirmed score", 60, 90, 75, 5)
    min_turnover = st.number_input("Minimum 24h turnover (if available)", 0, value=10000, step=10000)
    refresh = st.selectbox("Refresh (seconds)", [30, 60, 90, 120], index=1)
    st.caption("No signal if strict filters do not agree.")

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

@st.cache_data(ttl=20, show_spinner=False)
def market_snapshot():
    products = get("/v2/products", {"contract_types":"perpetual_futures", "states":"live", "page_size":200})
    tickers = get("/v2/tickers", {"contract_types":"perpetual_futures"})
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
    candidates = []
    for sym in symbols:
        t = by_symbol.get(sym)
        if not t:
            continue
        turnover = f(t, "turnover", "turnover_usd", "volume")
        if turnover >= min_turnover:
            candidates.append((sym, t, turnover))
    candidates.sort(key=lambda x: x[2], reverse=True)
    return candidates[:scan_limit], len(symbols), len(tickers)

def candles(symbol, resolution="5m", bars=100):
    end = int(time.time())
    sec = {"1m":60, "5m":300, "15m":900}[resolution]
    rows = get("/v2/history/candles", {"resolution":resolution, "symbol":symbol,
                                       "start":end-sec*bars, "end":end-sec})
    if not isinstance(rows, list) or not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows).rename(columns={
        "o":"open","h":"high","l":"low","c":"close","v":"volume",
        "t":"time","timestamp":"time"
    })
    if not all(c in df for c in ("open","high","low","close")):
        return pd.DataFrame()
    for c in ("open","high","low","close","volume"):
        if c not in df: df[c] = 0
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df.dropna(subset=["open","high","low","close"]).sort_values("time" if "time" in df else "close").reset_index(drop=True)

def pct(now, before):
    return (now / before - 1) * 100 if before else 0.0

def analyze(symbol, ticker, threshold):
    try:
        d = candles(symbol)
        if len(d) < 40:
            return None
        # Candle endpoint ends before current time; use last returned bar.
        close, high, low, vol = d.close, d.high, d.low, d.volume.fillna(0)
        e20 = close.ewm(span=20, adjust=False).mean()
        e50 = close.ewm(span=50, adjust=False).mean()
        price = float(close.iloc[-1])
        chg3 = pct(price, float(close.iloc[-4]))
        chg6 = pct(price, float(close.iloc[-7]))
        prev_hi = float(high.iloc[-21:-1].max())
        prev_lo = float(low.iloc[-21:-1].min())
        baseline = float(vol.iloc[-21:-1].median())
        vr = float(vol.iloc[-1]) / baseline if baseline > 0 else 0
        uptrend, downtrend = e20.iloc[-1] > e50.iloc[-1], e20.iloc[-1] < e50.iloc[-1]
        upbreak, downbreak = price > prev_hi, price < prev_lo
        # Early Watch is only a proxy; it cannot prove operator identity/intent.
        early_up = sum([uptrend, chg3 > 0, vr >= 1.3, price > e20.iloc[-1]])
        early_dn = sum([downtrend, chg3 < 0, vr >= 1.3, price < e20.iloc[-1]])
        early = "UP" if early_up >= 3 and early_up > early_dn else (
            "DOWN" if early_dn >= 3 and early_dn > early_up else "WAIT")
        up = (25 if uptrend else 0) + (25 if upbreak else 0) + (20 if vr >= 1.5 and chg3 > 0 else 0) + (15 if chg6 > .15 else 0) + (10 if price > e20.iloc[-1] else 0)
        dn = (25 if downtrend else 0) + (25 if downbreak else 0) + (20 if vr >= 1.5 and chg3 < 0 else 0) + (15 if chg6 < -.15 else 0) + (10 if price < e20.iloc[-1] else 0)
        oi = f(ticker, "open_interest", "oi")
        confirmed = "UP" if up >= threshold and up > dn else ("DOWN" if dn >= threshold and dn > up else "WAIT")
        return {"Symbol":symbol,"Price":price,"Early Watch":early,"Activity Score":min(100,max(0,max(early_up,early_dn)*20+(10 if oi else 0)+(10 if vr>=1.5 else 0))),
                "Confirmed":confirmed,"Momentum Score":max(up,dn),"5m Change %":chg3,"Volume x Median":vr,
                "OI available":"Yes" if oi else "No","Reason":f"EMA20/50 {'UP' if uptrend else 'DOWN' if downtrend else 'FLAT'}; breakout {'UP' if upbreak else 'DOWN' if downbreak else 'NO'}; volume {vr:.2f}x"}
    except Exception:
        return None

st.markdown(f'<meta http-equiv="refresh" content="{refresh}">', unsafe_allow_html=True)
try:
    with st.spinner("Loading live products and tickers..."):
        candidates, n_products, n_tickers = market_snapshot()
    if not candidates:
        st.error("Live candidates मिळाले नाहीत. Delta API response/product filters तपासा.")
        st.stop()
    rows = []
    progress = st.progress(0)
    with ThreadPoolExecutor(max_workers=5) as pool:
        jobs = {pool.submit(analyze, sym, tick, min_score): sym for sym,tick,_ in candidates}
        for i, future in enumerate(as_completed(jobs), 1):
            result = future.result()
            if result: rows.append(result)
            progress.progress(i / len(jobs))
    progress.empty()
    df = pd.DataFrame(rows)
    st.caption(f"Scan time: {datetime.now(timezone.utc).strftime('%H:%M:%S UTC')} | Products: {n_products} | Tickers: {n_tickers} | Candle scans: {len(candidates)}")
    if df.empty:
        st.warning("Candle data पुरेसा नाही. पुढील refresh मध्ये पुन्हा तपासा.")
        st.stop()
    confirmed = df[df["Confirmed"].isin(["UP","DOWN"])].sort_values("Momentum Score", ascending=False).drop_duplicates("Symbol").head(2)
    early = df[df["Early Watch"].isin(["UP","DOWN"])].sort_values(["Activity Score","Momentum Score"], ascending=False).drop_duplicates("Symbol")
    early = early[~early.Symbol.isin(confirmed.Symbol)].head(2)
    a,b,c = st.columns(3)
    a.metric("Analyzed",len(df)); b.metric("Confirmed",len(df[df.Confirmed.isin(["UP","DOWN"])])); c.metric("Early watch",len(early))
    st.subheader("🏆 CONFIRMED STRONG — Top 1–2")
    if confirmed.empty:
        st.warning("Strict criteria पूर्ण करणारा setup नाही — NO TRADE.")
    else:
        for _,r in confirmed.iterrows():
            with st.container(border=True):
                x,y,z,w = st.columns(4)
                x.metric("Asset",r.Symbol); y.metric("Direction","🟢 UP" if r.Confirmed=="UP" else "🔴 DOWN")
                z.metric("Momentum Score",f'{int(r["Momentum Score"])}/100'); w.metric("Volume vs median",f'{r["Volume x Median"]:.2f}x')
                st.write(f'Price: **{r.Price}** · 5m change: **{r["5m Change %"]:.2f}%** · Activity score: **{int(r["Activity Score"])}/100**')
                st.caption(r.Reason)
    st.subheader("👀 EARLY OPERATOR WATCH")
    if early.empty: st.info("सध्या Early Watch candidate नाही.")
    else: st.dataframe(early[["Symbol","Early Watch","Activity Score","Momentum Score","5m Change %","Volume x Median","OI available","Reason"]],hide_index=True,use_container_width=True)
    with st.expander("All scanned symbols (debug)"):
        st.dataframe(df.sort_values(["Momentum Score","Activity Score"],ascending=False),hide_index=True,use_container_width=True)
    st.info("Early Watch हे volume/trend/OI availability वर आधारित proxy आहे; operator कोण आहे किंवा त्याची intent काय आहे हे सार्वजनिक data सिद्ध करत नाही. हा पहिला version आहे; OI change history आणि historical backtest जोडण्याआधी signals paper-trade करून तपासा. कोणतेही orders place होत नाहीत.")
except requests.RequestException as e:
    st.error(f"Delta API network error: {e}")
except Exception as e:
    st.error(f"Scanner error: {e}")
    st.exception(e)
