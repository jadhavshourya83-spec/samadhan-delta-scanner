"""
SAMADHAN TERMINAL: ek file, ek nirnay (ENTER / WAIT). Order aapoaap jat nahi.

 A) CHART ENTRY: 5m DMI / Vortex / KST cha TAAJA cross + ADX/EMA/RSI/extension filter
 B) OPTION CHAIN OI-FLOW: dinbhar memory, kimaan OI, kharya velechi window, 2-minute confirm (fixed V38)
 C) Option premium var Entry / SL / T1 / T2 ani lots (jokhim % nusar)
 D) Paper journal + win rate (file madhe sathavle jate)

Chalvnyaadhi:  pip install -r requirements.txt
Secrets (.streamlit/secrets.toml kinva Streamlit Cloud 'Secrets'):
    DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN, TELEGRAM_TOKEN (optional), TELEGRAM_CHAT_ID (optional)
Chalva:  streamlit run samadhan_terminal.py
(Pahilya bhagat streamlit import nahi, mhanun test_terminal.py ne chachni karta yete.)
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import datetime, time as dtime

import numpy as np
import pandas as pd


# ═════════════════════════════ PART 1: PURE LOGIC (streamlit nahi) ═════════════════════════════
@dataclass
class Cfg:
    cross_n: int = 3
    min_crosses: int = 2
    adx_min: float = 20.0
    rsi_up: tuple = (52.0, 75.0)
    rsi_dn: tuple = (25.0, 48.0)
    ext_atr: float = 2.0
    move_atr: float = 1.5
    use_volume: bool = True
    vol_min: float = 1.2
    sl_atr: float = 1.0
    max_loss_pct: float = 30.0
    rr1: float = 1.5
    rr2: float = 3.0
    min_delta: float = 0.35
    max_delta: float = 0.75
    min_oi: float = 50000
    max_spread_pct: float = 3.0
    window_start: str = "09:30"
    window_end: str = "15:00"
    cooldown_min: int = 15
    max_trade_minutes: int = 120
    # OI flow
    flow_window_min: float = 5.0
    flow_min_score: float = 35.0
    flow_steps: int = 6
    flow_confirm_min: float = 2.0
    flow_min_ltp: float = 5.0
    # CAS v2 ("aadhi-oLakh" radar): OI jama hot ahe + premium dabat ahe + spot ajun halla nahi
    cas_long_min: float = 10.0       # mothi window (minute)
    cas_short_min: float = 3.0       # lahan window: veg vadhla ka
    cas_oi_full: float = 4.0         # OI % jyala purn gun
    cas_prem_full: float = 2.0       # premium ghat % jyala purn gun
    cas_early: float = 50.0
    cas_strong: float = 70.0
    cas_late_pct: float = 0.40       # spot itka halla tar 'LATE' (aadhich hallay)
    cas_max_hedge_steps: int = 8
    cas_hedge_min_oi: float = 20000
    cas_hedge_max_spread: float = 10.0
    cas_min_credit_pct: float = 12.0   # credit / spread width


# ───────── indicators ─────────
def true_range(d):
    pc = d["close"].shift(1)
    return pd.concat([d["high"] - d["low"], (d["high"] - pc).abs(), (d["low"] - pc).abs()], axis=1).max(axis=1)


def wilder(s, n=14):
    return s.ewm(alpha=1 / n, adjust=False).mean()


def ema(s, n):
    return s.ewm(span=n, adjust=False).mean()


def atr(d, n=14):
    return wilder(true_range(d), n)


def rsi(close, n=14):
    diff = close.diff()
    wu, wd = wilder(diff.clip(lower=0), n), wilder((-diff).clip(lower=0), n)
    out = 100 - 100 / (1 + wu / wd.replace(0, np.nan))
    out = out.where(wd != 0, 100.0)
    return out.where(~((wu == 0) & (wd == 0)), 50.0)


def dmi(d, n=14):
    up, dn = d["high"].diff(), -d["low"].diff()
    pdm = up.where((up > dn) & (up > 0), 0.0)
    mdm = dn.where((dn > up) & (dn > 0), 0.0)
    a = wilder(true_range(d), n)
    pdi, mdi = 100 * wilder(pdm, n) / a, 100 * wilder(mdm, n) / a
    dx = 100 * (pdi - mdi).abs() / (pdi + mdi).replace(0, np.nan)
    return pdi, mdi, wilder(dx.fillna(0), n)


def vortex(d, n=14):
    vmp = (d["high"] - d["low"].shift(1)).abs()
    vmm = (d["low"] - d["high"].shift(1)).abs()
    s_tr = true_range(d).rolling(n).sum()
    return vmp.rolling(n).sum() / s_tr, vmm.rolling(n).sum() / s_tr


def kst(close):
    roc = lambda k: (close / close.shift(k) - 1) * 100
    line = roc(10).rolling(10).mean() + 2 * roc(15).rolling(10).mean() \
        + 3 * roc(20).rolling(10).mean() + 4 * roc(30).rolling(15).mean()
    return line, line.rolling(9).mean()


def bars_since_cross(a, b, direction, lookback):
    """0 = shevtchi candle. None = taaja cross nahi (kinva aata ulti bajula)."""
    diff = a - b
    if len(diff) < 2:
        return None
    last = diff.iloc[-1]
    if not ((last > 0) if direction == "UP" else (last < 0)):
        return None
    for i in range(lookback):
        idx = len(diff) - 1 - i
        if idx < 1:
            break
        cur, prev = diff.iloc[idx], diff.iloc[idx - 1]
        if direction == "UP" and cur > 0 and prev <= 0:
            return i
        if direction == "DOWN" and cur < 0 and prev >= 0:
            return i
    return None


# ───────── candles ─────────
def _timestamps(df):
    if isinstance(df.index, pd.DatetimeIndex):
        ts = pd.Series(df.index, index=df.index)
    else:
        col = next((c for c in ("timestamp", "time", "datetime", "date") if c in df.columns), None)
        if col is None:
            return None
        ts = pd.to_datetime(df[col], errors="coerce")
    if ts.isna().all():
        return None
    if getattr(ts.dt, "tz", None) is not None:
        ts = ts.dt.tz_convert("Asia/Kolkata").dt.tz_localize(None)
    return ts


def drop_forming_candle(df, tf_min, now):
    """Ajun purna na zalelya shevtchya candle la kadha ('repaint' tala). now = IST naive."""
    if df is None or len(df) == 0:
        return df, "डेटा नाही"
    ts = _timestamps(df)
    if ts is None:
        return df, "⚠ timestamp रकाना सापडला नाही; शेवटची candle पूर्ण आहे की नाही ठरवता आले नाही"
    last_open = ts.iloc[-1]
    if now < last_open + pd.Timedelta(minutes=tf_min):
        return df.iloc[:-1], f"चालू (अपूर्ण) {last_open:%H:%M} ची candle वगळली"
    return df, f"शेवटची पूर्ण candle {last_open:%H:%M}"


def candle_key(df):
    ts = _timestamps(df)
    return str(ts.iloc[-1]) if ts is not None else f"n{len(df)}"


# ───────── chart signal ─────────
def _empty(msg):
    return {"direction": "WAIT", "leaning": "-", "checks": [(msg, False, "")], "bars": {}, "close": 0.0,
            "atr": 0.0, "adx": 0.0, "rsi": 50.0, "vol_ratio": None, "candle": ""}


def evaluate_signal(d, cfg):
    if d is None or len(d) < 60:
        return _empty(f"डेटा अपुरा (किमान 60 candle हव्यात, आहेत {0 if d is None else len(d)})")
    close = d["close"].astype(float)
    pdi, mdi, adx = dmi(d)
    vip, vim = vortex(d)
    k, ks = kst(close)
    e9, e20 = ema(close, 9), ema(close, 20)
    r, a = rsi(close), atr(d)
    now_close, now_atr = float(close.iloc[-1]), float(a.iloc[-1])
    if not (now_atr > 0):
        return _empty("ATR शून्य")
    vol_ratio = None
    if "volume" in d.columns:
        v = pd.to_numeric(d["volume"], errors="coerce").fillna(0)
        base = float(v.iloc[-21:-1].median())
        if base > 0:
            vol_ratio = float(v.iloc[-1]) / base
    pairs = {"DMI": (pdi, mdi), "VI": (vip, vim), "KST": (k, ks)}
    results = {}
    for direction in ("UP", "DOWN"):
        sgn = 1 if direction == "UP" else -1
        bars = {n: bars_since_cross(x, y, direction, cfg.cross_n) for n, (x, y) in pairs.items()}
        fresh = {n: b for n, b in bars.items() if b is not None}
        aligned = all((float(x.iloc[-1]) > float(y.iloc[-1])) == (direction == "UP") for x, y in pairs.values())
        rsi_lo, rsi_hi = cfg.rsi_up if direction == "UP" else cfg.rsi_dn
        trend_ok = (now_close > e9.iloc[-1] > e20.iloc[-1]) if direction == "UP" else (now_close < e9.iloc[-1] < e20.iloc[-1])
        ext = (now_close - float(e20.iloc[-1])) * sgn / now_atr
        ref_bar = max(fresh.values()) if fresh else 0
        move = (now_close - float(close.iloc[-1 - ref_bar])) * sgn / now_atr
        checks = [
            (f"ताजे cross ≥ {cfg.min_crosses} (गेल्या {cfg.cross_n} candle)", len(fresh) >= cfg.min_crosses,
             ", ".join(f"{n}:{b} candle आधी" for n, b in fresh.items()) or "एकही ताजा cross नाही"),
            ("DMI, VI, KST तिन्ही एकाच बाजूला", aligned, ""),
            (f"ADX ≥ {cfg.adx_min:.0f}", float(adx.iloc[-1]) >= cfg.adx_min, f"{float(adx.iloc[-1]):.1f}"),
            ("किंमत/EMA9/EMA20 दिशेनुसार", bool(trend_ok), f"EMA9 {float(e9.iloc[-1]):.1f}, EMA20 {float(e20.iloc[-1]):.1f}"),
            (f"RSI {rsi_lo:.0f}–{rsi_hi:.0f}", rsi_lo <= float(r.iloc[-1]) <= rsi_hi, f"{float(r.iloc[-1]):.1f}"),
            (f"EMA20 पासून ≤ {cfg.ext_atr} ATR (ताणलेले नाही)", ext <= cfg.ext_atr, f"{ext:.2f} ATR"),
            (f"cross नंतरची हालचाल ≤ {cfg.move_atr} ATR (उशीर नाही)", move <= cfg.move_atr, f"{move:.2f} ATR"),
        ]
        if cfg.use_volume:
            if vol_ratio is None:
                checks.append(("Volume (index ला volume नसतो → वगळले)", True, "n/a"))
            else:
                checks.append((f"Volume ≥ {cfg.vol_min}x", vol_ratio >= cfg.vol_min, f"{vol_ratio:.2f}x"))
        results[direction] = {"checks": checks, "passed": sum(1 for c in checks if c[1]), "bars": bars}
    up, dn = results["UP"], results["DOWN"]
    leaning = "UP" if up["passed"] >= dn["passed"] else "DOWN"
    best = results[leaning]
    ok = all(c[1] for c in best["checks"])
    other_ok = all(c[1] for c in results["DOWN" if leaning == "UP" else "UP"]["checks"])
    direction = leaning if (ok and not other_ok) else "WAIT"
    return {"direction": direction, "leaning": leaning, "checks": best["checks"], "bars": best["bars"],
            "close": now_close, "atr": now_atr, "adx": float(adx.iloc[-1]), "rsi": float(r.iloc[-1]),
            "vol_ratio": vol_ratio, "candle": candle_key(d)}


# ───────── time / expiry ─────────
def in_trade_window(now, start, end):
    if now.weekday() >= 5:
        return False
    h1, m1 = map(int, start.split(":"))
    h2, m2 = map(int, end.split(":"))
    return dtime(h1, m1) <= now.time() <= dtime(h2, m2)


_DATE = re.compile(r"^\d{4}-\d{2}-\d{2}")


def extract_dates(obj):
    out = []
    if isinstance(obj, str):
        if _DATE.match(obj):
            out.append(obj[:10])
    elif isinstance(obj, dict):
        for v in obj.values():
            out += extract_dates(v)
    elif isinstance(obj, (list, tuple)):
        for v in obj:
            out += extract_dates(v)
    return sorted(set(out))


# ───────── option chain ─────────
def _f(x, default=0.0):
    try:
        v = float(x)
        return v if math.isfinite(v) else default
    except (TypeError, ValueError):
        return default


def strike_map(oc):
    out = {}
    for k, v in (oc or {}).items():
        try:
            out[float(k)] = v
        except (TypeError, ValueError):
            pass
    return out


def chain_ltp(oc, strike, side):
    node = ((strike_map(oc).get(float(strike)) or {}).get(side) or {})
    v = _f(node.get("last_price"))
    return v if v > 0 else None


def pick_option(oc, spot, direction, step, cfg, itm_steps=0, strike_override=None):
    side = "ce" if direction == "UP" else "pe"
    smap = strike_map(oc)
    if not smap:
        return {"ok": False, "issues": ["option chain रिकामी"], "side": side}
    atm = round(spot / step) * step
    target = atm - itm_steps * step if side == "ce" else atm + itm_steps * step
    strike = float(strike_override) if strike_override is not None else min(smap, key=lambda s: abs(s - target))
    if strike not in smap:
        return {"ok": False, "issues": [f"strike {strike:.0f} chain madhe nahi"], "side": side}
    node = (smap[strike] or {}).get(side) or {}
    ltp, bid, ask = _f(node.get("last_price")), _f(node.get("top_bid_price")), _f(node.get("top_ask_price"))
    oi, vol = _f(node.get("oi")), _f(node.get("volume"))
    delta = abs(_f((node.get("greeks") or {}).get("delta")))
    spread = ((ask - bid) / ((ask + bid) / 2) * 100) if (bid > 0 and ask > 0) else None
    issues = []
    if ltp <= 0:
        issues.append("LTP शून्य (व्यवहार नाही)")
    if oi < cfg.min_oi:
        issues.append(f"OI कमी ({oi:,.0f} < {cfg.min_oi:,.0f})")
    if spread is not None and spread > cfg.max_spread_pct:
        issues.append(f"bid-ask spread जास्त ({spread:.1f}%)")
    if delta and not (cfg.min_delta <= delta <= cfg.max_delta):
        issues.append(f"delta {delta:.2f} पट्ट्याबाहेर")
    return {"ok": not issues, "issues": issues, "side": side, "strike": strike, "ltp": ltp, "bid": bid, "ask": ask,
            "oi": oi, "volume": vol, "delta": delta, "spread_pct": spread}


# ───────── trade plan ─────────
def _tick(x):
    return round(round(x / 0.05) * 0.05, 2)


def trade_plan(opt, atr_u, cfg, lot_size, capital, risk_pct):
    entry = opt["ask"] if opt.get("ask", 0) > 0 else opt["ltp"]
    delta = opt["delta"] if opt.get("delta", 0) > 0 else 0.5
    sl = _tick(max(entry - delta * cfg.sl_atr * atr_u, entry * (1 - cfg.max_loss_pct / 100), 0.05))
    risk = entry - sl
    if risk <= 0:
        return {"ok": False, "note": "SL entry पेक्षा खाली ठरवता आला नाही"}
    t1, t2 = _tick(entry + cfg.rr1 * risk), _tick(entry + cfg.rr2 * risk)
    risk_amt = capital * risk_pct / 100
    lots = int(risk_amt // (risk * lot_size))
    plan = {"ok": lots >= 1, "entry": _tick(entry), "sl": sl, "t1": t1, "t2": t2, "risk_pts": round(risk, 2),
            "lots": lots, "qty": lots * lot_size, "max_loss": round(risk * lot_size * lots, 0),
            "cost": round(entry * lot_size * lots, 0), "risk_budget": round(risk_amt, 0),
            "sl_pct": round(risk / entry * 100, 1), "note": ""}
    if lots < 1:
        plan["note"] = (f"1 lot चा धोका ₹{risk * lot_size:,.0f} आहे, तुमच्या जोखीम मर्यादेपेक्षा (₹{risk_amt:,.0f}) जास्त. "
                        f"भांडवल/जोखीम % वाढवा किंवा हा ट्रेड सोडा.")
    return plan


# ───────── paper tracker ─────────
OPEN_STATES = ("ACTIVE", "T1 HIT")


def new_trade(sym, direction, expiry, opt, plan, now_ts, candle, flow="none"):
    return {"sym": sym, "dir": direction, "expiry": expiry, "strike": opt["strike"], "side": opt["side"],
            "t": now_ts, "candle": candle, "entry": plan["entry"], "sl": plan["sl"], "sl0": plan["sl"],
            "t1": plan["t1"], "t2": plan["t2"], "lots": plan["lots"], "qty": plan["qty"], "last": plan["entry"],
            "max": plan["entry"], "min": plan["entry"], "t1_done": False, "status": "ACTIVE", "exit": None,
            "flow": flow}


def update_trade(t, ltp, now_ts, eod, max_minutes):
    if t["status"] not in OPEN_STATES or not ltp or ltp <= 0:
        return t
    t["last"], t["max"], t["min"] = ltp, max(t["max"], ltp), min(t["min"], ltp)
    if not t["t1_done"] and ltp >= t["t1"]:
        t["t1_done"], t["status"] = True, "T1 HIT"
        t["sl"] = max(t["sl"], t["entry"])
    if ltp >= t["t2"]:
        t["status"], t["exit"] = "T2 HIT", ltp
    elif ltp <= t["sl"]:
        t["status"], t["exit"] = ("BE EXIT" if t["t1_done"] else "SL HIT"), ltp
    elif eod or (now_ts - t["t"]) / 60 > max_minutes:
        t["status"], t["exit"] = "EXPIRED", ltp
    return t


def summarize(trades):
    """Jinkla = kimaan T1 gathla. Haralela = SL aadhich lagla. T1 shivay EXPIRED = nirnay nahi."""
    closed = [t for t in trades if t["status"] in ("T2 HIT", "SL HIT", "BE EXIT", "EXPIRED")]
    wins = [t for t in closed if t["t1_done"]]
    losses = [t for t in closed if t["status"] == "SL HIT"]
    decided = len(wins) + len(losses)
    return {"closed": len(closed), "wins": len(wins), "losses": len(losses),
            "win_rate": (len(wins) / decided * 100) if decided else None,
            "open": sum(1 for t in trades if t["status"] in OPEN_STATES),
            "undecided": sum(1 for t in closed if not t["t1_done"] and t["status"] != "SL HIT")}


def cooldown_ok(trades, sym, direction, now_ts, minutes, candle):
    for t in trades:
        if t["sym"] != sym or t["dir"] != direction:
            continue
        if t["candle"] == candle or now_ts - t["t"] < minutes * 60:
            return False
    return True


def fmt_alert(sym, direction, expiry, opt, plan, sig, flow_text=""):
    kind = "CE (CALL)" if direction == "UP" else "PE (PUT)"
    return (f"🚩 {sym} ENTRY सिग्नल: {kind}\n"
            f"📅 Expiry {expiry} | Strike {opt['strike']:.0f}\n"
            f"💰 Entry (limit) ₹{plan['entry']} | 🛑 SL ₹{plan['sl']} | 🎯 T1 ₹{plan['t1']} | 🎯 T2 ₹{plan['t2']}\n"
            f"📦 {plan['lots']} lot ({plan['qty']} qty) | कमाल तोटा ₹{plan['max_loss']:.0f}\n"
            f"📊 ADX {sig['adx']:.0f} | RSI {sig['rsi']:.0f} | OI {opt['oi']:,.0f}\n"
            + (f"🌊 OI flow: {flow_text}\n" if flow_text else "")
            + "⚠ T1 नंतर SL entry वर आणा. हा सल्ला नाही.")


# ───────── OI FLOW (dinbhar memory, kimaan OI, kharya velechi window) ─────────
FLOW_KEEP_SEC = 3 * 3600


def flow_key(sym, expiry, strike, side):
    return f"{sym}|{expiry}|{float(strike):.0f}|{side}"


def flow_update(store, key, ts, ltp, oi, vol, iv=0.0, spot=0.0, delta=0.0):
    h = store.setdefault(key, [])
    if h and ts - h[-1][0] < 5:
        return
    h.append((ts, ltp, oi, vol, iv, spot, delta))
    while h and ts - h[0][0] > FLOW_KEEP_SEC:
        h.pop(0)


def est_delta(side, strike, spot, step):
    """Dhan ne delta dila nasel tar andaje delta (logistic, ATM=0.5, 2 strike OTM ~0.27). CE +, PE -."""
    x = (strike - spot) / (2.0 * step)
    d_ce = 1.0 / (1.0 + math.exp(max(min(x, 30), -30)))
    return d_ce if side == "ce" else d_ce - 1.0


def flow_ingest(store, sym, expiry, oc, spot, cfg, step, ts):
    sh = store.setdefault(f"__spot__{sym}", [])
    if spot > 0 and (not sh or ts - sh[-1][0] >= 5):
        sh.append((ts, spot))
        while sh and ts - sh[0][0] > FLOW_KEEP_SEC:
            sh.pop(0)
    for strike, node in strike_map(oc).items():
        if abs(strike - spot) > cfg.flow_steps * step:
            continue
        for side in ("ce", "pe"):
            o = (node or {}).get(side) or {}
            ltp = _f(o.get("last_price"))
            if ltp > 0:
                dl = abs(_f((o.get("greeks") or {}).get("delta")))
                sdl = (dl if side == "ce" else -dl) if dl > 0 else est_delta(side, strike, spot, step)
                if dl <= 0:
                    store["__delta_est__"] = store.get("__delta_est__", 0) + 1
                flow_update(store, flow_key(sym, expiry, strike, side), ts, ltp, _f(o.get("oi")),
                            _f(o.get("volume")), _f(o.get("implied_volatility")), spot, sdl)


def _spot_at(spot_hist, ts):
    """ts chya veli (kinva tyachya aadhicha) spot; nasel tar None."""
    best = None
    for t, v in spot_hist or []:
        if t <= ts + 5:
            best = v
        else:
            break
    return best


def flow_metrics(h, window_min=5.0, min_hist_min=3.0, spot_hist=None):
    """Kharya velechi window (pratyek 30s nahi). History kami asel tar None."""
    if not h or len(h) < 3:
        return None
    t_now, ltp, oi, vol, iv = h[-1][:5]
    sp_now, dl_now = (h[-1][5], h[-1][6]) if len(h[-1]) >= 7 else (0.0, 0.0)
    if sp_now <= 0:
        sp_now = _spot_at(spot_hist, t_now) or 0.0
    target = t_now - window_min * 60
    ref = None
    for s in h:
        if s[0] <= target:
            ref = s
        else:
            break
    if ref is None:
        if (t_now - h[0][0]) / 60 < min_hist_min:
            return None
        ref = h[0]
    span_s = t_now - ref[0]
    if span_s < 60:
        return None
    oi_pct = (oi - ref[2]) / ref[2] * 100 if ref[2] > 0 else 0.0
    prem_raw = (ltp - ref[1]) / ref[1] * 100 if ref[1] > 0 else 0.0
    # SPOT-NEUTRAL premium: spot halla tar premium delta mule apoaap halto (spot khali = PE vadhto). Te vegle kara.
    prem_pct, adj = prem_raw, False
    ref_spot = ref[5] if len(ref) >= 7 and ref[5] > 0 else (_spot_at(spot_hist, ref[0]) or 0.0)
    ref_dl = ref[6] if len(ref) >= 7 and ref[6] != 0 else dl_now
    if ref_spot > 0 and sp_now > 0 and ref_dl != 0 and dl_now != 0 and ref[1] > 0:
        exp_chg = (ref_dl + dl_now) / 2 * (sp_now - ref_spot)
        prem_pct, adj = (ltp - ref[1] - exp_chg) / ref[1] * 100, True
    iv_chg = (iv - ref[4]) / ref[4] * 100 if (ref[4] > 0 and iv > 0) else 0.0
    new_vol = max(vol - ref[3], 0.0)
    vol_ratio = None
    earlier_s = ref[0] - h[0][0]
    if earlier_s >= span_s:                     # kimaan ek khidki itka purvaitihas
        base = max(ref[3] - h[0][3], 0.0) / earlier_s * span_s
        if base > 0:
            vol_ratio = new_vol / base
    return {"oi_pct": oi_pct, "prem_pct": prem_pct, "prem_raw": prem_raw, "adj": adj, "iv_chg": iv_chg, "new_vol": new_vol,
            "vol_ratio": vol_ratio, "span_min": span_s / 60}


def flow_score(side, strike, spot, oi_pct, prem_pct, vol_ratio, iv_chg, prem_raw=None, adj=False):
    """CE/PE + OI + premium cha 4 prakar. Kamal hallchal nasel tar 'noise' (score 0).
    Spot vagalun kelela premium (adj) sathi noise-patti 1.0% (delta-matra andaja, chuka shakya).
    Kaccha ani spot-vagalun premium chya dishaa ulat asun spot-vagalun aakda chhota (<3%) asel tar saṅket aspasht -> score 0."""
    if prem_raw is not None and prem_raw * prem_pct < 0 and abs(prem_pct) < 3.0:
        return {"direction": "UP", "score": 0.0, "up": 0.0, "down": 0.0,
                "reasons": [f"premium cha sanket aspasht: kaccha {prem_raw:+.1f}% pan spot vagalun {prem_pct:+.1f}% (spot ne halavla)"]}
    prem_floor = 1.0 if adj else 0.5
    up = down = 0.0
    r_up, r_dn = [], []
    oi_s, pr_s = min(abs(oi_pct), 20), min(abs(prem_pct), 20)
    if abs(oi_pct) >= 0.5 and abs(prem_pct) >= prem_floor:
        long_build = oi_pct > 0 and prem_pct > 0       # nave buyers
        short_build = oi_pct > 0 and prem_pct < 0      # nave writers
        covering = oi_pct < 0 and prem_pct > 0         # writers baher
        unwind = oi_pct < 0 and prem_pct < 0           # buyers baher
        txt = f"{side} OI {oi_pct:+.1f}% / premium {prem_pct:+.1f}%"
        if side == "CE":
            if long_build:
                up += min(oi_s * 1.4, 20) + min(pr_s * 1.4, 20); r_up.append(txt + " (CE long build-up)")
            elif short_build:
                down += min(oi_s * 1.4, 20) + min(pr_s * 1.4, 20); r_dn.append(txt + " (CE writing)")
            elif covering:
                up += min(oi_s * 1.2, 15) + min(pr_s * 1.6, 20); r_up.append(txt + " (CE short covering)")
            elif unwind:
                down += min(oi_s, 12) + min(pr_s * 1.3, 18); r_dn.append(txt + " (CE long unwinding)")
        else:
            if long_build:
                down += min(oi_s * 1.4, 20) + min(pr_s * 1.4, 20); r_dn.append(txt + " (PE long build-up)")
            elif short_build:
                up += min(oi_s * 1.4, 20) + min(pr_s * 1.4, 20); r_up.append(txt + " (PE writing)")
            elif covering:
                down += min(oi_s * 1.2, 15) + min(pr_s * 1.6, 20); r_dn.append(txt + " (PE short covering)")
            elif unwind:
                up += min(oi_s, 12) + min(pr_s * 1.3, 18); r_up.append(txt + " (PE long unwinding)")
    up, down = up * 2.5, down * 2.5            # 5 minitachya chhotya hallchali sathi (OI 5% + prem 5% ~ 35 gun)
    if vol_ratio is not None and vol_ratio >= 1.5 and up != down:
        bonus = min((vol_ratio - 1.0) * 5, 25)
        if up > down:
            up += bonus; r_up.append(f"ताजा volume {vol_ratio:.1f}x")
        else:
            down += bonus; r_dn.append(f"ताजा volume {vol_ratio:.1f}x")
    if abs(iv_chg) >= 2 and up != down:
        bonus = min(abs(iv_chg), 10)
        if up > down:
            up += bonus; r_up.append(f"IV {iv_chg:+.1f}%")
        else:
            down += bonus; r_dn.append(f"IV {iv_chg:+.1f}%")
    dist = abs(strike - spot) / max(spot, 1) * 100          # dur gelele strikes la kami wajan (junya code madhe tya ordering chuklyachi hoti)
    mult = 0.70 if dist > 3 else (0.85 if dist > 1.5 else (1.05 if dist <= 0.5 else 1.0))
    up, down = min(up * mult, 100), min(down * mult, 100)
    direction = "UP" if up >= down else "DOWN"
    return {"direction": direction, "score": round(max(up, down), 1), "up": round(up, 1), "down": round(down, 1),
            "reasons": r_up if direction == "UP" else r_dn}


def flow_rank(store, sym, expiry, oc, spot, cfg, step):
    out = []
    for strike, node in strike_map(oc).items():
        if abs(strike - spot) > cfg.flow_steps * step:
            continue
        for side in ("ce", "pe"):
            o = (node or {}).get(side) or {}
            ltp, oi = _f(o.get("last_price")), _f(o.get("oi"))
            if ltp < cfg.flow_min_ltp or oi < cfg.min_oi:        # ashakya/pathal strikes baad
                continue
            m = flow_metrics(store.get(flow_key(sym, expiry, strike, side)), cfg.flow_window_min,
                             spot_hist=store.get(f"__spot__{sym}"))
            if not m:
                continue
            sc = flow_score(side.upper(), strike, spot, m["oi_pct"], m["prem_pct"], m["vol_ratio"], m["iv_chg"],
                            prem_raw=m.get("prem_raw"), adj=m.get("adj", False))
            if sc["score"] >= cfg.flow_min_score:
                out.append({"strike": strike, "side": side, "ltp": ltp, "oi": oi, **m, **sc})
    out.sort(key=lambda x: x["score"], reverse=True)
    return out


def flow_confirmed(top_hist, key, now_ts, minutes):
    """Shevtchya seriyatla tohi top candidate kimaan 'minutes' minute satat hota ka."""
    run_start, n = None, 0
    for ts, k in reversed(top_hist):
        if k != key:
            break
        run_start, n = ts, n + 1
    return n >= 2 and run_start is not None and (now_ts - run_start) >= minutes * 60


def flow_relation(flow_dir, direction):
    if not flow_dir:
        return "none"
    return "agree" if flow_dir == direction else "oppose"


# ───────── CAS v2: 'move chya aadhi' writers cha radar + sarvottam hedge ─────────
def spot_move_pct(store, window_min, sym=""):
    """Spot ne shevtchya window madhe kiti % halla (+ var / - khali). Itihas kami asel tar None."""
    sh = store.get(f"__spot__{sym}") or []
    if len(sh) < 3:
        return None
    t_now, sp = sh[-1]
    ref = None
    for t, v in sh:
        if t <= t_now - window_min * 60:
            ref = (t, v)
        else:
            break
    if ref is None:
        if (t_now - sh[0][0]) < 120:
            return None
        ref = sh[0]
    mv = (sp - ref[1]) / ref[1] * 100
    return mv if abs(mv) <= 5 else None          # 5% peksha jast = data chukicha (index badalla), vaparu naka


def cas_detect(store, sym, expiry, oc, spot, cfg, step):
    """Option WRITERS jama hot ahet ka, SPOT halnyachya aadhi?
    CE writing = BEARISH (CE strike spot chya vari/javal), PE writing = BULLISH (PE strike spot chya khali/javal).
    Score 0-100:  OI vadh (35) + vegacha vadh/acceleration (15) + premium dabat (20) + volume (15) + spot ajun halla nahi (15).
    Stage: EARLY >= cas_early, STRONG >= cas_strong, LATE = spot aadhich cas_late_pct halla (aata ushir)."""
    moved_dn = spot_move_pct(store, cfg.cas_long_min, sym)         # spot ne kiti halla (signed)
    out = []
    for strike, node in strike_map(oc).items():
        if abs(strike - spot) > cfg.flow_steps * step:
            continue
        for side in ("ce", "pe"):
            if side == "ce" and strike < spot - 0.25 * step:    # ITM nako: sell strike ATM/OTM ch (breakeven spot chya pudhe hava)
                continue
            if side == "pe" and strike > spot + 0.25 * step:
                continue
            o = (node or {}).get(side) or {}
            ltp, oi = _f(o.get("last_price")), _f(o.get("oi"))
            if ltp < cfg.flow_min_ltp or oi < cfg.min_oi:
                continue
            h = store.get(flow_key(sym, expiry, strike, side))
            sh_ = store.get(f"__spot__{sym}")
            ml = flow_metrics(h, cfg.cas_long_min, spot_hist=sh_)
            ms = flow_metrics(h, cfg.cas_short_min, min_hist_min=2.0, spot_hist=sh_)
            m = ml or ms
            if not m or m["oi_pct"] < 0.8 or m["prem_pct"] > 0.3:       # writing chi mul-gharat: OI vadhla, premium vadhla nahi
                continue
            bias = "BEARISH" if side == "ce" else "BULLISH"
            reasons = []
            oi_pts = 35 * min(1.0, m["oi_pct"] / cfg.cas_oi_full)
            reasons.append(f"OI {m['oi_pct']:+.1f}% ({m['span_min']:.0f}मि)")
            acc_pts = 0.0
            if ml and ms and ml["span_min"] > 0 and ms["span_min"] > 0:
                r_long, r_short = ml["oi_pct"] / ml["span_min"], ms["oi_pct"] / ms["span_min"]
                if r_short > 0 and r_short > r_long * 1.2:
                    acc_pts = 15 * min(1.0, r_short / max(r_long, 1e-9) / 3)
                    reasons.append(f"वेग वाढतोय ({ms['oi_pct']:+.1f}% / {ms['span_min']:.0f}मि)")
            pr_pts = 20 * min(1.0, max(-m["prem_pct"], 0) / cfg.cas_prem_full)
            reasons.append(f"premium {m['prem_pct']:+.1f}%")
            vr = m["vol_ratio"]
            vo_pts = 15 * min(1.0, max(vr - 1.0, 0)) if vr is not None else 0.0
            reasons.append(f"volume {vr:.1f}x" if vr is not None else "volume इतिहास नाही")
            exp = (-moved_dn if bias == "BEARISH" else moved_dn) if moved_dn is not None else None   # apekshit dishene kiti halla
            if exp is None:
                sp_pts, moved = 7.0, None
                reasons.append("spot इतिहास नाही")
            else:
                moved = exp
                sp_pts = 15.0 if exp <= 0.15 else max(0.0, 15.0 * (1 - (exp - 0.15) / max(cfg.cas_late_pct - 0.15, 1e-9)))
                reasons.append(f"spot अजून {exp:+.2f}% हललाय")
            score = round(oi_pts + acc_pts + pr_pts + vo_pts + sp_pts, 1)
            late = moved is not None and moved >= cfg.cas_late_pct
            stage = "LATE" if late else ("STRONG" if score >= cfg.cas_strong else ("EARLY" if score >= cfg.cas_early else None))
            if stage:
                out.append({"spot": spot, "strike": strike, "side": side, "bias": bias, "stage": stage, "score": score, "ltp": ltp,
                            "oi": oi, "oi_pct": m["oi_pct"], "prem_pct": m["prem_pct"], "vol_ratio": vr,
                            "span_min": m["span_min"], "moved": moved, "reasons": reasons})
    out.sort(key=lambda x: ({"STRONG": 0, "EARLY": 1, "LATE": 2}[x["stage"]], -x["score"]))
    return out


def cas_spread(c, oc, step, lot_size, capital, risk_pct, cfg):
    """Sell = CAS strike. Hedge = 1..cas_max_hedge_steps strike pudhe pasun SARVOTTAM (purn chain madhun):
    liquid (OI, spread), credit vastav (sell bid - hedge ask), jokhim-limit madhe >=1 lot, aani reward/risk saglyat changla.
    Margin gruhit dharla nahi."""
    side, sell = c["side"], float(c["strike"])
    smap = strike_map(oc)
    sn = (smap.get(sell) or {}).get(side) or {}
    s_bid = _f(sn.get("top_bid_price"))
    s_px = s_bid if s_bid > 0 else _f(sn.get("last_price"))
    if s_px <= 0:
        return {"ok": False, "issues": [f"{sell:.0f} {side.upper()} cha bhav nahi"], "alts": []}
    budget = capital * risk_pct / 100
    cands, why = [], {}

    def no(msg):
        why[msg] = why.get(msg, 0) + 1

    for k in range(1, cfg.cas_max_hedge_steps + 1):
        hs = sell + k * step if side == "ce" else sell - k * step
        hn = (smap.get(hs) or {}).get(side)
        if not hn:
            no("strike chain madhe nahi"); continue
        h_ask, h_bid, h_ltp, h_oi = _f(hn.get("top_ask_price")), _f(hn.get("top_bid_price")), _f(hn.get("last_price")), _f(hn.get("oi"))
        h_px = h_ask if h_ask > 0 else h_ltp
        if h_px <= 0:
            no("bhav nahi"); continue
        if h_oi < cfg.cas_hedge_min_oi:
            no("hedge OI kami"); continue
        if h_ask > 0 and h_bid > 0 and (h_ask - h_bid) / ((h_ask + h_bid) / 2) * 100 > cfg.cas_hedge_max_spread:
            no("hedge spread jast"); continue
        width, credit = k * step, s_px - h_px
        if credit <= 0:
            no("credit shunya/rinatmak"); continue
        if credit / width * 100 < cfg.cas_min_credit_pct:
            no("credit kami"); continue
        loss1 = (width - credit) * lot_size
        lots = int(budget // loss1) if loss1 > 0 else 0
        if lots < 1:
            no("jokhim limit peksha jast tota"); continue
        cands.append({"hedge": hs, "hedge_px": h_px, "hedge_oi": h_oi, "width": width, "credit": round(credit, 2),
                      "rr": credit / (width - credit), "lots": lots, "max_loss": round(loss1 * lots, 0),
                      "max_profit": round(credit * lot_size * lots, 0)})
    if not cands:
        return {"ok": False, "issues": [f"{m} ({n} strike)" for m, n in why.items()] or ["hedge milala nahi"], "alts": []}
    cands.sort(key=lambda x: (-x["rr"], -x["width"]))
    best = cands[0]
    be = sell + best["credit"] if side == "ce" else sell - best["credit"]
    sp0 = c.get("spot")
    if sp0 and ((side == "ce" and be <= sp0) or (side == "pe" and be >= sp0)):
        return {"ok": False, "alts": [], "issues": [f"breakeven {be:.0f} spot ({sp0:.0f}) chya aatach: spread aatach tot-sthitit"]}
    return {"ok": True, "issues": [], "side": side, "sell": sell, "sell_px": s_px, "alts": cands[:4],
            "hedge": best["hedge"], "hedge_px": best["hedge_px"], "credit": best["credit"], "width": best["width"],
            "lots": best["lots"], "max_profit": best["max_profit"], "max_loss": best["max_loss"],
            "rr": round(best["rr"], 2), "breakeven": sell + best["credit"] if side == "ce" else sell - best["credit"],
            "name": "BEAR CALL CREDIT SPREAD" if side == "ce" else "BULL PUT CREDIT SPREAD"}

# ───────── V40.2 engine (v40_engine.py) cha signal -> tumcha option plan ─────────
def v40_plan(sig, oc, atr_u, cfg, lot_size, capital, risk_pct, step):
    """sig = st.session_state.v40_last_signal ({'type':'CE'|'PE','strike':..}). V40 swasta OTM (delta 0.1-0.3) options ghet asalyane
    delta-pattyachi mayada ruNdavli jate. Strike V40 ch, SL/T1/T2/lots tumchya jokhim niyamanusar."""
    from dataclasses import replace
    c2 = replace(cfg, min_delta=0.05, max_delta=0.80)
    d = "UP" if sig["type"] == "CE" else "DOWN"
    opt = pick_option(oc, 0.0, d, step, c2, strike_override=float(sig["strike"]))
    if not opt.get("ok"):
        return {"ok": False, "opt": opt, "note": "option batla: " + "; ".join(opt.get("issues", []))}
    plan = trade_plan(opt, atr_u, cfg, lot_size, capital, risk_pct)
    return {"ok": plan["ok"], "opt": opt, "plan": plan, "note": plan.get("note", "")}


# ═════════════════════════════ PART 2: STREAMLIT UI ═════════════════════════════
INDEX_MAP = {"NIFTY": dict(id=13, step=50, lot=65), "BANKNIFTY": dict(id=25, step=100, lot=30),
             "SENSEX": dict(id=51, step=100, lot=20)}


def main():
    import json
    import os
    import time
    from datetime import timedelta, timezone
    from pathlib import Path

    import requests
    import streamlit as st

    IST = timezone(timedelta(hours=5, minutes=30))
    JOURNAL = Path(__file__).with_name("entry_journal.json")

    @st.cache_resource(show_spinner=False)
    def get_v40_code(path):
        return compile(Path(path).read_text(encoding="utf-8"), path, "exec")

    st.set_page_config(page_title="Samadhan Terminal", page_icon="🚩", layout="wide")
    st.title("🚩 समाधान टर्मिनल")
    st.caption("एकच निर्णय: ENTER / WAIT · Entry-SL-Target option premium वर · कोणतीही order आपोआप जात नाही")

    def secret(name):
        try:
            v = st.secrets.get(name)
            if v:
                return str(v)
        except Exception:
            pass
        return os.environ.get(name)

    with st.sidebar:
        sym = st.selectbox("Index", list(INDEX_MAP), index=0)
        meta = INDEX_MAP[sym]
        st.subheader("💰 भांडवल व जोखीम")
        capital = st.number_input("भांडवल (₹)", 10000, 100000000, 100000, 10000)
        risk_pct = st.slider("एका ट्रेडमध्ये जोखीम %", 0.25, 3.0, 1.0, 0.25)
        lot_size = st.number_input(f"{sym} lot size (broker वर तपासा)", 1, 5000, meta["lot"], 1)
        strike_mode = st.selectbox("Strike", ["ATM", "1 ITM (जास्त delta, जास्त premium)"], 0)
        st.subheader("⏱ वेळ")
        w_start = st.text_input("ट्रेड सुरू (HH:MM)", "09:30")
        w_end = st.text_input("ट्रेड बंद (HH:MM)", "15:00")
        cooldown = st.slider("त्याच दिशेचा पुढचा सिग्नल किमान (मिनिटे)", 5, 60, 15, 5)
        st.subheader("🌊 OI flow")
        flow_gate = st.checkbox("OI flow विरोधात असेल तर थांबा", False)
        with st.expander("🟡 CAS (writers radar)"):
            cas_early = st.slider("EARLY score किमान", 30, 80, 50, 5)
            cas_strong = st.slider("STRONG score किमान", 50, 95, 70, 5)
            cas_late = st.slider("spot इतका हलला तर 'LATE' %", 0.1, 1.0, 0.40, 0.05)
        run_v40_on = st.checkbox("V40.2 OI इंजिन (तुमचा कोड) चालवा", True)
        with st.expander("⚙ प्रगत फिल्टर"):
            min_cross = st.slider("ताजे cross किमान (DMI/VI/KST पैकी)", 1, 3, 2)
            cross_n = st.slider("cross किती candle पर्यंत 'ताजा'", 1, 6, 3)
            adx_min = st.slider("ADX किमान", 10, 30, 20)
            sl_atr = st.slider("SL = index ATR चे गुणक", 0.5, 2.5, 1.0, 0.25)
            max_loss = st.slider("premium वर कमाल SL %", 10, 50, 30, 5)
            min_oi = st.number_input("किमान OI (contracts)", 0, 100000000, 50000, 10000)
            max_spread = st.slider("कमाल bid-ask spread %", 0.5, 10.0, 3.0, 0.5)
            use_volume = st.checkbox("Volume फिल्टर (index ला volume नसल्यास वगळला जातो)", True)
            flow_window = st.slider("OI flow window (मिनिटे)", 3, 15, 5, 1)
            flow_min = st.slider("OI flow किमान score", 20, 80, 35, 5)
        refresh = st.selectbox("Refresh (सेकंद)", [15, 30, 60], index=1)
        if st.button("🗑 Journal रिकामे करा"):
            st.session_state["journal"] = []
            JOURNAL.write_text("[]")

    cfg = Cfg(cross_n=cross_n, min_crosses=min_cross, adx_min=float(adx_min), sl_atr=sl_atr,
              max_loss_pct=float(max_loss), min_oi=float(min_oi), max_spread_pct=max_spread, use_volume=use_volume,
              window_start=w_start, window_end=w_end, cooldown_min=cooldown, flow_window_min=float(flow_window),
              flow_min_score=float(flow_min), cas_early=float(cas_early), cas_strong=float(cas_strong), cas_late_pct=float(cas_late))

    if "journal" not in st.session_state:
        try:
            st.session_state["journal"] = json.loads(JOURNAL.read_text())
        except Exception:
            st.session_state["journal"] = []
    st.session_state.setdefault("flow_store", {})
    st.session_state.setdefault("flow_top", [])

    @st.cache_resource(show_spinner=False)
    def get_clients(client_id, token):
        from dhanhq import DhanContext, dhanhq
        from Dhan_Tradehull import Tradehull
        return Tradehull(client_id, token, mode="access_token"), dhanhq(DhanContext(client_id, token))

    def cached(key, ttl, fn):
        now = time.time()
        hit = st.session_state.get(key)
        if hit and now - hit[0] < ttl:
            return hit[1]
        val = fn()
        st.session_state[key] = (now, val)
        return val

    def fetch_chain(d_api, sec_id, expiry):
        resp = d_api.option_chain(under_security_id=sec_id, under_exchange_segment="IDX_I", expiry=expiry)
        data = (resp or {}).get("data", {}).get("data") if isinstance(resp, dict) else None
        if not data or "oc" not in data:
            raise RuntimeError(f"option chain मिळाली नाही: {str(resp)[:200]}")
        return {"spot": float(data.get("last_price", 0)), "oc": data["oc"]}

    def send_telegram(text):
        tok, chat = secret("TELEGRAM_TOKEN"), secret("TELEGRAM_CHAT_ID")
        if not tok or not chat:
            return False
        try:
            r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage",
                              json={"chat_id": chat, "text": text}, timeout=5)
            return r.status_code == 200
        except Exception:
            return False

    def save_journal():
        try:
            JOURNAL.write_text(json.dumps(st.session_state["journal"]))
        except Exception:
            pass

    def view():
        now = datetime.now(IST).replace(tzinfo=None)
        now_ts = time.time()
        client_id, token = secret("DHAN_CLIENT_ID"), secret("DHAN_ACCESS_TOKEN")
        if not client_id or not token:
            st.error("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN सापडले नाहीत. Secrets मध्ये ठेवा (कोडमध्ये कधीही लिहू नका).")
            return
        try:
            tsl, d_api = get_clients(client_id, token)
        except Exception as e:
            st.error(f"Dhan कनेक्शन त्रुटी: {e}")
            return

        # 1) Expiry (dynamic)
        try:
            exp_list = cached(f"exp_{sym}", 600, lambda: extract_dates(
                d_api.expiry_list(under_security_id=meta["id"], under_exchange_segment="IDX_I")))
        except Exception:
            exp_list = []
        today = now.strftime("%Y-%m-%d")
        exp_list = [e for e in exp_list if e >= today]
        if exp_list:
            expiry = st.selectbox("Expiry", exp_list, 0, key=f"expiry_{sym}")
        else:
            expiry = st.text_input("Expiry (YYYY-MM-DD): यादी मिळाली नाही, हाताने लिहा", key=f"expiry_txt_{sym}")
            if not expiry:
                return
        if expiry == today:
            st.warning("⚠ आज एक्स्पायरी: theta/gamma खूप जास्त, premium क्षणात शून्याकडे जाऊ शकतो. पुढचा expiry निवडण्याचा विचार करा.")

        # 2) Chain + OI flow memory (chart cha data ala nasla tari flow memory chalu rahte)
        try:
            chain = cached(f"c_{sym}_{expiry}", 8, lambda: fetch_chain(d_api, meta["id"], expiry))
        except Exception as e:
            st.error(f"Option chain त्रुटी: {e}")
            return
        spot, oc = chain["spot"], chain["oc"]
        if st.session_state.get("flow_day") != today:               # navin divas = junya memory chi kachra nako
            st.session_state["flow_store"], st.session_state["flow_top"] = {}, []
            st.session_state["flow_day"] = today
        flow_ingest(st.session_state["flow_store"], sym, expiry, oc, spot, cfg, meta["step"], now_ts)
        flows = flow_rank(st.session_state["flow_store"], sym, expiry, oc, spot, cfg, meta["step"])
        flow_dir, flow_ok, flow_best = None, False, (flows[0] if flows else None)
        if flow_best:
            top_key = (flow_best["strike"], flow_best["side"], flow_best["direction"])
            st.session_state["flow_top"].append((now_ts, top_key))
            flow_ok = flow_confirmed(st.session_state["flow_top"], top_key, now_ts, cfg.flow_confirm_min)
            flow_dir = flow_best["direction"] if flow_ok else None
        st.session_state["flow_top"] = [x for x in st.session_state["flow_top"] if now_ts - x[0] < 600]

        # 3) Candles (purna candle fakt)
        try:
            raw = cached(f"h_{sym}", 20, lambda: tsl.get_historical_data(sym, "INDEX", "5"))
        except Exception as e:
            st.error(f"Candle डेटा त्रुटी: {e}")
            return
        df, note = drop_forming_candle(raw, 5, now)
        sig = evaluate_signal(df, cfg)
        if sig["close"] and spot and abs(spot / sig["close"] - 1) > 0.015:
            st.error(f"⚠ Chain चा spot ({spot:,.0f}) आणि चार्टचा भाव ({sig['close']:,.0f}) जुळत नाहीत. "
                     f"Security id ({meta['id']}) {sym} चा आहे का तपासा. निर्णय थांबवला.")
            return

        # 4) Nirnay
        reasons, opt, plan = [], None, None
        direction = sig["direction"]
        rel = flow_relation(flow_dir, direction) if direction in ("UP", "DOWN") else "none"
        if not in_trade_window(now, cfg.window_start, cfg.window_end):
            reasons.append(f"ट्रेड वेळेबाहेर ({cfg.window_start}–{cfg.window_end}, सोम–शुक्र)")
        if direction == "WAIT":
            reasons.append("सर्व अटी एकत्र जुळल्या नाहीत (खाली चेकलिस्ट पहा)")
        else:
            opt = pick_option(oc, spot, direction, meta["step"], cfg, itm_steps=1 if strike_mode.startswith("1") else 0)
            if not opt["ok"]:
                reasons += [f"Option: {i}" for i in opt["issues"]]
            else:
                plan = trade_plan(opt, sig["atr"], cfg, int(lot_size), float(capital), float(risk_pct))
                if not plan["ok"]:
                    reasons.append(plan["note"] or "योजना बनवता आली नाही")
            if not cooldown_ok(st.session_state["journal"], sym, direction, now_ts, cfg.cooldown_min, sig["candle"]):
                reasons.append("हाच सिग्नल/दिशा नुकतीच नोंदली आहे (cooldown); दुबार entry नको")
            if flow_gate and rel == "oppose":
                reasons.append("OI flow उलट दिशा दाखवतो (सिद्ध झालेला, 2 मिनिटे)")
        flow_txt = {"agree": "✅ जुळतो", "oppose": "⚠ विरोधात", "none": "— पुष्टी नाही"}[rel]

        enter = direction in ("UP", "DOWN") and not reasons and plan is not None
        if enter:
            st.session_state["journal"].append(new_trade(sym, direction, expiry, opt, plan, now_ts, sig["candle"], rel))
            ok = send_telegram(fmt_alert(sym, direction, expiry, opt, plan, sig, flow_txt))
            st.toast("Telegram: " + ("पाठवला" if ok else "पाठवला नाही (secrets तपासा)"))

        eod = now.time() >= dtime(15, 20)
        for t in st.session_state["journal"]:
            if t["sym"] == sym and t["expiry"] == expiry and t["status"] in OPEN_STATES:
                update_trade(t, chain_ltp(oc, t["strike"], t["side"]), now_ts, eod, cfg.max_trade_minutes)
        save_journal()

        # ───────── UI ─────────
        st.caption(f"{now:%d-%b %H:%M:%S} IST · Spot {spot:,.1f} · {note} · ATR {sig['atr']:.1f} · "
                   f"ADX {sig['adx']:.0f} · RSI {sig['rsi']:.0f}")
        if enter:
            kind, color = ("CALL (CE) खरेदी", "#0b5d1e") if direction == "UP" else ("PUT (PE) खरेदी", "#7a1020")
            st.markdown(
                f"<div style='background:{color};padding:22px;border-radius:16px;border:3px solid white;color:white'>"
                f"<h2 style='margin:0'>🚀 ENTER: {sym} {opt['strike']:.0f} {kind}</h2>"
                f"<p style='font-size:18px;margin:8px 0 0 0'>Expiry {expiry} · Limit buy <b>₹{plan['entry']}</b> (ask) · "
                f"🛑 SL <b>₹{plan['sl']}</b> ({plan['sl_pct']}%) · 🎯 T1 <b>₹{plan['t1']}</b> · 🎯 T2 <b>₹{plan['t2']}</b><br>"
                f"📦 <b>{plan['lots']} lot</b> ({plan['qty']} qty) · कमाल तोटा <b>₹{plan['max_loss']:.0f}</b> · "
                f"गुंतवणूक ₹{plan['cost']:.0f}<br>🌊 OI flow: {flow_txt}<br>"
                f"T1 लागला की SL entry वर आणा. 15:20 नंतर बाहेर पडा.</p></div>", unsafe_allow_html=True)
        else:
            lean = {"UP": "वरचा", "DOWN": "खालचा", "-": "—"}[sig["leaning"]]
            st.markdown(
                "<div style='background:#2b2b2b;padding:22px;border-radius:16px;border:2px dashed #888;color:white'>"
                f"<h2 style='margin:0'>⏳ WAIT: entry नाही</h2><p style='margin:8px 0 0 0'>कल: {lean} · "
                + " · ".join(reasons[:4]) + "</p></div>", unsafe_allow_html=True)

        c1, c2 = st.columns(2)
        with c1:
            st.subheader("✅ चेकलिस्ट (झुकाव: " + {"UP": "वर", "DOWN": "खाली", "-": "—"}[sig["leaning"]] + ")")
            st.dataframe(pd.DataFrame([{"": "✅" if ok else "❌", "अट": n, "आत्ता": d} for n, ok, d in sig["checks"]]),
                         hide_index=True, use_container_width=True)
        with c2:
            st.subheader("🎯 Option")
            if opt:
                st.write(f"Strike **{opt['strike']:.0f}** {opt['side'].upper()} · LTP **{opt['ltp']}** · bid/ask "
                         f"{opt['bid']}/{opt['ask']} · OI {opt['oi']:,.0f} · delta {opt['delta']:.2f}"
                         + (f" · spread {opt['spread_pct']:.1f}%" if opt['spread_pct'] is not None else ""))
                if opt["issues"]:
                    st.warning(" | ".join(opt["issues"]))
            else:
                st.caption("सिग्नल आल्यावर इथे strike आणि योजना दिसेल.")

        st.subheader("🌊 OI flow (option chain)")
        n_hist = len(st.session_state["flow_store"])
        if flows:
            atm = round(spot / meta["step"]) * meta["step"]
            n_up = sum(1 for x in flows if x["direction"] == "UP")
            n_dn = len(flows) - n_up
            st.markdown(f"**एकूण कल:** {'🔴 DOWN' if n_dn > n_up else ('🟢 UP' if n_up > n_dn else '⚪ मिश्र')} "
                        f"(UP {n_up} / DOWN {n_dn}). सगळे एकाच दिशेला असतील तर ते स्पॉट च्या हालचालीचा परिणाम असू शकतो.")
            if not all(x.get("adj") for x in flows):
                st.warning("⚠ काही ओळींसाठी spot वगळून हिशेब बंद आहे (delta/spot इतिहास अपुरा). त्या ओळींचा premium कच्चा आहे, म्हणजे spot च्या हालचालीचा परिणाम त्यात मिसळलेला असू शकतो.")
            mv = spot_move_pct(st.session_state["flow_store"], cfg.flow_window_min, sym)
            st.caption(f"spot ची हालचाल (शेवटच्या {cfg.flow_window_min:.0f} मि): "
                       + (f"{mv:+.2f}% (~{mv / 100 * spot:+.0f} पॉइंट)" if mv is not None else "इतिहास जमतोय")
                       + " · Premium 'spot वगळून' = कच्चा premium − (delta × spot बदल). दोन्ही सारखे असतील तर spot ने फारसा हललाच नाही.")
            st.dataframe(pd.DataFrame([{
                "Strike": f"{x['strike']:.0f} {x['side'].upper()}", "दिशा": x["direction"], "Score": x["score"],
                "घ्यायचे": ("PE" if x["direction"] == "DOWN" else "CE") + f" खरेदी (ATM {atm:.0f})",
                f"OI Δ% ({x['span_min']:.0f}मि)": round(x["oi_pct"], 1),
                "Premium Δ% (spot वगळून)": round(x["prem_pct"], 1), "Premium Δ% (कच्चा)": round(x["prem_raw"], 1),
                "Volume x": round(x["vol_ratio"], 1) if x["vol_ratio"] else None, "OI": f"{x['oi']:,.0f}",
                "कारण": "; ".join(x["reasons"])} for x in flows[:8]]), hide_index=True, use_container_width=True)
            st.caption(("🔒 सलग " + f"{cfg.flow_confirm_min:.0f}+ मिनिटे तोच top: पुष्टी झाली → " + flow_best["direction"])
                       if flow_ok else "अजून पुष्टी नाही (तोच top candidate सलग 2 मिनिटे हवा). फक्त माहिती; दिशा सिद्ध नाही.")
            # Flow varun suchavlela strike + yojana (chart signal nasla tari mahiti mhanun)
            fo = pick_option(oc, spot, flow_best["direction"], meta["step"], cfg,
                             itm_steps=1 if strike_mode.startswith("1") else 0)
            if fo["ok"]:
                fp = trade_plan(fo, sig["atr"], cfg, int(lot_size), float(capital), float(risk_pct))
                side_txt = "PUT (PE)" if flow_best["direction"] == "DOWN" else "CALL (CE)"
                if fp["ok"]:
                    st.info(f"📍 **OI flow नुसार सुचवलेला strike:** {sym} **{fo['strike']:.0f} {side_txt}** खरेदी · "
                            f"Entry ~₹{fp['entry']} · SL ₹{fp['sl']} · T1 ₹{fp['t1']} · T2 ₹{fp['t2']} · {fp['lots']} lot · "
                            f"कमाल तोटा ₹{fp['max_loss']:.0f}\n\n"
                            + ("✅ चार्ट सिग्नल याच दिशेचा आहे." if direction == flow_best["direction"] else
                               "⚠ चार्ट सिग्नल याच दिशेचा नाही (ENTER नाही). हा फक्त OI चा कल आहे; एकटा आधार म्हणून घेऊ नका."))
                else:
                    st.warning(f"OI flow दिशा {flow_best['direction']}, strike {fo['strike']:.0f}: " + (fp.get("note") or "योजना बनत नाही"))
            else:
                st.warning(f"OI flow दिशा {flow_best['direction']}, पण option बाद: " + " | ".join(fo["issues"]))
        else:
            st.info(f"OI flow candidate नाही ({n_hist} option चा इतिहास जमा; किमान ~3-5 मिनिटांचा डेटा लागतो).")

        st.subheader("🟡 CAS: writers चा radar, move च्या आधी (फक्त माहिती)")
        cas_list = cas_detect(st.session_state["flow_store"], sym, expiry, oc, spot, cfg, meta["step"])
        if cas_list:
            icon = {"STRONG": "🔥", "EARLY": "🟡", "LATE": "⏰"}
            for c in cas_list[:3]:
                head = (f"{icon[c['stage']]} **{c['stage']}** · {'🔴 BEARISH' if c['bias'] == 'BEARISH' else '🟢 BULLISH'} · "
                        f"{c['strike']:.0f} {c['side'].upper()} · score {c['score']:.0f} · " + " · ".join(c["reasons"]))
                if c["stage"] == "LATE":
                    st.info(head + "\n\nspot आधीच हलला; आता उशीर झाला असू शकतो.")
                    continue
                sp = cas_spread(c, oc, meta["step"], int(lot_size), float(capital), float(risk_pct), cfg)
                if sp["ok"]:
                    st.success(f"{head}\n\n**{sp['name']}**: SELL {sp['sell']:.0f} {sp['side'].upper()} @ {sp['sell_px']} / "
                               f"BUY hedge **{sp['hedge']:.0f}** @ {sp['hedge_px']} · Credit **₹{sp['credit']}** · reward/risk {sp['rr']} · "
                               f"{sp['lots']} lot · कमाल नफा ₹{sp['max_profit']:.0f} · कमाल तोटा ₹{sp['max_loss']:.0f} · Breakeven {sp['breakeven']:.0f}")
                    if len(sp["alts"]) > 1:
                        st.caption("इतर hedge: " + " | ".join(f"{a['hedge']:.0f} (credit {a['credit']}, rr {a['rr']:.2f}, तोटा ₹{a['max_loss']:.0f})" for a in sp["alts"][1:]))
                else:
                    st.warning(head + "\n\nSpread नाही: " + " | ".join(sp["issues"]))
            st.caption("Credit spread = विकणे; margin लागतो (इथे धरलेला नाही, broker वर तपासा). हा 'writers जमत असावेत' असा अंदाज आहे, move येईलच याची खात्री नाही.")
        else:
            st.caption("CAS नाही (OI वाढ + premium दबाव हवा; इतिहास जमा व्हायला किमान 2–10 मिनिटे लागतात).")

        st.subheader("📒 Journal (paper-trade): win rate")
        J = st.session_state["journal"]
        sm = summarize(J)
        m1, m2, m3, m4 = st.columns(4)
        m1.metric("चालू", sm["open"])
        m2.metric("जिंकले / हरले", f"{sm['wins']} / {sm['losses']}")
        m3.metric("Win rate", f"{sm['win_rate']:.0f}%" if sm["win_rate"] is not None else "—")
        m4.metric("निर्णय नाही (EXPIRED)", sm["undecided"])
        if sm["wins"] + sm["losses"] < 30:
            st.caption(f"फक्त {sm['wins'] + sm['losses']} निकाल. 30+ होईपर्यंत win rate वर विश्वास ठेवू नका. "
                       "'जिंकले' = किमान T1 लागला. Refresh च्या मधला wick चुकू शकतो.")
        if J:
            rows = [{"वेळ": datetime.fromtimestamp(t["t"], IST).strftime("%d-%b %H:%M"), "Index": t["sym"],
                     "Option": f"{t['strike']:.0f} {t['side'].upper()}", "Expiry": t["expiry"], "Entry": t["entry"],
                     "आत्ता": t["last"], "SL": t["sl"], "T1": t["t1"], "T2": t["t2"], "Lots": t["lots"],
                     "OI flow": {"agree": "✅", "oppose": "⚠", "none": "—"}.get(t.get("flow", "none"), "—"),
                     "Status": t["status"], "P/L pts": round((t["exit"] or t["last"]) - t["entry"], 2)}
                    for t in reversed(J)]
            st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
        if run_v40_on:
            st.divider()
            v40_file = Path(__file__).with_name("v40_engine.py")
            if not v40_file.exists():
                st.warning("v40_engine.py सापडली नाही (samadhan_terminal.py च्या शेजारी ठेवा).")
            else:
                ctx = (sym, expiry)
                if st.session_state.get("v40_ctx") != ctx:          # index/expiry badalla = V40 chi memory navi (key madhe index nasto)
                    for k_ in [k_ for k_ in st.session_state if str(k_).startswith("v40_")]:
                        del st.session_state[k_]
                    st.session_state["v40_ctx"] = ctx
                for env_k, sec_k in (("V40_TELEGRAM_TOKEN", "TELEGRAM_TOKEN"), ("V40_TELEGRAM_CHAT", "TELEGRAM_CHAT_ID")):
                    if secret(sec_k) and not os.environ.get(env_k):
                        os.environ[env_k] = secret(sec_k)
                g = {"__name__": "v40_engine", "OptionChain": {"data": {"data": {"oc": oc, "last_price": spot}}},
                     "spot": spot, "df_5": df, "expiry": expiry}
                try:
                    exec(get_v40_code(str(v40_file)), g)
                except Exception as e_:
                    st.error(f"V40 इंजिन त्रुटी: {e_}")
                vs = st.session_state.get("v40_last_signal")
                if vs and vs.get("scan") == st.session_state.get("v40_scan_count"):
                    vp = v40_plan(vs, oc, sig["atr"], cfg, int(lot_size), float(capital), float(risk_pct), meta["step"])
                    if vp["ok"]:
                        p_ = vp["plan"]
                        st.info(f"📍 **V40 सिग्नलची योजना (तुमच्या जोखमीनुसार):** {sym} **{vs['strike']} {vs['type']}** खरेदी · "
                                f"Entry ~₹{p_['entry']} · SL ₹{p_['sl']} · T1 ₹{p_['t1']} · T2 ₹{p_['t2']} · {p_['lots']} lot · "
                                f"कमाल तोटा ₹{p_['max_loss']:.0f}  (V40 चा SL नाही; हा index ATR+delta वरून अंदाजे)")
                    else:
                        st.warning("V40 सिग्नल आहे, पण योजना बनत नाही: " + (vp["note"] or "?"))

        st.info("हे इंडिकेटर-नियम आहेत, भाकीत नाही. SL/Target index ATR आणि delta वरून premium मध्ये काढले आहेत "
                "(अंदाजे; gamma/theta धरलेले नाही). OI flow फक्त माहिती आहे, ऑपरेटर कोण हे सिद्ध होत नाही. खरे पैसे "
                "लावण्याआधी काही आठवडे paper-trade करा. हा आर्थिक सल्ला नाही.")

    if hasattr(st, "fragment"):
        st.fragment(run_every=refresh)(view)()
    else:
        view()
        time.sleep(refresh)
        st.rerun()


if __name__ == "__main__":
    main()
