import json
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import requests
import streamlit as st

BASE = "https://api.india.delta.exchange"
S = requests.Session()
S.headers.update({"Accept": "application/json", "User-Agent": "SamadhanDeltaScanner/5.0"})
IST = timezone(timedelta(hours=5, minutes=30))
LOG_FILE = Path(__file__).with_name("signals_log.json")

st.set_page_config(page_title="Samadhan Delta Scanner v5", page_icon="📡", layout="wide")
st.title("📡 SAMADHAN DELTA SCANNER v5 — पूर्व-रडार ➜ SIGNAL ➜ MOMENTUM ➜ SMC/DMI/KST/VI ➜ SAVED SIGNALS")
st.caption("5m candles | Public data only | No auto-orders | Passed signals refresh nantar pan rahatat")


# ───────────────────────── SAVED SIGNALS (persist) ─────────────────────────
def load_log():
    try:
        return json.loads(LOG_FILE.read_text())
    except Exception:
        return {}


def save_log(d):
    try:
        LOG_FILE.write_text(json.dumps(d))
    except Exception:
        pass


if "tracked" not in st.session_state:
    st.session_state["tracked"] = load_log()

with st.sidebar:
    st.subheader("Stage 1 — Early signal")
    max_move = st.slider("Max 24h move % (aadhi move zalele kadha)", 2, 10, 5, 1)
    scan_limit = st.slider("Max signal stocks to send to Stage 2", 5, 40, 20, 5)
    min_signal = st.slider("Minimum SIGNAL score", 40, 90, 55, 5)
    min_turnover = st.number_input("Minimum 24h turnover", 0, value=10000, step=10000)
    st.subheader("Stage 2 — Fresh momentum")
    min_score = st.slider("Minimum MOMENTUM score", 50, 90, 70, 5)
    st.subheader("Stage 3 — 5m confirmations")
    cross_n = st.slider("Cross shevtchya kiti candle madhe zala pahije", 1, 6, 3, 1)
    adx_min = st.slider("DMI: ADX minimum", 10, 30, 18, 1)
    req_dmi = st.checkbox("DMI cross required (+DI / -DI)", True)
    req_kst = st.checkbox("KST cross required (KST / signal)", True)
    req_vi = st.checkbox("Vortex cross required (VI+ / VI-)", True)
    req_smc = st.checkbox("SMC required (BOS/CHoCH + sweep ya FVG)", True)
    req_oi = st.checkbox("OI direction la support karto (build-up) required", False)
    min_conf = st.slider("ticked पैकी किमान किती पुष्टी जुळाव्यात (0 = सगळ्या)", 0, 5, 0, 1,
                         help="सगळ्या पुष्टी एकत्र जुळणे दुर्मिळ आहे. 3 केल्यास ticked पैकी कोणत्याही 3 जुळल्या तरी चालेल.")
    st.subheader("Stage 4 — एकच CONFIRMED ENTRY")
    capital = st.number_input("Capital ($)", 10.0, value=1000.0, step=100.0)
    risk_pct = st.slider("प्रति trade जोखीम % (SL लागल्यास)", 0.25, 3.0, 1.0, 0.25)
    eng_n = st.slider("इंजिन: किती coins तपासायचे (turnover नुसार टॉप)", 10, 60, 30, 5)
    eng_min = st.slider("ENTRY साठी किमान Score (0-100)", 50, 90, 68, 2,
                        help="कमी = जास्त entries पण कमी दर्जा. जास्त = कमी पण कडक.")
    eng_adx = st.slider("इंजिन: किमान ADX (trend ताकद)", 12, 30, 18, 1)
    eng_min_sl = st.slider("किमान SL अंतर % (त्याखाली SL नको)", 0.3, 2.0, 0.6, 0.1,
                           help="खूप जवळचा SL spread/noise मध्येच लागतो आणि size अवाढव्य होतो.")
    eng_lev = st.slider("कमाल leverage (size मर्यादा)", 1.0, 20.0, 5.0, 1.0)
    eng_rr = st.slider("Target = किती R (SL च्या पटीत)", 1.5, 4.0, 2.0, 0.5)
    e_conf = st.slider("(जुना gate) ENTRY साठी किमान पुष्टी (5 पैकी)", 2, 5, 4, 1)
    e_vol = st.slider("किमान Volume x (median च्या)", 1.0, 3.0, 1.5, 0.1)
    e_adx = st.slider("ENTRY साठी किमान ADX", 15, 35, 22, 1)
    e_ext = st.slider("कमाल extension (ATR) - EMA20 पासून; पाठलाग नको", 0.5, 3.0, 1.25, 0.25)
    req_htf = st.checkbox("15m trend जुळला पाहिजे (EMA20/50)", True)
    req_taker = st.checkbox("Taker flow दिशेशी जुळला पाहिजे", True)
    st.subheader("Saved signals (paper-trade)")
    sl_atr = st.slider("Stop-loss = kiti ATR", 0.5, 3.0, 1.5, 0.25)
    tp_atr = st.slider("Target = kiti ATR", 1.0, 6.0, 3.0, 0.5)
    keep_hours = st.slider("Signal kiti tas 'ACTIVE' theva", 1, 48, 12, 1)
    if st.button("🗑 Saved signals clear kara"):
        st.session_state["tracked"] = {}
        save_log({})
    st.subheader("Majors (nehmi scan hotat)")
    pinned_bases = st.multiselect("Pinned coins", ["BTC", "ETH", "SOL", "DOGE", "XRP", "BNB", "ADA", "AVAX", "LINK", "BLESS"],
                                  default=["BTC", "ETH", "SOL", "DOGE"])
    st.subheader("🕵 पूर्व-रडार (शांत माल भरणे)")
    radar_window = st.slider("इतिहास खिडकी (मिनिटे)", 15, 90, 30, 5)
    oi_thr = st.slider("OI (contracts) किमान वाढ %", 1.0, 10.0, 3.0, 0.5)
    pflat = st.slider("किंमत सपाट: जास्तीत जास्त बदल %", 0.2, 1.5, 0.5, 0.1)
    radar_min = st.slider("किमान Radar score", 30, 90, 55, 5)
    crowd_thr = st.slider("Funding गर्दी पातळी (±)", 0.02, 0.10, 0.03, 0.01)
    thin_turnover = st.number_input("पातळ market: 24h turnover खाली ($)", 0, value=500000, step=100000)
    block_crowd = st.checkbox("गर्दीच्याच बाजूचा signal FINAL मधून काढा", False)
    refresh = st.selectbox("Refresh (seconds)", [30, 60, 90, 120], index=1)
    st.caption("Refresh fakt data cha bhag karto; sidebar settings ani saved signals tashich rahatat.")

CFG = {"cross_n": cross_n, "adx_min": adx_min,
       "req": {"DMI": req_dmi, "KST": req_kst, "VI": req_vi, "SMC": req_smc, "OI": req_oi},
       "min_conf": min_conf}


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


# ───────────────────────── OI MEANING ─────────────────────────
# Price direction (6h) + OI direction (6h) = kay chalu ahe
#   Price UP   + OI UP   = Long build-up   (nave buyers ale -> move mazboot)
#   Price UP   + OI DOWN = Short covering  (shorts band hot ahet -> move kamjor)
#   Price DOWN + OI UP   = Short build-up  (nave sellers ale -> move mazboot)
#   Price DOWN + OI DOWN = Long unwinding  (longs bahar padat ahet -> kamjor)
# |OI| < 0.5% kinva |price| < 0.2% = anischit (NEUTRAL)
def oi_meaning(price_chg, oi_pct):
    if abs(oi_pct) < 0.5 or abs(price_chg) < 0.2:
        return "-", "⚪ OI स्थिर / अनिश्चित"
    if price_chg > 0 and oi_pct > 0:
        return "UP", "🟢 लाँग बिल्ड-अप (नवे खरेदीदार, तेजी मजबूत)"
    if price_chg > 0 and oi_pct < 0:
        return "-", "🟡 शॉर्ट कव्हरिंग (शॉर्ट बंद होत आहेत, तेजी कमकुवत)"
    if price_chg < 0 and oi_pct > 0:
        return "DOWN", "🔴 शॉर्ट बिल्ड-अप (नवे विक्रेते, घसरण मजबूत)"
    return "-", "🟠 लाँग अनवाइंडिंग (लाँग बाहेर पडत आहेत, घसरण कमकुवत)"


# ───────────────────────── STAGE 1 : EARLY SIGNAL ─────────────────────────
# GATE: |24h change| > max_move -> baad.
# SIGNAL SCORE = 30*turnover pct + 30*min(1, max(0,OI 6h)/3%) + 25*(1-|24h|/max_move) + 15 range-edge javal
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
        direction = "WAIT"
    return {"Signal Score": round(score, 1), "Signal Dir": direction, "24h Change %": chg24,
            "OI Change 6h %": oi_pct, "Range Pos": rng_pos, "Funding": f(t, "funding_rate")}


@st.cache_data(ttl=20, show_spinner=False)
def market_snapshot(min_turnover, scan_limit, min_signal, max_move, pinned_bases=()):
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
    symbols = list(dict.fromkeys(list(by_symbol) + symbols))

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

    def status(s):
        if abs(s["24h Change %"]) > max_move:
            return f'EXTENDED (24h {s["24h Change %"]:.1f}% > {max_move}%)'
        if s["Signal Dir"] == "WAIT":
            return "Direction WAIT (24h change ani range position match nahit)"
        if s["Signal Score"] < min_signal:
            return f'Score {s["Signal Score"]:.0f} < {min_signal}'
        return "PASS"

    signals, extended, scored = [], 0, {}
    for sym, t, turnover in pool:
        s = signal_score(t, rank[sym], max_move)
        s["Turnover"] = turnover
        s["Stage1 Status"] = status(s)
        scored[sym] = (sym, t, s)
        if abs(s["24h Change %"]) > max_move:
            extended += 1
        if s["Stage1 Status"] == "PASS":
            signals.append((sym, t, s))
    signals.sort(key=lambda x: x[2]["Signal Score"], reverse=True)
    signals = signals[:scan_limit]

    pinned, missing = [], []
    for base in pinned_bases:
        names = {base + "USD", base + "USDT"}
        hit = [sym for sym in by_symbol if str(sym).upper() in names]
        if not hit:
            missing.append(base)
            continue
        sym = hit[0]
        if sym in scored:
            pinned.append(scored[sym])
        else:
            t = by_symbol[sym]
            turnover = f(t, "turnover_usd", "turnover", "volume")
            s = signal_score(t, 0.0, max_move)
            s["Turnover"] = turnover
            s["Stage1 Status"] = f"Turnover {turnover:.0f} < min {min_turnover}"
            pinned.append((sym, t, s))

    # live price / OI for saved-signal tracking (sagle tickers)
    live = {}
    for sym, t in by_symbol.items():
        oi_val, oi_chg = f(t, "oi_value_usd"), f(t, "oi_change_usd_6h")
        live[sym] = {"price": f(t, "close", "mark_price"),
                     "oi_pct": oi_chg / oi_val * 100 if oi_val > 0 else 0.0,
                     "funding": f(t, "funding_rate"), "oi_val": oi_val,
                     "turnover": f(t, "turnover_usd", "turnover", "volume")}
    return signals, len(symbols), len(tickers), len(pool), extended, pinned, missing, live


# ───────────────────────── CANDLES ─────────────────────────
def candles(symbol, resolution="5m", bars=150):
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


# ───────────────────────── INDICATORS (5m) ─────────────────────────
def true_range(d):
    pc = d.close.shift(1)
    return pd.concat([d.high - d.low, (d.high - pc).abs(), (d.low - pc).abs()], axis=1).max(axis=1)


def wilder(s, n=14):
    return s.ewm(alpha=1 / n, adjust=False).mean()


def crossed(a, b, n, direction):
    diff = a - b
    if direction == "UP":
        ev = (diff > 0) & (diff.shift(1) <= 0)
        return bool(ev.iloc[-n:].any()) and bool(diff.iloc[-1] > 0)
    ev = (diff < 0) & (diff.shift(1) >= 0)
    return bool(ev.iloc[-n:].any()) and bool(diff.iloc[-1] < 0)


def dmi_signal(d, n, adx_min):
    up_move, dn_move = d.high.diff(), -d.low.diff()
    pdm = up_move.where((up_move > dn_move) & (up_move > 0), 0.0)
    mdm = dn_move.where((dn_move > up_move) & (dn_move > 0), 0.0)
    atr = wilder(true_range(d))
    pdi, mdi = 100 * wilder(pdm) / atr, 100 * wilder(mdm) / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    adx = wilder(dx.fillna(0))
    adx_now = float(adx.iloc[-1])
    if adx_now < adx_min:
        return "-", adx_now
    if crossed(pdi, mdi, n, "UP"):
        return "UP", adx_now
    if crossed(pdi, mdi, n, "DOWN"):
        return "DOWN", adx_now
    return "-", adx_now


def kst_signal(d, n):
    c = d.close
    roc = lambda k: (c / c.shift(k) - 1) * 100
    kst = roc(10).rolling(10).mean() + 2 * roc(15).rolling(10).mean() \
        + 3 * roc(20).rolling(10).mean() + 4 * roc(30).rolling(15).mean()
    sig = kst.rolling(9).mean()
    if crossed(kst, sig, n, "UP"):
        return "UP"
    if crossed(kst, sig, n, "DOWN"):
        return "DOWN"
    return "-"


def vortex_signal(d, n, length=14):
    vmp = (d.high - d.low.shift(1)).abs()
    vmm = (d.low - d.high.shift(1)).abs()
    s_tr = true_range(d).rolling(length).sum()
    vip, vim = vmp.rolling(length).sum() / s_tr, vmm.rolling(length).sum() / s_tr
    if crossed(vip, vim, n, "UP"):
        return "UP"
    if crossed(vip, vim, n, "DOWN"):
        return "DOWN"
    return "-"


# ───────────────────────── SMC ─────────────────────────
# Swing = 3+3 pivot | BOS/CHoCH = shevtchya 3 candle madhe swing high/low break
# SWEEP = shevtchya 6 candle madhe liquidity ghetli ani parat close aat | FVG = shevtchya 10 candle madhe unfilled gap
# OK = fresh BOS/CHoCH AND (sweep OR FVG) same direction
def smc_signal(d):
    h, l, c = d.high.values, d.low.values, d.close.values
    n = len(d)
    ph = (d.high == d.high.rolling(7, center=True).max()).values
    pl = (d.low == d.low.rolling(7, center=True).min()).values
    sh_idx, sl_idx = np.where(ph)[0], np.where(pl)[0]
    if len(sh_idx) < 2 or len(sl_idx) < 2:
        return "-", 0, "no structure"
    SH, SH_prev = h[sh_idx[-1]], h[sh_idx[-2]]
    SL, SL_prev = l[sl_idx[-1]], l[sl_idx[-2]]

    bos_up = c[-4] <= SH and c[-3:].max() > SH
    bos_dn = c[-4] >= SL and c[-3:].min() < SL
    kind_up = "CHoCH" if SH < SH_prev else "BOS"
    kind_dn = "CHoCH" if SL > SL_prev else "BOS"
    sweep_up = bool(((l[-6:] < SL) & (c[-6:] > SL)).any())
    sweep_dn = bool(((h[-6:] > SH) & (c[-6:] < SH)).any())

    fvg_up = fvg_dn = False
    for i in range(n - 10, n):
        if i < 2:
            continue
        if l[i] > h[i - 2] and (i == n - 1 or l[i + 1:].min() >= h[i - 2]):
            fvg_up = True
        if h[i] < l[i - 2] and (i == n - 1 or h[i + 1:].max() <= l[i - 2]):
            fvg_dn = True

    score_up = 40 * bos_up + 30 * sweep_up + 30 * fvg_up
    score_dn = 40 * bos_dn + 30 * sweep_dn + 30 * fvg_dn
    ok_up = bos_up and (sweep_up or fvg_up)
    ok_dn = bos_dn and (sweep_dn or fvg_dn)

    def label(kind, sweep, fvg):
        return kind + (" + sweep" if sweep else "") + (" + FVG" if fvg else "")

    if ok_up and not ok_dn:
        return "UP", score_up, label(kind_up, sweep_up, fvg_up)
    if ok_dn and not ok_up:
        return "DOWN", score_dn, label(kind_dn, sweep_dn, fvg_dn)
    return "-", max(score_up, score_dn), "no SMC setup"


# ───────────────────────── STAGE 2 : FRESH MOMENTUM ─────────────────────────
def momentum(symbol, sig, threshold, cfg):
    try:
        d = candles(symbol)
        if len(d) < 90:
            return None
        close, high, low, vol = d.close, d.high, d.low, d.volume.fillna(0)
        e20 = close.ewm(span=20, adjust=False).mean()
        e50 = close.ewm(span=50, adjust=False).mean()
        price = float(close.iloc[-1])

        tr = true_range(d)
        atr_long = float(tr.iloc[-50:].mean())
        atr_before = float(tr.iloc[-23:-3].mean())
        if atr_long <= 0:
            return None
        squeeze_ratio = atr_before / atr_long
        squeeze = squeeze_ratio <= 0.85

        ref_hi, ref_lo = float(high.iloc[-24:-4].max()), float(low.iloc[-24:-4].min())
        fresh_up = float(close.iloc[-3:].max()) > ref_hi and price > ref_hi
        fresh_dn = float(close.iloc[-3:].min()) < ref_lo and price < ref_lo

        baseline = float(vol.iloc[-23:-3].median())
        vr = float(vol.iloc[-2:].max()) / baseline if baseline > 0 else 0
        chg3 = pct(price, float(close.iloc[-4]))
        chg6h = pct(price, float(close.iloc[-73]))     # 72 x 5m = 6 tas (OI 6h shi match)

        ext_e20 = abs(price - float(e20.iloc[-1])) / atr_long
        ext_30m = abs(price - float(close.iloc[-7])) / atr_long
        ext_brk = (price - ref_hi) / atr_long if fresh_up else ((ref_lo - price) / atr_long if fresh_dn else 0)
        extended = ext_e20 > 2.5 or ext_30m > 4 or ext_brk > 2

        e20_up, e20_dn = e20.iloc[-1] > e20.iloc[-4], e20.iloc[-1] < e20.iloc[-4]
        up = (30 if fresh_up else 0) + (20 if squeeze else 0) + (20 if vr >= 1.5 and chg3 > 0 else 0) \
            + (15 if price > e20.iloc[-1] and e20_up else 0) + (15 if price > e50.iloc[-1] else 0)
        dn = (30 if fresh_dn else 0) + (20 if squeeze else 0) + (20 if vr >= 1.5 and chg3 < 0 else 0) \
            + (15 if price < e20.iloc[-1] and e20_dn else 0) + (15 if price < e50.iloc[-1] else 0)

        if extended:
            state, up, dn = "EXTENDED", 0, 0
        elif fresh_up or fresh_dn:
            state = "FRESH"
        elif squeeze and ((ref_hi - price) / atr_long <= 0.5 or (price - ref_lo) / atr_long <= 0.5):
            state = "FORMING"
        else:
            state = "NONE"

        mom_dir = "UP" if up >= threshold and up > dn else ("DOWN" if dn >= threshold and dn > up else "WAIT")

        dmi_dir, adx_now = dmi_signal(d, cfg["cross_n"], cfg["adx_min"])
        kst_dir = kst_signal(d, cfg["cross_n"])
        vi_dir = vortex_signal(d, cfg["cross_n"])
        smc_dir, smc_score, smc_label = smc_signal(d)
        oi_dir, oi_text = oi_meaning(chg6h, sig["OI Change 6h %"])
        conf = {"DMI": dmi_dir, "KST": kst_dir, "VI": vi_dir, "SMC": smc_dir, "OI": oi_dir}
        required = [k for k, v in cfg["req"].items() if v]
        matched = sum(1 for k in required if conf[k] == mom_dir)
        need = len(required) if cfg.get("min_conf", 0) <= 0 else min(cfg["min_conf"], len(required))
        confirms_ok = (matched >= need) if (mom_dir != "WAIT" and required) else False

        base_ok = state == "FRESH" and mom_dir != "WAIT" and mom_dir == sig["Signal Dir"] \
            and sig.get("Stage1 Status") == "PASS"
        final = mom_dir if (base_ok and confirms_ok) else "NO"

        return {
            "Symbol": symbol, "Price": price, "State": state,
            "Stage1": sig.get("Stage1 Status", "PASS"),
            "Signal Score": sig["Signal Score"], "Signal Dir": sig["Signal Dir"],
            "24h Change %": sig["24h Change %"], "OI Change 6h %": sig["OI Change 6h %"],
            "Price 6h %": chg6h, "OI Meaning": oi_text, "Funding": sig.get("Funding", 0.0),
            "Momentum Dir": mom_dir, "Momentum Score": max(up, dn),
            "DMI": dmi_dir, "ADX": adx_now, "KST": kst_dir, "VI": vi_dir, "SMC": smc_dir, "OI": oi_dir,
            "SMC Score": smc_score, "SMC Setup": smc_label,
            "Confirms": sum(1 for k in conf if conf[k] == mom_dir and mom_dir != "WAIT"),
            "15m Change %": chg3, "Volume x Median": vr, "Squeeze (ATR ratio)": squeeze_ratio,
            "Extension (ATR)": ext_e20, "ATR": atr_long, "FINAL": final,
            "Reason": f"breakout {'UP' if fresh_up else 'DOWN' if fresh_dn else 'NO'}; squeeze {squeeze_ratio:.2f}; "
                      f"volume {vr:.2f}x; ADX {adx_now:.0f}; SMC: {smc_label}; {oi_text}",
        }
    except Exception:
        return None


def chip(val, direction):
    return "✅" if (val == direction and direction in ("UP", "DOWN")) else "❌"


# ───────────────────────── STAGE 4 : एकच CONFIRMED ENTRY ─────────────────────────
# FINAL (5m fresh breakout + indicator crosses) -> pudhe ya gates sagle pass zale tarach ENTRY.
# Sagle gates pass zalelyat jyacha EScore jast to ekach ENTRY dakhavla jato.
def htf_trend(sym):
    """15m trend: price > EMA20 > EMA50 = UP, ulat = DOWN, baki '-'. Data nasel tar None."""
    try:
        d = candles(sym, "15m", 120)
        if len(d) < 60:
            return None
        c = d.close
        e20, e50 = c.ewm(span=20, adjust=False).mean(), c.ewm(span=50, adjust=False).mean()
        p = float(c.iloc[-1])
        if p > e20.iloc[-1] > e50.iloc[-1]:
            return "UP"
        if p < e20.iloc[-1] < e50.iloc[-1]:
            return "DOWN"
        return "-"
    except Exception:
        return None


def entry_gates(r, tk, htf, turnover, P):
    """r: FINAL row (dict/Series). Return (ok, score, gates[(naav, ok, text)])."""
    d = r["FINAL"]
    up = d == "UP"
    G = []
    G.append(("पुष्टी", r["Confirms"] >= P["conf"], f'{int(r["Confirms"])}/5 (किमान {P["conf"]})'))
    G.append(("Volume", r["Volume x Median"] >= P["vol"], f'{r["Volume x Median"]:.2f}x (किमान {P["vol"]}x)'))
    G.append(("ADX", r["ADX"] >= P["adx"], f'{r["ADX"]:.0f} (किमान {P["adx"]})'))
    G.append(("Extension", r["Extension (ATR)"] <= P["ext"], f'{r["Extension (ATR)"]:.2f} ATR (कमाल {P["ext"]})'))
    fnd = r["Funding"]
    crowded = fnd >= P["crowd"] if up else fnd <= -P["crowd"]
    G.append(("Funding गर्दी नाही", not crowded, f"funding {fnd:.3f}"))
    G.append(("Turnover पातळ नाही", turnover >= P["thin"], f"${turnover:,.0f}"))
    tk_ok = bool(tk and tk.get("ok"))
    share = tk["buy_share"] if tk_ok else None
    tk_align = tk_ok and ((share >= 0.55) if up else (share <= 0.45))
    G.append(("Taker flow", tk_align if P["req_taker"] else True,
              f"खरेदी {share * 100:.0f}%" if tk_ok else "डेटा नाही"))
    htf_ok = htf == d
    G.append(("15m trend", htf_ok if P["req_htf"] else True, f"15m: {htf if htf else 'डेटा नाही'}"))
    ok = all(g[1] for g in G)
    score = (40 * min(1.0, r["Confirms"] / 5)
             + 20 * min(1.0, max(0.0, (r["Volume x Median"] - 1) / 1.5))
             + 15 * min(1.0, max(0.0, (r["ADX"] - 15) / 20))
             + 15 * max(0.0, 1 - r["Extension (ATR)"] / max(P["ext"], 0.1))
             + (10 if tk_align else 0))
    return ok, round(score, 1), G


def entry_plan(price, atr, d, sl_atr, tp_atr, capital, risk_pct):
    """SL = sl_atr*ATR, T1 = 1R, TP = tp_atr*ATR. Size = risk$ / SL%."""
    sign = 1 if d == "UP" else -1
    sl_d = sl_atr * atr
    if price <= 0 or sl_d <= 0:
        return None
    risk_usd = capital * risk_pct / 100
    notional = risk_usd / (sl_d / price)
    return {"entry": price, "sl": price - sign * sl_d, "t1": price + sign * sl_d,
            "tp": price + sign * tp_atr * atr, "rr": tp_atr / sl_atr, "risk_usd": risk_usd,
            "notional": notional, "leverage": notional / capital if capital > 0 else 0,
            "sl_pct": sl_d / price * 100}


def apply_entry(df, live):
    """df madhe ENTRY / EScore kolumn bhara. Fakt ek (sarvat changla) ENTRY thevto."""
    P = {"conf": e_conf, "vol": e_vol, "adx": e_adx, "ext": e_ext, "crowd": crowd_thr,
         "thin": thin_turnover, "req_taker": req_taker, "req_htf": req_htf}
    df["ENTRY"], df["EScore"] = "NO", 0.0
    details, passed = {}, []
    for i, r in df[df.FINAL.isin(["UP", "DOWN"])].iterrows():
        tk = taker_flow(r.Symbol)
        htf = htf_trend(r.Symbol)
        ok, sc, G = entry_gates(r, tk, htf, live.get(r.Symbol, {}).get("turnover", 0.0), P)
        df.at[i, "EScore"] = sc
        details[r.Symbol] = (ok, sc, G)
        if ok:
            passed.append((sc, i))
    if passed:
        _, best = max(passed)
        df.at[best, "ENTRY"] = df.at[best, "FINAL"]
    return df, details


# ───────────────────────── ENGINE v7 : SAMADHAN CRYPTO ENTRY SCORE ─────────────────────────
# Sagle liquid coins var chalto. Prati coin LONG ani SHORT dogh bajunche score (0-100):
#   15m trend 25 | trigger (breakout kinva EMA20 pullback-bounce) 25 | momentum (DMI/ADX/RSI) 20
#   | volume 15 | extension (EMA20 pasun antar) 10 | OI + funding sandarbh 5
# HARD BLOCK: 15m trend ulat, trigger nahi, kinva price EMA20 pasun >3 ATR dur (pathlaag nako).
def dmi_vals(d, n=14):
    up_move, dn_move = d.high.diff(), -d.low.diff()
    pdm = up_move.where((up_move > dn_move) & (up_move > 0), 0.0)
    mdm = dn_move.where((dn_move > up_move) & (dn_move > 0), 0.0)
    atr = wilder(true_range(d), n)
    pdi, mdi = 100 * wilder(pdm, n) / atr, 100 * wilder(mdm, n) / atr
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return pdi, mdi, wilder(dx.fillna(0), n), atr


def rsi_series(c, n=14):
    dlt = c.diff()
    g, l_ = wilder(dlt.clip(lower=0), n), wilder((-dlt).clip(lower=0), n)
    return 100 - 100 / (1 + g / l_.replace(0, np.nan))


def score_coin(d5, d15, oi_pct=0.0, price6h=0.0, funding=0.0, crowd=0.03, rr=2.0,
               min_atr_pct=0.12, min_sl_pct=0.6, adx_min=18.0, fee_rt=0.12, ext_max=1.5):
    """d5/d15: closed-candle DataFrames. Return best side dict kinva None."""
    if len(d5) < 60 or len(d15) < 55:
        return None
    c, h, l_, v = d5.close, d5.high, d5.low, d5.volume.fillna(0)
    e20, e50 = c.ewm(span=20, adjust=False).mean(), c.ewm(span=50, adjust=False).mean()
    pdi, mdi, adx, atr_s = dmi_vals(d5)
    rsi = rsi_series(c)
    atr = float(atr_s.iloc[-1])
    price = float(c.iloc[-1])
    if atr <= 0 or price <= 0:
        return None
    if atr / price * 100 < min_atr_pct:                    # coin ajibat halat nahi (flat / bandh market) -> SL/target arthhin
        return None
    if float((v.iloc[-30:] <= 0).mean()) > 0.2:            # volume nasalele candles jast -> illiquid / bandh
        return None
    c15 = d15.close
    t20, t50 = c15.ewm(span=20, adjust=False).mean(), c15.ewm(span=50, adjust=False).mean()
    p15 = float(c15.iloc[-1])
    base = float(v.iloc[-43:-3].median())
    if base <= 0:
        return None
    vr = min(5.0, float(v.iloc[-3:].max()) / base)          # 5x chya var mojat nahi (illiquid ratio phugvta)
    ext = abs(price - float(e20.iloc[-1])) / atr
    out = []
    for side in ("UP", "DOWN"):
        up = side == "UP"
        sg = 1 if up else -1
        if up:
            htf = 25 if (p15 > t50.iloc[-1] and t20.iloc[-1] > t50.iloc[-1]) else (12 if p15 > t50.iloc[-1] else None)
        else:
            htf = 25 if (p15 < t50.iloc[-1] and t20.iloc[-1] < t50.iloc[-1]) else (12 if p15 < t50.iloc[-1] else None)
        if htf is None or ext > ext_max or float(adx.iloc[-1]) < adx_min:
            continue
        # trigger
        trig, setup = 0, ""
        for k in (1, 2):                                      # fakt gelya 2 closed candle madhe breakout (3rd = late)
            i = len(c) - k
            lvl = float(h.iloc[i - 20:i].max()) if up else float(l_.iloc[i - 20:i].min())
            if (up and c.iloc[i] > lvl) or (not up and c.iloc[i] < lvl):
                late = ((price - lvl) if up else (lvl - price)) / atr
                if late > 0.8:                                 # breakout pasun 0.8 ATR pudhe = late, nako
                    continue
                if (up and price > lvl) or (not up and price < lvl):   # ajun tikla ahe
                    trig, setup = {1: 25, 2: 20}[k], f"20-candle breakout ({k} candle purvi)"
                    break
        if not trig:                                           # pullback bounce EMA20 var
            trend_ok = (e20.iloc[-1] > e50.iloc[-1]) if up else (e20.iloc[-1] < e50.iloc[-1])
            touch = any((l_.iloc[-k] <= e20.iloc[-k] * 1.001) if up else (h.iloc[-k] >= e20.iloc[-k] * 0.999)
                        for k in (2, 3, 4))
            back = (price > e20.iloc[-1] and c.iloc[-1] > c.iloc[-2]) if up else (price < e20.iloc[-1] and c.iloc[-1] < c.iloc[-2])
            if trend_ok and touch and back and vr >= 1.0:        # pullback var volume sath havi
                trig, setup = 22, "EMA20 pullback + bounce"
        if not trig:
            continue
        ran = ((price - float(c.iloc[-13])) if up else (float(c.iloc[-13]) - price)) / atr
        if ran > 3.5:                                          # gelya 1 tasat dishene 3.5 ATR chalala = move zalach
            continue
        mom = (8 if ((pdi.iloc[-1] > mdi.iloc[-1]) if up else (pdi.iloc[-1] < mdi.iloc[-1])) else 0) \
            + (6 if adx.iloc[-1] >= 18 else 0) + (3 if adx.iloc[-1] > adx.iloc[-4] else 0) \
            + (3 if ((50 <= rsi.iloc[-1] <= 72) if up else (28 <= rsi.iloc[-1] <= 50)) else 0)
        volp = 15 * min(1.0, max(0.0, (vr - 0.8) / 1.0))
        extp = 10 * max(0.0, 1 - ext / ext_max)
        ctx = 0
        if (up and oi_pct > 0.5 and price6h > 0) or (not up and oi_pct > 0.5 and price6h < 0):
            ctx += 5
        if (up and funding >= crowd) or (not up and funding <= -crowd):
            ctx -= 5
        tr_ = true_range(d5)
        sq = float(tr_.iloc[-13:-3].mean()) / max(float(tr_.iloc[-50:].mean()), 1e-12)
        sqz = 5 if sq <= 0.85 else 0                           # breakout adhi shanti (squeeze) = bonus
        score = min(100.0, htf + trig + mom + volp + extp + ctx + sqz)
        # SL: swing (gelya 10 candle) +/- 0.2 ATR, kamit 1 ATR, jastit jast 2.5 ATR
        sw = float(l_.iloc[-10:].min()) - 0.2 * atr if up else float(h.iloc[-10:].max()) + 0.2 * atr
        dist = min(max(abs(price - sw), 1.5 * atr), 3.0 * atr)   # SL kamit 1.5 ATR (5m noise madhe lagu naye)
        dist = max(dist, price * min_sl_pct / 100)
        sl_p = dist / price * 100
        if (rr * sl_p - fee_rt) / (sl_p + fee_rt) < 1.4:        # fee (entry+exit) dharun kharа RR 1.4 peksha kami = nako
            continue              # SL kamit min_sl_pct % (spread/noise madhe udu naye)
        if dist > 4 * atr:                                      # coin itka shant ki target gathane avghad
            continue
        out.append({"side": side, "score": round(score, 1), "setup": setup, "htf": htf, "trig": trig,
                    "mom": mom, "vr": vr, "adx": float(adx.iloc[-1]), "rsi": float(rsi.iloc[-1]),
                    "ext": ext, "atr": atr, "price": price, "sqz": sqz, "sl": price - sg * dist,
                    "t1": price + sg * dist, "tp": price + sg * rr * dist, "dist": dist})
    return max(out, key=lambda x: x["score"]) if out else None


def coil_watch(d5, d15, oi_pct=0.0, min_atr_pct=0.12):
    """Move ADHI: shanti (squeeze) + 15m trend + price range kadhachya javal, pan breakout ajun nahi. Fakt 'tayari', khatri nahi."""
    if len(d5) < 60 or len(d15) < 55:
        return None
    c, h, l_, v = d5.close, d5.high, d5.low, d5.volume.fillna(0)
    tr_ = true_range(d5)
    atr = float(wilder(tr_).iloc[-1]); price = float(c.iloc[-1])
    if atr <= 0 or price <= 0 or atr / price * 100 < min_atr_pct or float((v.iloc[-30:] <= 0).mean()) > 0.2:
        return None
    sq = float(tr_.iloc[-13:-3].mean()) / max(float(tr_.iloc[-50:].mean()), 1e-12)
    if sq > 0.8:
        return None
    c15 = d15.close
    t20, t50 = c15.ewm(span=20, adjust=False).mean(), c15.ewm(span=50, adjust=False).mean()
    p15 = float(c15.iloc[-1])
    hi, lo = float(h.iloc[-21:-1].max()), float(l_.iloc[-21:-1].min())
    if p15 > t50.iloc[-1] and t20.iloc[-1] > t50.iloc[-1] and price <= hi and (hi - price) / atr <= 0.6:
        return {"side": "UP", "level": hi, "dist": (hi - price) / atr, "sq": sq, "price": price, "oi": oi_pct}
    if p15 < t50.iloc[-1] and t20.iloc[-1] < t50.iloc[-1] and price >= lo and (price - lo) / atr <= 0.6:
        return {"side": "DOWN", "level": lo, "dist": (price - lo) / atr, "sq": sq, "price": price, "oi": oi_pct}
    return None


def engine_scan(live, pinned, n, rr):
    pool = sorted(((s, v) for s, v in live.items() if v.get("turnover", 0) >= min_turnover and v["price"] > 0),
                  key=lambda x: x[1]["turnover"], reverse=True)
    syms = list(dict.fromkeys([p for p in pinned] + [s for s, _ in pool[:n]]))

    watch = []

    def work(sym):
        try:
            d5, d15 = candles(sym, "5m", 150), candles(sym, "15m", 120)
            v = live.get(sym, {})
            r = score_coin(d5, d15, v.get("oi_pct", 0.0), 0.0, v.get("funding", 0.0), crowd_thr, rr,
                           min_atr_pct=0.12, min_sl_pct=eng_min_sl, adx_min=float(eng_adx), ext_max=float(e_ext))
            w = coil_watch(d5, d15, v.get("oi_pct", 0.0))
            if w:
                w["sym"] = sym
                watch.append(w)
            if r:
                # 6h price change (72 x 5m) OI sandarbhasathi
                r["p6h"] = pct(float(d5.close.iloc[-1]), float(d5.close.iloc[-73])) if len(d5) > 73 else 0.0
                if (r["side"] == "UP" and v.get("oi_pct", 0) > 0.5 and r["p6h"] > 0) or \
                   (r["side"] == "DOWN" and v.get("oi_pct", 0) > 0.5 and r["p6h"] < 0):
                    pass
                r["sym"] = sym
            return r
        except Exception:
            return None

    with ThreadPoolExecutor(max_workers=6) as ex:
        res = [r for r in ex.map(work, syms) if r]
    res.sort(key=lambda x: x["score"], reverse=True)
    watch.sort(key=lambda x: x["dist"])
    return res, watch, len(syms)


def eng_plan(r, capital, risk_pct, max_lev=5.0):
    risk_usd = capital * risk_pct / 100
    notional = risk_usd / (r["dist"] / r["price"])
    capped = notional > capital * max_lev
    if capped:                                   # leverage mayadet: size kami, tota pan kami
        notional = capital * max_lev
        risk_usd = notional * r["dist"] / r["price"]
    return {"capped": capped, "risk_usd": risk_usd, "notional": notional, "lev": notional / capital if capital > 0 else 0,
            "sl_pct": r["dist"] / r["price"] * 100}


# ───────────────────────── OI HISTORY + OPERATOR FOOTPRINTS ─────────────────────────
# Prati scan prati coin cha (vel, price, OI value, funding) lakshat theto (session madhe, max 3 tas).
# OI "contracts" = oi_value_usd / price  -> price badalla tari OI chi khari vadh dakhavto.
HIST_KEEP = 3 * 3600


def update_history(live):
    H = st.session_state.setdefault("hist", {})
    now = time.time()
    for sym, v in live.items():
        if v.get("turnover", 0) < min_turnover or v["price"] <= 0 or v.get("oi_val", 0) <= 0:
            continue
        dq = H.setdefault(sym, [])
        if dq and now - dq[-1][0] < 10:
            continue
        dq.append((now, v["price"], v["oi_val"], v["funding"]))
        while dq and now - dq[0][0] > HIST_KEEP:
            dq.pop(0)


def footprint(sym, window_min=None):
    """Operator chya pavlakhuna (andaaz). Janeevar kami data asel tar None."""
    window_min = window_min or radar_window
    H = st.session_state.get("hist", {}).get(sym)
    if not H or len(H) < 4:
        return None
    t_end = H[-1][0]
    pts = [p for p in H if t_end - p[0] <= window_min * 60]
    if len(pts) < 4:
        return None
    minutes = (pts[-1][0] - pts[0][0]) / 60
    if minutes < max(5.0, window_min * 0.5):
        return None
    ct = [p[2] / p[1] for p in pts]                      # OI contracts
    steps = [ct[i] - ct[i - 1] for i in range(1, len(ct))]
    persistence = sum(1 for x in steps if x > 0) / len(steps)
    oi_gain = (ct[-1] / ct[0] - 1) * 100 if ct[0] > 0 else 0.0
    prices = [p[1] for p in pts]
    price_chg = pct(prices[-1], prices[0])
    hi_p, lo_p = max(prices), min(prices)
    rng_pct = (hi_p / lo_p - 1) * 100 if lo_p > 0 else 0.0
    pos = (prices[-1] - lo_p) / (hi_p - lo_p) if hi_p > lo_p else 0.5

    adds = [(pts[i][1], steps[i - 1]) for i in range(1, len(pts)) if steps[i - 1] > 0]
    wsum = sum(w for _, w in adds)
    avg = lo = hi = None
    if wsum > 0:
        avg = sum(pr * w for pr, w in adds) / wsum
        acc = 0.0
        for pr, w in sorted(adds):
            acc += w
            if lo is None and acc >= 0.1 * wsum:
                lo = pr
            if hi is None and acc >= 0.9 * wsum:
                hi = pr
    bias = "वरचा झुकाव (कमकुवत)" if pos > 0.6 else ("खालचा झुकाव (कमकुवत)" if pos < 0.4 else "दिशा अज्ञात")
    return {"minutes": minutes, "oi_gain": oi_gain, "persistence": persistence, "price_chg": price_chg,
            "rng_pct": rng_pct, "pos": pos, "entry_avg": avg, "entry_lo": lo, "entry_hi": hi,
            "bias": bias, "funding": pts[-1][3], "price": prices[-1]}


def risk_flags(v):
    out = []
    fnd = v.get("funding", 0.0)
    if fnd >= crowd_thr:
        out.append(f"⚠ लाँगची गर्दी (funding {fnd:.3f})")
    elif fnd <= -crowd_thr:
        out.append(f"⚠ शॉर्टची गर्दी (funding {fnd:.3f})")
    tv = v.get("turnover", 0.0)
    if tv < thin_turnover:
        out.append(f"⚠ पातळ market (turnover ${tv:,.0f})")
    return out


def fp_text(sym, live):
    v = live.get(sym, {})
    fp = footprint(sym)
    parts = []
    if fp:
        parts.append(f"OI {fp['oi_gain']:+.1f}% / किंमत {fp['price_chg']:+.2f}% ({fp['minutes']:.0f} मि)")
        if fp["entry_avg"]:
            parts.append(f"अंदाजे भरणा ~{fp['entry_avg']:.6g}")
    else:
        parts.append("इतिहास कमी")
    parts += risk_flags(v)
    return " | ".join(parts)


def radar_score(fp, vr):
    oi_s = 30 * min(1.0, max(0.0, fp["oi_gain"]) / (2 * oi_thr))
    flat_s = 20 * max(0.0, 1 - abs(fp["price_chg"]) / pflat)
    pers_s = 10 * min(1.0, max(0.0, (fp["persistence"] - 0.4) / 0.4))
    af = abs(fp["funding"])
    fund_s = 15 if af <= 0.02 else (7 if af < crowd_thr else 0)
    vol_s = 20 * min(1.0, max(0.0, (vr - 1.0) / 1.0)) if vr else 0
    return round(oi_s + flat_s + pers_s + fund_s + vol_s, 1)


def radar_volume_ratio(sym):
    try:
        d = candles(sym, "5m", 80)
        if len(d) < 40:
            return None
        v = d.volume.fillna(0)
        base = float(v.iloc[-46:-6].median())
        return float(v.iloc[-6:].mean()) / base if base > 0 else None
    except Exception:
        return None


def taker_flow(sym, window_min=None):
    """Public trades madhun: kon bhav olandun (taker) ghet hota. Rakane auto-olakhto; na sapadle tar karan dakhavto."""
    window_min = window_min or radar_window
    diag = st.session_state.setdefault("taker_diag", {})
    try:
        rows = get(f"/v2/trades/{sym}")
    except Exception as ex_:
        diag[sym] = f"trades API cha call fail: {ex_}"
        return {"ok": False}
    if isinstance(rows, dict):
        rows = rows.get("trades") or rows.get("result") or []
    if not isinstance(rows, list) or not rows or not isinstance(rows[0], dict):
        diag[sym] = "trades rikame / vegla format"
        return {"ok": False}
    diag[sym] = "पहिला trade (रकाने तपासा): " + str({k: rows[0][k] for k in list(rows[0])[:10]})
    now, buy, sell, ts = time.time(), 0.0, 0.0, []
    for r in rows:
        size = f(r, "size", "quantity", "volume")
        if size <= 0:
            continue
        t = f(r, "timestamp", default=0.0)
        if t > 1e17:
            t /= 1e9
        elif t > 1e14:
            t /= 1e6
        elif t > 1e11:
            t /= 1e3
        if t and now - t > window_min * 60:
            continue
        br, sr = str(r.get("buyer_role", "")).lower(), str(r.get("seller_role", "")).lower()
        sd = str(r.get("side", "")).lower()
        if br == "taker":
            buy += size
        elif sr == "taker":
            sell += size
        elif sd == "buy":
            buy += size
        elif sd == "sell":
            sell += size
        else:
            continue
        if t:
            ts.append(t)
    tot = buy + sell
    if tot <= 0:
        diag[sym] += " | taker/side रकाना सापडला नाही किंवा खिडकीत trade नाहीत"
        return {"ok": False}
    return {"ok": True, "buy_share": buy / tot, "minutes": (now - min(ts)) / 60 if ts else None}


def absorption(price_chg, buy_share):
    if abs(price_chg) > pflat:
        return "किंमत आधीच हलली"
    if buy_share <= 0.40:
        return "🟢 शांत खरेदी शोषण (विक्री घाईची, किंमत टिकली)"
    if buy_share >= 0.60:
        return "🔴 शांत विक्री शोषण (खरेदी घाईची, किंमत अडली)"
    return "⚪ संतुलित"


def compute_radar(live, limit=8):
    """Sagle coins madhun: OI vadhat ahe + kimat sapat + funding shant (gardi nahi)."""
    cands = []
    for sym, v in live.items():
        if v.get("turnover", 0) < min_turnover:
            continue
        fp = footprint(sym)
        if not fp:
            continue
        if fp["oi_gain"] >= oi_thr and abs(fp["price_chg"]) <= pflat and abs(fp["funding"]) < crowd_thr:
            cands.append((sym, fp))
    cands.sort(key=lambda x: x[1]["oi_gain"], reverse=True)
    cands = cands[:limit]
    rows = []
    if cands:
        with ThreadPoolExecutor(max_workers=4) as ex:
            vrs = list(ex.map(lambda c: (radar_volume_ratio(c[0]), taker_flow(c[0])), cands))
        for (sym, fp), (vr, tf) in zip(cands, vrs):
            sc = radar_score(fp, vr)
            if sc < radar_min:
                continue
            flags = risk_flags(live[sym])
            rows.append({
                "Symbol": sym, "Radar Score": sc, "OI वाढ %": round(fp["oi_gain"], 2),
                "किंमत %": round(fp["price_chg"], 2), "Volume x": round(vr, 2) if vr else None,
                "Funding": fp["funding"], "OI सातत्य": f'{fp["persistence"] * 100:.0f}%',
                "अंदाजे भरणा (सरासरी)": fp["entry_avg"],
                "भरणा पट्टा": f'{fp["entry_lo"]:.6g} – {fp["entry_hi"]:.6g}' if fp["entry_lo"] else "—",
                "आत्ताची किंमत": fp["price"], "झुकाव": fp["bias"],
                "Taker खरेदी %": round(tf["buy_share"] * 100) if tf["ok"] else None,
                "शोषण": absorption(fp["price_chg"], tf["buy_share"]) if tf["ok"] else "taker डेटा नाही",
                "इशारे": "; ".join(flags) if flags else "—", "मिनिटे": round(fp["minutes"]),
            })
    rows.sort(key=lambda r: r["Radar Score"], reverse=True)
    return rows


# ───────────────────────── TRACKER (saved signals) ─────────────────────────
def update_tracker(df, live):
    T = st.session_state["tracked"]
    now = time.time()
    seen = set()
    active = {(v["symbol"], v["dir"]) for v in T.values() if v["status"] == "ACTIVE"}

    if not df.empty:
        for _, r in df[df.ENTRY.isin(["UP", "DOWN"])].iterrows():
            seen.add((r.Symbol, r.ENTRY))
            if (r.Symbol, r.ENTRY) in active:
                continue
            entry = live.get(r.Symbol, {}).get("price") or float(r.Price)
            sign = 1 if r.ENTRY == "UP" else -1
            T[f"{r.Symbol}|{r.ENTRY}|{int(now)}"] = {
                "symbol": r.Symbol, "dir": r.ENTRY, "t": now, "entry": entry,
                "sl": float(r.SL), "tp": float(r.TP),
                "sig": float(r["Signal Score"]), "mom": int(r["Momentum Score"]),
                "oi_meaning": r["OI Meaning"], "oi_pct": float(r["OI Change 6h %"]),
                "smc": r["SMC Setup"], "last": entry, "pnl": 0.0, "max_pnl": 0.0, "min_pnl": 0.0,
                "status": "ACTIVE", "valid_now": True,
            }

    for v in T.values():
        if v["status"] != "ACTIVE":
            continue
        p = live.get(v["symbol"], {}).get("price") or v["last"]
        sign = 1 if v["dir"] == "UP" else -1
        pnl = sign * (p / v["entry"] - 1) * 100
        v.update(last=p, pnl=pnl, max_pnl=max(v["max_pnl"], pnl), min_pnl=min(v["min_pnl"], pnl),
                 valid_now=(v["symbol"], v["dir"]) in seen)
        if (sign == 1 and p <= v["sl"]) or (sign == -1 and p >= v["sl"]):
            v["status"] = "SL HIT"
        elif (sign == 1 and p >= v["tp"]) or (sign == -1 and p <= v["tp"]):
            v["status"] = "TP HIT"
        elif now - v["t"] > keep_hours * 3600:
            v["status"] = "EXPIRED"
    save_log(T)
    return T


def render_tracker(T):
    st.subheader("📌 SAVED SIGNALS — refresh zalyavar pan rahatat (paper-trade tracking)")
    if not T:
        st.info("Ajun ekahi signal FINAL zala nahi. FINAL zalyavar to ithe save hoil.")
        return
    closed = [v for v in T.values() if v["status"] in ("TP HIT", "SL HIT")]
    wins = [v for v in closed if v["status"] == "TP HIT"]
    n_active = sum(1 for v in T.values() if v["status"] == "ACTIVE")
    n_exp = sum(1 for v in T.values() if v["status"] == "EXPIRED")
    breakeven = sl_atr / (sl_atr + tp_atr) * 100      # fees/slippage dhartaa nahi
    c1, c2, c3, c4, c5 = st.columns(5)
    c1.metric("ACTIVE", n_active)
    c2.metric("TP / SL", f"{len(wins)} / {len(closed) - len(wins)}")
    c3.metric("Win rate", f"{len(wins) / len(closed) * 100:.0f}%" if closed else "—")
    c4.metric("Avg P/L (closed)",
              f"{sum(v['pnl'] for v in closed) / len(closed):.2f}%" if closed else "—")
    c5.metric("Breakeven win rate", f"{breakeven:.0f}%")
    if len(closed) < 30:
        st.caption(f"फक्त {len(closed)} closed सिग्नल आहेत (EXPIRED {n_exp} मोजले नाहीत). "
                   f"30+ होईपर्यंत win rate वर विश्वास ठेवू नका.")
    now = time.time()
    rows = []
    for v in sorted(T.values(), key=lambda x: x["t"], reverse=True):
        rows.append({
            "Symbol": v["symbol"], "Dir": "🟢 UP" if v["dir"] == "UP" else "🔴 DOWN",
            "Signal time (IST)": datetime.fromtimestamp(v["t"], IST).strftime("%d-%b %H:%M:%S"),
            "Age": f'{int((now - v["t"]) / 60)} min',
            "Entry": v["entry"], "Now": v["last"], "P/L %": round(v["pnl"], 2),
            "Max P/L %": round(v["max_pnl"], 2), "SL": v["sl"], "TP": v["tp"],
            "Status": v["status"],
            "Setup ajun valid?": ("✅" if v.get("valid_now") else "⚠ kamjor") if v["status"] == "ACTIVE" else "—",
            "OI (signal veli)": v["oi_meaning"], "SMC": v["smc"], "Score S/M": f'{v["sig"]:.0f}/{v["mom"]}',
        })
    out = pd.DataFrame(rows)
    st.dataframe(out, hide_index=True, use_container_width=True)
    st.download_button("⬇ CSV download", out.to_csv(index=False), "saved_signals.csv", key=f"dl_{int(now)}")
    st.caption("SL/TP ATR var adharit ahet; P/L fakt refresh veli chya price var — dochya madhlya wick cha SL/TP chukhu shakto.")


# ───────────────────────── UI (fragment: fakt he bhag refresh hoto) ─────────────────────────
def scan_view():
    try:
        with st.spinner("Stage 1: Early signal stocks shodhat ahe..."):
            signals, n_products, n_tickers, n_pool, n_ext, pinned, missing, live = market_snapshot(
                min_turnover, scan_limit, min_signal, max_move, tuple(pinned_bases))

        update_history(live)
        st.caption(f"Last scan: {datetime.now(IST).strftime('%H:%M:%S IST')} | Products: {n_products} | "
                   f"Tickers: {n_tickers} | Turnover pass: {n_pool} | Aadhi move zalele (kadhle): {n_ext} | "
                   f"Early signal stocks: {len(signals)}")
        if missing:
            st.warning(f"Ya coins che symbols Delta tickers madhe sapadle nahit: {', '.join(missing)}")

        st.subheader("🎯 STAGE 1 — EARLY SIGNAL STOCKS")
        if signals:
            sig_df = pd.DataFrame([{"Symbol": s, **v} for s, _, v in signals])
            st.dataframe(sig_df[["Symbol", "Signal Dir", "Signal Score", "24h Change %", "OI Change 6h %",
                                 "Funding", "Turnover"]], hide_index=True, use_container_width=True)
        else:
            st.info("Stage 1 madhe ekahi early signal nahi.")

        st.subheader("🕵 पूर्व-रडार — शांत माल भरणे (breakout च्या आधी)")
        n_hist = len(st.session_state.get("hist", {}))
        try:
            radar_rows = compute_radar(live)
        except Exception as ex_:
            radar_rows = []
            st.warning(f"Radar त्रुटी: {ex_}")
        if radar_rows:
            st.dataframe(pd.DataFrame(radar_rows), hide_index=True, use_container_width=True)
            st.caption("Taker खरेदी % = भाव ओलांडून (घाईत) घेणाऱ्यांमध्ये खरेदीदारांचा वाटा. किंमत सपाट + विक्री घाईची = कोणीतरी शांतपणे घेत असू शकतो; "
                       "उलट असेल तर शांतपणे विकत असू शकतो. हा अंदाज आहे, ओळख नाही.")
            st.caption("ही यादी 'काहीतरी जमत आहे' एवढेच सांगते, दिशा नाही. Breakout (FRESH) झाल्यावरच FINAL चे नियम लागतात. "
                       "'अंदाजे भरणा' = ज्या किमतींवर OI वाढला त्यांची OI-वजनाची सरासरी; ऑपरेटरची खरी entry नाही.")
        else:
            st.info(f"आत्ता पूर्व-रडारवर कोणीही नाही. ({n_hist} coins चा इतिहास जमा; "
                    f"{radar_window} मिनिटांचा डेटा जमायला ॲप चालू ठेवा.)")

        with st.expander("🔧 Taker डेटा तपासणी (trades रकाने बरोबर वाचले का)"):
            dg = st.session_state.get("taker_diag", {})
            if dg:
                for k_, v_ in list(dg.items())[-6:]:
                    st.write(f"**{k_}:** {v_}")
            else:
                st.caption("अजून taker डेटा मागवला नाही (रडारवर coin आला की मागवला जाईल).")

        done = {s[0] for s in signals}
        todo = list(signals) + [p for p in pinned if p[0] not in done]
        rows = []
        if todo:
            progress = st.progress(0)
            with ThreadPoolExecutor(max_workers=5) as ex:
                jobs = {ex.submit(momentum, sym, sig, min_score, CFG): sym for sym, _, sig in todo}
                for i, future in enumerate(as_completed(jobs), 1):
                    r = future.result()
                    if r:
                        rows.append(r)
                    progress.progress(i / len(jobs))
            progress.empty()
        df = pd.DataFrame(rows)

        if not df.empty:
            df["Footprint"] = df.Symbol.map(lambda x: fp_text(x, live))
            if block_crowd:
                crowded = ((df.FINAL == "UP") & (df.Funding >= crowd_thr)) | \
                          ((df.FINAL == "DOWN") & (df.Funding <= -crowd_thr))
                df.loc[crowded, "FINAL"] = "NO"

        egates = {}
        with st.spinner("इंजिन: coins चे 5m + 15m candles तपासत आहे..."):
            eng, watch_l, n_eng = engine_scan(live, [p[0] for p in pinned], eng_n, eng_rr)
        best = eng[0] if eng else None
        if best and best["score"] >= eng_min:
            tk_ = taker_flow(best["sym"])
            adj = 0
            if tk_["ok"]:
                sh = tk_["buy_share"]
                adj = (5 if sh >= 0.55 else (-8 if sh <= 0.40 else 0)) if best["side"] == "UP" \
                    else (5 if sh <= 0.45 else (-8 if sh >= 0.60 else 0))
            best["score"] = min(100.0, best["score"] + adj)
            best["taker"] = f'{tk_["buy_share"] * 100:.0f}% खरेदी ({adj:+d})' if tk_["ok"] else "डेटा नाही"
        entered = bool(best and best["score"] >= eng_min)
        df_e = pd.DataFrame()
        if entered:
            pe = live.get(best["sym"], {}).get("price") or best["price"]
            df_e = pd.DataFrame([{"Symbol": best["sym"], "ENTRY": best["side"], "Price": pe, "SL": best["sl"],
                                  "TP": best["tp"], "Signal Score": best["score"], "Momentum Score": int(best["mom"]),
                                  "OI Meaning": best["setup"], "OI Change 6h %": live.get(best["sym"], {}).get("oi_pct", 0.0),
                                  "SMC Setup": best["setup"]}])
        T = update_tracker(df_e, live)      # navin ENTRY save kara, junyanchi live price/SL/TP update kara

        st.subheader("🎯 ENTRY — एकच CONFIRMED (Samadhan Crypto Score)")
        st.caption(f"{n_eng} coins तपासले (5m + 15m, फक्त closed candles). ENTRY = सर्वोच्च Score आणि ≥ {eng_min}.")
        if entered:
            r = best
            pl = eng_plan(r, capital, risk_pct, eng_lev)
            with st.container(border=True):
                st.success(f'✅ ENTRY: {"🟢 LONG" if r["side"] == "UP" else "🔴 SHORT"} — **{r["sym"]}** · Score {r["score"]:.0f}/100 · {r["setup"]}')
                m1, m2, m3, m4, m5 = st.columns(5)
                m1.metric("Entry (आत्ता)", f'{r["price"]:.6g}')
                m2.metric("Stop-loss", f'{r["sl"]:.6g}', f'-{pl["sl_pct"]:.2f}%')
                m3.metric("T1 (1R)", f'{r["t1"]:.6g}')
                m4.metric(f"Target ({eng_rr}R)", f'{r["tp"]:.6g}')
                _rw, _rk = abs(r["tp"] - r["price"]) / r["price"] * 100, pl["sl_pct"]
                st.caption(f'Target अंतर {_rw:.2f}% · SL अंतर {_rk:.2f}% · फी (येणे+जाणे ≈0.12%) धरून खरा RR 1:{(_rw - 0.12) / (_rk + 0.12):.2f}')
                m5.metric("Size (notional)", f'${pl["notional"]:,.0f}', f'{pl["lev"]:.1f}x lev')
                if pl["capped"]:
                    st.info(f'Leverage मर्यादा {eng_lev}x मुळे size कमी केला; म्हणून तोटा {risk_pct}% पेक्षा कमी आहे.')
                st.write(f'SL लागल्यास तोटा ≈ **${pl["risk_usd"]:.2f}**. T1 ला अर्धा नफा बुक करा, SL entry वर आणा.')
                st.write(f'15m trend {r["htf"]}/25 · Trigger {r["trig"]}/25 · Momentum {r["mom"]}/20 · '
                         f'Volume {r["vr"]:.2f}x · ADX {r["adx"]:.0f} · RSI {r["rsi"]:.0f} · Extension {r["ext"]:.2f} ATR · '
                         f'Taker: {r.get("taker", "—")}')
                if pl["lev"] > 10:
                    st.warning(f'⚠ Leverage {pl["lev"]:.0f}x लागेल — SL जवळ आहे. जोखीम % कमी करा.')
        else:
            st.warning("NO ENTRY — थांबा. Score ≥ किमान असलेला एकही coin नाही. खाली सर्वात जवळचे आहेत.")
        if eng:
            st.dataframe(pd.DataFrame([{
                "Coin": x["sym"], "Side": "🟢 LONG" if x["side"] == "UP" else "🔴 SHORT", "Score": x["score"],
                "Setup": x["setup"], "15m": x["htf"], "Trigger": x["trig"], "Mom": x["mom"],
                "Vol x": round(x["vr"], 2), "ADX": round(x["adx"]), "Ext ATR": round(x["ext"], 2),
                "Price": x["price"], "SL": x["sl"], "Target": x["tp"]} for x in eng[:8]]),
                hide_index=True, use_container_width=True)
        else:
            st.info("आत्ता कुठल्याच coin ला trend + trigger दोन्ही नाही.")
        st.subheader("👀 तयारी — move आधी (breakout अजून झाला नाही)")
        if watch_l:
            st.dataframe(pd.DataFrame([{
                "Coin": w["sym"], "बाजू (15m trend)": "🟢 वर" if w["side"] == "UP" else "🔴 खाली",
                "Breakout लेव्हल": w["level"], "आत्ताची किंमत": w["price"], "अंतर (ATR)": round(w["dist"], 2),
                "शांती (ATR ratio)": round(w["sq"], 2), "OI 6h %": round(w["oi"], 2)} for w in watch_l[:8]]),
                hide_index=True, use_container_width=True)
            st.caption("शांती (squeeze) + 15m trend + किंमत रेंजच्या कडेला. ही फक्त तयारी आहे, move येईलच असे नाही. "
                       "5m candle लेव्हल ओलांडून बंद झाली आणि वरचा Score ≥ किमान झाला तरच ENTRY.")
        else:
            st.caption("आत्ता कुठलाच coin शांतपणे breakout लेव्हलच्या जवळ नाही.")

        if not df.empty:
            final = df[df.FINAL.isin(["UP", "DOWN"])].sort_values(["Confirms", "Momentum Score"],
                                                                  ascending=False).head(2)
            near = df[(df.Stage1 == "PASS") & (df.State.isin(["FRESH", "FORMING"])) & (df.FINAL == "NO")] \
                .sort_values(["Confirms", "Signal Score"], ascending=False).head(6)

            a, b, c, d_ = st.columns(4)
            a.metric("Early signals", len(signals))
            b.metric("FRESH momentum", len(df[df.State == "FRESH"]))
            c.metric("FINAL ata", len(df[df.FINAL.isin(["UP", "DOWN"])]))
            d_.metric("EXTENDED (late, baad)", len(df[df.State == "EXTENDED"]))

            st.subheader("🏆 FINAL — Signal + Fresh Momentum + SMC + DMI/KST/VI cross (ata)")
            if final.empty:
                st.warning("Ata sagle confirmations jul nahit — NO NEW TRADE. (Khali saved signals paha.)")
            else:
                for _, r in final.iterrows():
                    with st.container(border=True):
                        x, y, z, w = st.columns(4)
                        x.metric("Asset", r.Symbol)
                        y.metric("Direction", "🟢 UP" if r.FINAL == "UP" else "🔴 DOWN")
                        z.metric("Signal / Momentum", f'{r["Signal Score"]:.0f} / {int(r["Momentum Score"])}')
                        w.metric("Volume vs median", f'{r["Volume x Median"]:.2f}x')
                        st.write(f'DMI {chip(r.DMI, r.FINAL)} · KST {chip(r.KST, r.FINAL)} · VI {chip(r.VI, r.FINAL)} · '
                                 f'SMC {chip(r.SMC, r.FINAL)} (**{r["SMC Setup"]}**) · OI {chip(r.OI, r.FINAL)} · '
                                 f'ADX **{r.ADX:.0f}**')
                        st.write(f'**OI:** {r["OI Meaning"]} — OI 6h **{r["OI Change 6h %"]:.2f}%**, '
                                 f'price 6h **{r["Price 6h %"]:.2f}%**, funding **{r.Funding}**')
                        st.write(f'Price: **{r.Price}** · 24h: **{r["24h Change %"]:.2f}%** · '
                                 f'Extension: **{r["Extension (ATR)"]:.1f} ATR**')
                        st.caption(r.Reason)
                        st.write(f'🕵 **पाऊलखुणा:** {r["Footprint"]}')
                        _tf = taker_flow(r.Symbol)
                        if _tf["ok"]:
                            st.write(f'⚖ **Taker खरेदी:** {_tf["buy_share"] * 100:.0f}% (उरलेले विक्री) · '
                                     f'{absorption(r["15m Change %"], _tf["buy_share"])}')
                        else:
                            st.caption("Taker डेटा मिळाला नाही (वरची 'Taker डेटा तपासणी' पहा).")
                        _fl = risk_flags(live.get(r.Symbol, {}))
                        if (r.FINAL == "UP" and r.Funding >= crowd_thr) or (r.FINAL == "DOWN" and r.Funding <= -crowd_thr):
                            st.warning("⚠ तुमच्या बाजूला गर्दी आहे (funding). उलट झटका (squeeze) शक्य; SL नक्की लावा.")
                        if any("पातळ" in x for x in _fl):
                            st.warning("⚠ पातळ market: spread/slippage जास्त, किंमत सहज हलते. Leverage कमी ठेवा.")

        if not df.empty:
            with st.expander("🔬 गाळणी फनेल — signal कुठे अडतो?", expanded=True):
                n1 = len(signals)
                fresh = df[(df.Stage1 == "PASS") & (df.State == "FRESH")]
                aligned = fresh[(fresh["Momentum Dir"] != "WAIT") & (fresh["Momentum Dir"] == fresh["Signal Dir"])]
                req_list = [k for k, on in (("DMI", req_dmi), ("KST", req_kst), ("VI", req_vi),
                                            ("SMC", req_smc), ("OI", req_oi)) if on]
                st.write(f"1️⃣ बाजारातील coins (turnover पास): **{n_pool}** → आधीच जास्त हलले (काढले): **{n_ext}**")
                st.write(f"2️⃣ Stage 1 पास (early signal): **{n1}**")
                st.write(f"3️⃣ त्यातले FRESH breakout (गेल्या 3 candle मध्ये): **{len(fresh)}**")
                st.write(f"4️⃣ Momentum दिशा = Signal दिशा: **{len(aligned)}**")
                if len(aligned):
                    cnt = {k: int((aligned[k] == aligned["Momentum Dir"]).sum()) for k in req_list}
                    st.write("5️⃣ या " + str(len(aligned)) + " पैकी प्रत्येक पुष्टी किती coins मध्ये जुळली: " +
                             " · ".join(f"**{k}** {v}" for k, v in cnt.items()))
                st.write(f"🏁 FINAL: **{len(df[df.FINAL.isin(['UP', 'DOWN'])])}**")
                worst = None
                if n1 == 0:
                    worst = "Stage 1 मध्येच सगळे गळतात. 'Max 24h move' वाढवा (7) किंवा 'Minimum SIGNAL score' कमी करा (45)."
                elif len(fresh) == 0:
                    worst = "Stage 1 पास coins मध्ये कोणाचाही breakout ताजा नाही (किंवा EXTENDED). बाजार शांत असेल तर हे सामान्य आहे."
                elif len(aligned) == 0:
                    worst = "Breakout आहे, पण Stage 1 आणि Momentum च्या दिशा विरुद्ध आहेत."
                elif len(df[df.FINAL.isin(["UP", "DOWN"])]) == 0:
                    worst = ("Breakout आणि दिशा जुळतात, पण पुष्टी (DMI/KST/VI/SMC) सगळ्या एकत्र जुळत नाहीत. "
                             "Sidebar मध्ये 'किमान पुष्टी' 3 करून पहा, किंवा सर्वात कमी जुळणारी पुष्टी बंद करा.")
                if worst:
                    st.info("👉 अडथळा: " + worst)
                near1 = aligned[aligned.Confirms >= max(1, len(req_list) - 1)].sort_values("Confirms", ascending=False)
                if len(near1) and df[df.FINAL.isin(["UP", "DOWN"])].empty:
                    st.write("**अगदी जवळचे (FINAL साठी एक पुष्टी कमी):**")
                    st.dataframe(near1[["Symbol", "Momentum Dir", "Confirms", "DMI", "KST", "VI", "SMC", "OI"]],
                                 hide_index=True, use_container_width=True)

        render_tracker(T)

        if not df.empty:
            st.subheader("⏳ JAVAL — Fresh/Forming ahe pan sagle confirm nahit")
            if near.empty:
                st.info("Ata kahi nahi.")
            else:
                st.dataframe(near[["Symbol", "State", "Signal Dir", "Momentum Dir", "Confirms", "DMI", "KST", "VI",
                                   "SMC", "OI", "OI Meaning", "SMC Setup", "Funding", "Footprint", "Signal Score",
                                   "Momentum Score"]],
                             hide_index=True, use_container_width=True)

            st.subheader("📌 MAJORS — nehmi scan (Stage 1 madhe ka nahit te 'Stage1' column madhe)")
            pin_syms = [p[0] for p in pinned]
            mj = df[df.Symbol.isin(pin_syms)]
            if mj.empty:
                st.info("Majors cha candle data aala nahi. Pudhchya refresh madhe punha tapasa.")
            else:
                st.dataframe(mj[["Symbol", "Price", "Stage1", "Signal Dir", "Signal Score", "24h Change %", "State",
                                 "Momentum Dir", "Momentum Score", "DMI", "KST", "VI", "SMC", "OI Meaning",
                                 "SMC Setup", "Funding", "Footprint", "FINAL"]], hide_index=True, use_container_width=True)

            with st.expander("All analyzed symbols (debug)"):
                st.dataframe(df.sort_values(["FINAL", "Confirms", "Momentum Score"], ascending=False),
                             hide_index=True, use_container_width=True)

        with st.expander("ℹ OI कसा वाचायचा"):
            st.markdown(
                "**OI (Open Interest)** म्हणजे बाजारात सध्या उघडे असलेले एकूण सौदे. OI वाढला = नवीन पैसा येत आहे; OI कमी झाला = सौदे बंद होत आहेत.\n\n"
                "- 🟢 **किंमत ↑ + OI ↑ = लाँग बिल्ड-अप:** नवीन खरेदीदार आले आहेत, तेजी मजबूत\n"
                "- 🟡 **किंमत ↑ + OI ↓ = शॉर्ट कव्हरिंग:** शॉर्ट विक्रेते सौदे बंद करत आहेत, नवीन खरेदीदार नाहीत, तेजी कमकुवत\n"
                "- 🔴 **किंमत ↓ + OI ↑ = शॉर्ट बिल्ड-अप:** नवीन विक्रेते आले आहेत, घसरण मजबूत\n"
                "- 🟠 **किंमत ↓ + OI ↓ = लाँग अनवाइंडिंग:** लाँग घेतलेले लोक बाहेर पडत आहेत, नवीन विक्रेते नाहीत, घसरण कमकुवत\n"
                "- ⚪ OI 0.5% पेक्षा कमी किंवा किंमत 0.2% पेक्षा कमी बदलली = अनिश्चित, स्पष्ट संकेत नाही\n\n"
                "किंमत आणि OI दोन्हींचा गेल्या 6 तासांचा बदल वापरला आहे.")

        st.info("SMC ani indicator crosses rule-based approximations ahet; future predict hot nahi. "
                "Real paisa lavnyaadhi paper-trade ani backtest kara. Orders place hot nahit.")
    except requests.RequestException as e:
        st.error(f"Delta API network error: {e}")
    except Exception as e:
        st.error(f"Scanner error: {e}")
        st.exception(e)


if hasattr(st, "fragment"):
    st.fragment(run_every=refresh)(scan_view)()      # Streamlit >= 1.37: page reload nahi, session tikto
else:
    scan_view()                                      # junya version sathi fallback
    time.sleep(refresh)
    st.rerun()
