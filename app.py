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
S.headers.update({"Accept": "application/json", "User-Agent": "SamadhanDeltaScanner/4.0"})
IST = timezone(timedelta(hours=5, minutes=30))
LOG_FILE = Path(__file__).with_name("signals_log.json")

st.set_page_config(page_title="Samadhan Delta Scanner v4", page_icon="📡", layout="wide")
st.title("📡 SAMADHAN DELTA SCANNER v4 — SIGNAL ➜ MOMENTUM ➜ SMC/DMI/KST/VI ➜ OI + SAVED SIGNALS")
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
    st.subheader("Saved signals (paper-trade)")
    sl_atr = st.slider("Stop-loss = kiti ATR", 0.5, 3.0, 1.5, 0.25)
    tp_atr = st.slider("Target = kiti ATR", 1.0, 6.0, 3.0, 0.5)
    keep_hours = st.slider("Signal kiti tas 'ACTIVE' theva", 1, 48, 12, 1)
    if st.button("🗑 Saved signals clear kara"):
        st.session_state["tracked"] = {}
        save_log({})
    st.subheader("Majors (nehmi scan hotat)")
    pinned_bases = st.multiselect("Pinned coins", ["BTC", "ETH", "SOL", "DOGE", "XRP", "BNB", "ADA", "AVAX", "LINK"],
                                  default=["BTC", "ETH", "SOL", "DOGE"])
    refresh = st.selectbox("Refresh (seconds)", [30, 60, 90, 120], index=1)
    st.caption("Refresh fakt data cha bhag karto; sidebar settings ani saved signals tashich rahatat.")

CFG = {"cross_n": cross_n, "adx_min": adx_min,
       "req": {"DMI": req_dmi, "KST": req_kst, "VI": req_vi, "SMC": req_smc, "OI": req_oi}}


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
                     "funding": f(t, "funding_rate")}
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
        confirms_ok = all(conf[k] == mom_dir for k in required) if mom_dir != "WAIT" else False

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


# ───────────────────────── TRACKER (saved signals) ─────────────────────────
def update_tracker(df, live):
    T = st.session_state["tracked"]
    now = time.time()
    seen = set()
    active = {(v["symbol"], v["dir"]) for v in T.values() if v["status"] == "ACTIVE"}

    if not df.empty:
        for _, r in df[df.FINAL.isin(["UP", "DOWN"])].iterrows():
            seen.add((r.Symbol, r.FINAL))
            if (r.Symbol, r.FINAL) in active:
                continue
            entry = live.get(r.Symbol, {}).get("price") or float(r.Price)
            sign = 1 if r.FINAL == "UP" else -1
            T[f"{r.Symbol}|{r.FINAL}|{int(now)}"] = {
                "symbol": r.Symbol, "dir": r.FINAL, "t": now, "entry": entry,
                "sl": entry - sign * sl_atr * float(r.ATR), "tp": entry + sign * tp_atr * float(r.ATR),
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

        T = update_tracker(df, live)      # navin FINAL save kara, junyanchi live price/SL/TP update kara

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

        render_tracker(T)

        if not df.empty:
            st.subheader("⏳ JAVAL — Fresh/Forming ahe pan sagle confirm nahit")
            if near.empty:
                st.info("Ata kahi nahi.")
            else:
                st.dataframe(near[["Symbol", "State", "Signal Dir", "Momentum Dir", "Confirms", "DMI", "KST", "VI",
                                   "SMC", "OI", "OI Meaning", "SMC Setup", "Signal Score", "Momentum Score"]],
                             hide_index=True, use_container_width=True)

            st.subheader("📌 MAJORS — nehmi scan (Stage 1 madhe ka nahit te 'Stage1' column madhe)")
            pin_syms = [p[0] for p in pinned]
            mj = df[df.Symbol.isin(pin_syms)]
            if mj.empty:
                st.info("Majors cha candle data aala nahi. Pudhchya refresh madhe punha tapasa.")
            else:
                st.dataframe(mj[["Symbol", "Price", "Stage1", "Signal Dir", "Signal Score", "24h Change %", "State",
                                 "Momentum Dir", "Momentum Score", "DMI", "KST", "VI", "SMC", "OI Meaning",
                                 "SMC Setup", "FINAL"]], hide_index=True, use_container_width=True)

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
