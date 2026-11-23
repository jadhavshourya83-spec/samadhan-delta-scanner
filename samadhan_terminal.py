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
    min_aligned: int = 3          # DMI/VI/KST pyki kitI ekach bajula (3 = tinhi)
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
    flow_oi_jump_max: float = 40.0     # 5 min madhe OI itka % jast badalla (kinva ek snapshot madhe 2x/0.5x) tar data sanshayaspad -> sanket baad
    flow_spot_conflict_pct: float = 0.10   # spot itka % ulat dishene halla ahe tar sanket baad (UP sanket + spot khali, kinva ulat)
    flow_block_conflict: bool = True
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
        n_al = sum(1 for x, y in pairs.values() if (float(x.iloc[-1]) > float(y.iloc[-1])) == (direction == "UP"))
        aligned = n_al >= cfg.min_aligned
        rsi_lo, rsi_hi = cfg.rsi_up if direction == "UP" else cfg.rsi_dn
        trend_ok = (now_close > e9.iloc[-1] > e20.iloc[-1]) if direction == "UP" else (now_close < e9.iloc[-1] < e20.iloc[-1])
        ext = (now_close - float(e20.iloc[-1])) * sgn / now_atr
        ref_bar = max(fresh.values()) if fresh else 0
        move = (now_close - float(close.iloc[-1 - ref_bar])) * sgn / now_atr
        checks = [
            (f"ताजे cross ≥ {cfg.min_crosses} (गेल्या {cfg.cross_n} candle)", len(fresh) >= cfg.min_crosses,
             ", ".join(f"{n}:{b} candle आधी" for n, b in fresh.items()) or "एकही ताजा cross नाही"),
            (f"DMI, VI, KST पैकी ≥ {cfg.min_aligned} एकाच बाजूला", aligned, f"{n_al}/3"),
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


def continuation_signal(d, cfg):
    """Trend continuation / pullback (PRAYOGIK). Jevha 'ताजा cross' nasto pan trend majbut asto ani bhav EMA9 pashi parat valun punha pudhe jato.
    UP: DMI/VI/KST paiki >=2 vari, ADX>=25, EMA9>EMA20 ani EMA20 vadhtoy, gelya 8 candle madhe low EMA9 javal (<=0.25 ATR) aala,
    atta close EMA9 chya vari ani maagchya candle chya high chya vari, RSI 45-72, EMA20 pasun <=1.5 ATR. DOWN: ulat.
    Parat: {'direction': 'UP'|'DOWN'|None, 'checks': [...]}"""
    if d is None or len(d) < 60:
        return {"direction": None, "checks": []}
    close, high, low = d["close"].astype(float), d["high"].astype(float), d["low"].astype(float)
    pdi, mdi, adx = dmi(d)
    vip, vim = vortex(d)
    k, ks = kst(close)
    e9, e20 = ema(close, 9), ema(close, 20)
    r, a = rsi(close), atr(d)
    c0, atr0 = float(close.iloc[-1]), float(a.iloc[-1])
    if not (atr0 > 0):
        return {"direction": None, "checks": []}
    out = {}
    for direction in ("UP", "DOWN"):
        sg = 1 if direction == "UP" else -1
        al = sum(1 for x, y in ((pdi, mdi), (vip, vim), (k, ks)) if (float(x.iloc[-1]) > float(y.iloc[-1])) == (direction == "UP"))
        slope = (float(e20.iloc[-1]) - float(e20.iloc[-4])) * sg > 0
        if direction == "UP":
            order = float(e9.iloc[-1]) > float(e20.iloc[-1])
            touched = any(float(low.iloc[-i]) <= float(e9.iloc[-i]) + 0.25 * atr0 for i in range(2, 10))
            resume = c0 > float(e9.iloc[-1]) and c0 > float(high.iloc[-2])
            rsi_ok = 45 <= float(r.iloc[-1]) <= 72
        else:
            order = float(e9.iloc[-1]) < float(e20.iloc[-1])
            touched = any(float(high.iloc[-i]) >= float(e9.iloc[-i]) - 0.25 * atr0 for i in range(2, 10))
            resume = c0 < float(e9.iloc[-1]) and c0 < float(low.iloc[-2])
            rsi_ok = 28 <= float(r.iloc[-1]) <= 55
        ext = (c0 - float(e20.iloc[-1])) * sg / atr0
        checks = [("DMI, VI, KST पैकी ≥ 2 एकाच बाजूला", al >= 2, f"{al}/3"),
                  ("ADX ≥ 25", float(adx.iloc[-1]) >= 25, f"{float(adx.iloc[-1]):.1f}"),
                  ("EMA9/EMA20 दिशेनुसार आणि EMA20 त्याच दिशेने", bool(order and slope), f"EMA9 {float(e9.iloc[-1]):.1f}, EMA20 {float(e20.iloc[-1]):.1f}"),
                  ("गेल्या 8 candle मध्ये EMA9 जवळ pullback", bool(touched), "होय" if touched else "नाही"),
                  ("पुन्हा पुढे: close EMA9 पलीकडे आणि मागच्या candle च्या extreme पलीकडे", bool(resume), "होय" if resume else "नाही"),
                  ("RSI " + ("45–72" if direction == "UP" else "28–55"), bool(rsi_ok), f"{float(r.iloc[-1]):.1f}"),
                  ("EMA20 पासून ≤ 1.5 ATR", ext <= 1.5, f"{ext:.2f} ATR")]
        out[direction] = (all(c[1] for c in checks), checks)
    ok_up, ok_dn = out["UP"][0], out["DOWN"][0]
    if ok_up and not ok_dn:
        return {"direction": "UP", "checks": out["UP"][1]}
    if ok_dn and not ok_up:
        return {"direction": "DOWN", "checks": out["DOWN"][1]}
    lean = "UP" if sum(c[1] for c in out["UP"][1]) >= sum(c[1] for c in out["DOWN"][1]) else "DOWN"
    return {"direction": None, "checks": out[lean][1]}


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


def implied_spot(oc, min_px=0.3):
    """Put-call parity: K + CE - PE = option cha khara underlying. MCX madhe chain cha 'last_price' dusrya mahinyacha future asu shakto
    (expiry diwashi 4% paryant farak), pan option bhav tya expiry chya futurevar astat. ATM/delta/strike nivd yavarun hava.
    ATM javalchya 3 strike cha median (CE ani PE donhi >= min_px). Kami data = None."""
    est = []
    for k, node in strike_map(oc).items():
        ce = _f(((node or {}).get("ce") or {}).get("last_price"))
        pe = _f(((node or {}).get("pe") or {}).get("last_price"))
        if ce >= min_px and pe >= min_px:
            est.append((abs(ce - pe), k + ce - pe))
    if len(est) < 3:
        return None
    est.sort()
    top = sorted(v for _, v in est[:3])
    return top[1]


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


def oi_suspect(h, window_min, max_pct, oi_pct):
    """OI cha data sanshayaspad ahe ka: window madhe jast %, kinva don snapshot madhe 2x/0.5x udi (data update/reset chi shakyata)."""
    if abs(oi_pct) > max_pct:
        return True
    if not h:
        return False
    t_now = h[-1][0]
    recent = [x for x in h if x[0] >= t_now - window_min * 60 - 60]
    for a, b in zip(recent, recent[1:]):
        if a[2] > 0 and b[2] > 0:
            r = b[2] / a[2]
            if r >= 2.0 or r <= 0.5:
                return True
    return False


def flow_rank(store, sym, expiry, oc, spot, cfg, step):
    out, dropped = [], []
    mv = spot_move_pct(store, cfg.flow_window_min, sym)
    for strike, node in strike_map(oc).items():
        if abs(strike - spot) > cfg.flow_steps * step:
            continue
        for side in ("ce", "pe"):
            o = (node or {}).get(side) or {}
            ltp, oi = _f(o.get("last_price")), _f(o.get("oi"))
            if ltp < cfg.flow_min_ltp or oi < cfg.min_oi:        # ashakya/pathal strikes baad
                continue
            hist = store.get(flow_key(sym, expiry, strike, side))
            m = flow_metrics(hist, cfg.flow_window_min, spot_hist=store.get(f"__spot__{sym}"))
            if not m:
                continue
            if oi_suspect(hist, cfg.flow_window_min, cfg.flow_oi_jump_max, m["oi_pct"]):
                dropped.append(f"{strike:.0f} {side.upper()}: OI डेटा संशयास्पद (OI {m['oi_pct']:+.0f}%); संकेत बाजूला")
                continue
            sc = flow_score(side.upper(), strike, spot, m["oi_pct"], m["prem_pct"], m["vol_ratio"], m["iv_chg"],
                            prem_raw=m.get("prem_raw"), adj=m.get("adj", False))
            if sc["score"] >= cfg.flow_min_score:
                if (cfg.flow_block_conflict and mv is not None and abs(mv) >= cfg.flow_spot_conflict_pct
                        and ((sc["direction"] == "UP" and mv < 0) or (sc["direction"] == "DOWN" and mv > 0))):
                    dropped.append(f"{strike:.0f} {side.upper()}: संकेत {sc['direction']} पण spot {mv:+.2f}% उलट दिशेने; बाजूला")
                    continue
                out.append({"strike": strike, "side": side, "ltp": ltp, "oi": oi, **m, **sc})
    store["__flow_dropped__"] = dropped
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


# ───────── SIGNAL TRACKER: kon-ta engine kiti vela barobar? (spot chya dishene, 60 min madhe) ─────────
SIG_X = 0.15          # % : sanketachya dishene itka halla = HIT, ulat itka halla = MISS
SIG_HORIZON_MIN = 60  # itkya velat kahich nahi = NO MOVE


def siglog_add(log, now_ts, sym, expiry, engine, direction, strike, side, spot, score, exp_day, dedupe_min=15):
    """Navin sanket nondva. Tyach engine/dishecha sanket 15 min madhe punha ala tar nondvat nahi."""
    if direction not in ("UP", "DOWN") or not spot:
        return False
    for r in reversed(log):
        if r["sym"] == sym and r["engine"] == engine and r["dir"] == direction and now_ts - r["t"] < dedupe_min * 60:
            return False
    log.append({"t": now_ts, "sym": sym, "expiry": expiry, "engine": engine, "dir": direction, "strike": strike,
                "side": side, "spot0": float(spot), "score": score, "exp_day": bool(exp_day), "status": "OPEN",
                "fav": 0.0, "adv": 0.0, "result": None, "t_res": None})
    return True


def siglog_update(log, now_ts, sym, spot, x=SIG_X, horizon_min=SIG_HORIZON_MIN):
    """Ughdya sanketanchi ajchi spot disha tapasa. HIT/MISS/NO MOVE. App band hota ani data tutla tar 'NO DATA'."""
    if not spot:
        return
    for r in log:
        if r["status"] != "OPEN" or r["sym"] != sym:
            continue
        age = now_ts - r["t"]
        if age > horizon_min * 60 * 1.5:          # refresh madhe khup antar = mojta yet nahi
            r["status"], r["result"], r["t_res"] = "DONE", "NO DATA", now_ts
            continue
        mv = (spot / r["spot0"] - 1) * 100 * (1 if r["dir"] == "UP" else -1)
        r["fav"], r["adv"] = max(r["fav"], mv), min(r["adv"], mv)
        if mv >= x:
            res = "HIT"
        elif mv <= -x:
            res = "MISS"
        elif age >= horizon_min * 60:
            res = "NO MOVE"
        else:
            continue
        r["status"], r["result"], r["t_res"] = "DONE", res, now_ts


def siglog_summary(log):
    """Engine x (expiry diwas / itar diwas) pramane HIT/MISS/NO MOVE. Pharak 'NO DATA' mojat nahi."""
    rows = {}
    for r in log:
        if r["status"] != "DONE" or r["result"] == "NO DATA":
            continue
        k = (r["engine"], "expiry diwas" if r["exp_day"] else "itar diwas")
        d = rows.setdefault(k, {"n": 0, "HIT": 0, "MISS": 0, "NO MOVE": 0})
        d["n"] += 1
        d[r["result"]] += 1
    out = []
    for (eng, day), d in sorted(rows.items()):
        dec = d["HIT"] + d["MISS"]
        out.append({"engine": eng, "diwas": day, "sanket": d["n"], "HIT": d["HIT"], "MISS": d["MISS"], "NO MOVE": d["NO MOVE"],
                    "HIT % (HIT+MISS madhe)": None if dec == 0 else round(100 * d["HIT"] / dec, 1)})
    return out


MISS_WINDOW_MIN = 30


def missed_move(hist, siglog, now_ts, sym, spot, thr_pct):
    """Spot gelya 30 min madhe thr_pct% peksha jast halla ani tya disheche sanket (kontyahi engine che) tyachya aadhi/dorm. nondle nahit -> chukleli chaal.
    hist = [(ts, spot), ...]. Parat: {'dir','mv','t0'} kinva None."""
    pts = [(t, v) for t, v in hist if now_ts - MISS_WINDOW_MIN * 60 <= t <= now_ts and v]
    if len(pts) < 5 or not spot:
        return None
    t0, s0 = pts[0]
    mv = (spot / s0 - 1) * 100
    if abs(mv) < thr_pct:
        return None
    d = "UP" if mv > 0 else "DOWN"
    for r in siglog:
        if r["sym"] == sym and r["dir"] == d and r["t"] >= t0 - MISS_WINDOW_MIN * 60:
            return None
    return {"dir": d, "mv": round(mv, 2), "t0": t0}


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


# ───────── SAHMATI (sagle engine eka nirnayat) ─────────
# ───────── Monthly HOLD engine (KST, DMI, MOM, MACD, ALMA, HMA, VI, EMA9) ─────────
def wma(s, n):
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n).apply(lambda x: float(np.dot(x, w) / w.sum()), raw=True)


def hma(s, n=21):
    half, sq = max(1, n // 2), max(1, int(round(n ** 0.5)))
    return wma(2 * wma(s, half) - wma(s, n), sq)


def alma(s, n=9, offset=0.85, sigma=6.0):
    m, sg = offset * (n - 1), n / sigma
    w = np.exp(-((np.arange(n) - m) ** 2) / (2 * sg * sg))
    w = w / w.sum()
    return s.rolling(n).apply(lambda x: float(np.dot(x, w)), raw=True)


def macd_lines(close, fast=12, slow=26, sig=9):
    line = ema(close, fast) - ema(close, slow)
    return line, ema(line, sig)


def monthly_pairs(d):
    """Pratyek indicator = (a, b); a > b mhanje UP. 8 indicators."""
    close = d["close"].astype(float)
    pdi, mdi, _ = dmi(d)
    vip, vim = vortex(d)
    k, ks = kst(close)
    ml, ms = macd_lines(close)
    mom = close - close.shift(10)
    return {"KST": (k, ks), "DMI": (pdi, mdi), "MOM": (mom, mom * 0.0), "MACD": (ml, ms),
            "ALMA": (alma(close, 9), alma(close, 21)), "HMA": (hma(close, 9), hma(close, 21)),
            "VI": (vip, vim), "EMA9": (close, ema(close, 9))}


def monthly_hold_signal(d, cross_n=5, min_aligned=8, min_fresh=2, adx_min=20.0):
    """Sarva (default 8/8) indicators ekach bajula ani kimaan `min_fresh` taaje cross gelya `cross_n` candle madhe."""
    if d is None or len(d) < 70:
        return {"direction": "WAIT", "checks": [], "leaning": "-", "aligned": 0, "n": 8,
                "note": f"डेटा अपुरा (किमान 70 candle हव्यात, आहेत {0 if d is None else len(d)})"}
    pairs = monthly_pairs(d)
    adx_now = float(dmi(d)[2].iloc[-1])
    res = {}
    for direction in ("UP", "DOWN"):
        up = direction == "UP"
        rows, n_al, fresh = [], 0, 0
        for name, (a, b) in pairs.items():
            av, bv = a.iloc[-1], b.iloc[-1]
            if pd.isna(av) or pd.isna(bv):
                rows.append((name, False, "डेटा नाही"))
                continue
            al = (av > bv) if up else (av < bv)
            bs = bars_since_cross(a, b, direction, cross_n)
            n_al += int(al)
            fresh += int(bs is not None)
            rows.append((name, bool(al), "cross %d candle आधी" % bs if bs is not None else ("बाजू बरोबर" if al else "उलट बाजू")))
        res[direction] = {"rows": rows, "aligned": n_al, "fresh": fresh,
                          "ok": n_al >= min_aligned and fresh >= min_fresh and adx_now >= adx_min}
    lean = "UP" if (res["UP"]["aligned"], res["UP"]["fresh"]) >= (res["DOWN"]["aligned"], res["DOWN"]["fresh"]) else "DOWN"
    b_ = res[lean]
    direction = lean if (b_["ok"] and not res["DOWN" if lean == "UP" else "UP"]["ok"]) else "WAIT"
    checks = list(b_["rows"]) + [(f"cross खिडकीत ≥ {min_fresh}/8 (गेल्या {cross_n} candle)", b_["fresh"] >= min_fresh, f"{b_['fresh']}"),
                                 (f"एकाच बाजूला ≥ {min_aligned}/8", b_["aligned"] >= min_aligned, f"{b_['aligned']}/8"),
                                 (f"ADX ≥ {adx_min:.0f} (ट्रेंड आहे, सरळ बाजार नाही)", adx_now >= adx_min, f"{adx_now:.1f}")]
    return {"direction": direction, "leaning": lean, "checks": checks, "aligned": b_["aligned"], "fresh": b_["fresh"], "n": 8, "note": ""}


def monthly_align_series(d):
    """Pratyek candle var: kiti indicators UP / DOWN bajula (a>b / a<b)."""
    up = dn = None
    for a, b in monthly_pairs(d).values():
        u, w = (a > b).astype(int), (a < b).astype(int)
        up = u if up is None else up + u
        dn = w if dn is None else dn + w
    return up, dn


def monthly_pullback(d, window=75, min_aligned=8, adx_min=20.0, retrace=0.5, pb_aligned=5):
    """Gelya `window` candle madhe sarva (>=min_aligned) indicators ekach bajula ale hote ka?
    Asel tar: chaal kiti jhali (ATR), ani pullback pattali (peak - retrace x swing). Candle data varun, memory nako."""
    if d is None or len(d) < 70:
        return None
    close, high, low = (d[c].astype(float) for c in ("close", "high", "low"))
    up_c, dn_c = monthly_align_series(d)
    adx = dmi(d)[2]
    now_atr = float(atr(d).iloc[-1])
    if not (now_atr > 0):
        return None
    n, best = len(d), None
    for direction, cnt in (("UP", up_c), ("DOWN", dn_c)):
        ok = ((cnt >= min_aligned) & (adx >= adx_min)).tolist()
        idx = [i for i in range(max(0, n - window), n) if ok[i]]
        if not idx:
            continue
        j = idx[-1]
        s_ = j
        while s_ - 1 >= 0 and ok[s_ - 1]:
            s_ -= 1
        start = float(close.iloc[s_])
        if direction == "UP":
            peak = float(high.iloc[s_:].max())
            level = peak - retrace * (peak - start)
            held = float(close.iloc[-1]) > start
            reached = float(close.iloc[-1]) <= level
            swing = peak - start
        else:
            peak = float(low.iloc[s_:].min())
            level = peak + retrace * (start - peak)
            held = float(close.iloc[-1]) < start
            reached = float(close.iloc[-1]) >= level
            swing = start - peak
        al_now = int((up_c if direction == "UP" else dn_c).iloc[-1])
        cand = {"direction": direction, "start": start, "peak": peak, "level": level, "move_atr": max(0.0, swing) / now_atr,
                "bars_ago": n - 1 - j, "aligned_now": al_now, "held": held, "reached": reached,
                "ready": bool(reached and held and al_now >= pb_aligned and swing > 0)}
        if best is None or cand["bars_ago"] < best["bars_ago"]:
            best = cand
    return best


STOCK_LIST = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK", "LT", "ITC",
    "BHARTIARTL", "HINDUNILVR", "BAJFINANCE", "MARUTI", "SUNPHARMA", "TATASTEEL", "WIPRO", "HCLTECH",
    "ADANIENT", "ADANIPORTS", "ONGC", "NTPC", "POWERGRID", "COALINDIA", "JSWSTEEL", "ULTRACEMCO",
    "TITAN", "ASIANPAINT", "NESTLEIND", "M&M", "BAJAJFINSV", "TECHM", "INDUSINDBK", "HINDALCO",
    "GRASIM", "DRREDDY", "CIPLA", "EICHERMOT", "BPCL", "TATACONSUM", "APOLLOHOSP", "DIVISLAB",
    "HEROMOTOCO", "BRITANNIA", "SBILIFE",
]


def scan_stock_daily(raw, now, window=10, min_aligned=7, min_fresh=5, adx_min=20.0, late_atr=1.5, retrace=0.4, pb_aligned=3):
    """Ek stock cha daily candle var Monthly HOLD nikal. status: FRESH / LATE / PULLBACK READY / WAIT / data apura."""
    df, _ = drop_forming_candle(raw, 1440, now)             # chalu (apurna) divsachi candle vagale jate
    if df is None or len(df) < 70:
        return {"status": "डेटा अपुरा", "direction": "-", "aligned": 0, "adx": 0.0, "move_atr": 0.0,
                "level": None, "close": None, "note": f"candle {0 if df is None else len(df)} (किमान 70)"}
    sig = monthly_hold_signal(df, window, min_aligned, min_fresh, adx_min)
    pb = monthly_pullback(df, window, min_aligned, adx_min, retrace, pb_aligned)
    late = bool(pb and pb["move_atr"] > late_atr)
    if late and pb["ready"]:
        status, direction = "PULLBACK READY", pb["direction"]
    elif late:
        status, direction = "LATE", pb["direction"]
    elif sig["direction"] in ("UP", "DOWN"):
        status, direction = "FRESH", sig["direction"]
    else:
        status, direction = "WAIT", sig.get("leaning", "-")
    return {"status": status, "direction": direction, "aligned": int(sig.get("aligned", 0)), "adx": float(dmi(df)[2].iloc[-1]),
            "move_atr": float(pb["move_atr"]) if pb else 0.0, "level": float(pb["level"]) if pb else None,
            "close": float(df["close"].iloc[-1]), "note": ""}


def monthly_expiries(exp_list):
    """Pratyek mahinyatli shevtchi expiry (monthly)."""
    last = {}
    for e in sorted(exp_list):
        last[e[:7]] = e
    return sorted(last.values())


def pick_by_premium(oc, direction, target, spot=0.0, min_oi=1000.0, max_spread_pct=15.0):
    """Premium `target` javalcha strike (CE=UP, PE=DOWN). Liquidity tapasto."""
    side = "ce" if direction == "UP" else "pe"
    smap = strike_map(oc)
    best = None
    for strike, node_ in smap.items():
        node = (node_ or {}).get(side) or {}
        ltp = _f(node.get("last_price"))
        if ltp <= 0:
            continue
        cand = (abs(ltp - target) / max(target, 0.05), strike, node, ltp)
        if best is None or cand[0] < best[0]:
            best = cand
    if not best:
        return {"ok": False, "issues": ["chain मध्ये भाव असलेला strike नाही"], "side": side}
    _, strike, node, ltp = best
    bid, ask = _f(node.get("top_bid_price")), _f(node.get("top_ask_price"))
    oi, vol = _f(node.get("oi")), _f(node.get("volume"))
    delta = abs(_f((node.get("greeks") or {}).get("delta")))
    spread = ((ask - bid) / ((ask + bid) / 2) * 100) if (bid > 0 and ask > 0) else None
    issues = []
    if oi < min_oi:
        issues.append(f"OI कमी ({oi:,.0f} < {min_oi:,.0f})")
    if spread is not None and spread > max_spread_pct:
        issues.append(f"bid-ask spread जास्त ({spread:.1f}%)")
    warn = []
    dist = abs(strike - spot) / spot * 100 if spot else None
    if delta and delta < 0.10:
        warn.append(f"delta फक्त {delta:.2f}: खूप लांबचा strike, लॉटरीसारखा; भाव क्वचित हलतो")
    if abs(ltp - target) / max(target, 0.05) > 0.5:
        warn.append(f"₹{target:g} जवळचा strike नाही (सर्वात जवळचा ₹{ltp:g})")
    return {"ok": not issues, "issues": issues, "warn": warn, "side": side, "strike": strike, "ltp": ltp, "bid": bid,
            "ask": ask, "oi": oi, "volume": vol, "delta": delta, "spread_pct": spread, "dist_pct": dist}


def pick_by_delta(oc, direction, d_lo, d_hi, spot=0.0, step=50.0, min_oi=1000.0, max_spread_pct=15.0):
    """Delta [d_lo, d_hi] madhla strike (madhyabindu javalcha). Dhan ne delta dila nasel tar andaje delta."""
    side = "ce" if direction == "UP" else "pe"
    smap = strike_map(oc)
    mid, cands, est_used = (d_lo + d_hi) / 2.0, [], False
    for strike, node_ in smap.items():
        node = (node_ or {}).get(side) or {}
        ltp = _f(node.get("last_price"))
        if ltp <= 0:
            continue
        dl = abs(_f((node.get("greeks") or {}).get("delta")))
        est = False
        if dl <= 0 and spot:
            dl, est = abs(est_delta(side, strike, spot, step)), True
        if dl <= 0:
            continue
        cands.append((abs(dl - mid), strike, node, ltp, dl, est))
    if not cands:
        return {"ok": False, "issues": ["chain मध्ये delta/भाव असलेला strike नाही"], "side": side}
    inside = [c for c in cands if d_lo <= c[4] <= d_hi]
    liquid = [c for c in (inside or cands)
              if _f(c[2].get("oi")) >= min_oi] or (inside or cands)
    _, strike, node, ltp, delta, est = min(liquid, key=lambda c: c[0])
    bid, ask = _f(node.get("top_bid_price")), _f(node.get("top_ask_price"))
    oi, vol = _f(node.get("oi")), _f(node.get("volume"))
    spread = ((ask - bid) / ((ask + bid) / 2) * 100) if (bid > 0 and ask > 0) else None
    issues, warn = [], []
    if oi < min_oi:
        issues.append(f"OI कमी ({oi:,.0f} < {min_oi:,.0f})")
    if spread is not None and spread > max_spread_pct:
        issues.append(f"bid-ask spread जास्त ({spread:.1f}%)")
    if not (d_lo <= delta <= d_hi):
        warn.append(f"delta {delta:.2f} इच्छित {d_lo:g}–{d_hi:g} पट्ट्याबाहेर (सर्वात जवळचा)")
    if est:
        warn.append("Dhan ने delta दिला नाही; अंदाजे delta वापरला")
    return {"ok": not issues, "issues": issues, "warn": warn, "side": side, "strike": strike, "ltp": ltp, "bid": bid, "ask": ask,
            "oi": oi, "volume": vol, "delta": delta, "spread_pct": spread,
            "dist_pct": (abs(strike - spot) / spot * 100) if spot else None}


def month_plan(opt, lot_size, capital, risk_pct, sl_pct=50.0, t1_pct=100.0, t2_pct=200.0):
    entry = opt["ask"] if opt.get("ask", 0) > 0 else opt["ltp"]
    sl, t1, t2 = _tick(entry * (1 - sl_pct / 100)), _tick(entry * (1 + t1_pct / 100)), _tick(entry * (1 + t2_pct / 100))
    per_lot_loss = (entry - sl) * lot_size
    risk_amt = capital * risk_pct / 100
    lots = int(risk_amt // per_lot_loss) if per_lot_loss > 0 else 0
    cost = lots * lot_size * entry
    note = ""
    if lots < 1:
        note = f"1 lot चा संभाव्य तोटा ₹{per_lot_loss:,.0f} तुमच्या जोखीम मर्यादेपेक्षा (₹{risk_amt:,.0f}) जास्त"
    return {"ok": lots >= 1, "entry": _tick(entry), "sl": sl, "t1": t1, "t2": t2, "lots": lots, "qty": lots * lot_size,
            "max_loss": lots * per_lot_loss, "cost": cost, "note": note}


def chart_vote(sig):
    """Chart: purn signal = vote. Ekhadich ati kami asel (<=1 apayshi) tar jhukav vote."""
    if sig.get("direction") in ("UP", "DOWN"):
        return sig["direction"]
    failed = sum(1 for c in sig.get("checks", []) if not c[1])
    if sig.get("leaning") in ("UP", "DOWN") and failed <= 1:
        return sig["leaning"]
    return None


def consensus(votes, min_agree=2):
    """votes = {'चार्ट': 'UP'|'DOWN'|None, ...}. Nirnay: kimaan 2 sahmat ani virodhi 0, kinva >=3 sahmat ani virodhi <=1.
    Hi bhavishyavani nahi; 'kiti engine ekmat' evdhech sangte."""
    up = [k for k, v in votes.items() if v == "UP"]
    dn = [k for k, v in votes.items() if v == "DOWN"]
    for d, agree, opp in (("UP", up, dn), ("DOWN", dn, up)):
        if (len(agree) >= min_agree and not opp) or (len(agree) >= max(3, min_agree) and len(opp) <= 1):
            return d, agree, opp
    return "WAIT", up if len(up) >= len(dn) else dn, dn if len(up) >= len(dn) else up


def v34_winner(g):
    """V34 snippet (v34_engine.py) chya exec nantarche globals madhun 'ABSOLUTE CONFIRMED WINNER' kadha.
    Snippet chich ata: hero_list, rank >= 65, equilibrium/choppy nasel tar. Disha: CALL=UP, PUT=DOWN."""
    hero = g.get("hero_list") or []
    if not hero or g.get("is_equilibrium_active") or g.get("market_mode") == "🔴 CHOPPY / RANGEBOUND":
        return None
    lead = sorted(hero, key=lambda x: x["rank_score"], reverse=True)[0]
    if lead["rank_score"] < 65:
        return None
    try:
        strike = float(str(lead["option"]).split()[1])
    except Exception:
        strike = None
    return {"dir": "UP" if lead["type"] == "CALL" else "DOWN", "strike": strike, "side": "ce" if lead["type"] == "CALL" else "pe",
            "score": lead["rank_score"], "option": lead["option"], "stage": lead.get("stage", ""),
            "alloc": lead.get("allocation", ""), "premium": lead.get("premium"), "action": lead.get("action_text", "")}


# ───────── MCX commodity: Dhan instrument list madhun id/lot/strike-gap shodha ─────────
MCX_COMMON = ["CRUDEOIL", "CRUDEOILM", "NATURALGAS", "NATGASMINI", "GOLD", "GOLDM", "GOLDPETAL", "SILVER", "SILVERM", "SILVERMIC",
              "COPPER", "ZINC", "ZINCMINI", "ALUMINIUM", "LEAD", "NICKEL"]


def _col(df, names):
    low = {str(c).strip().lower(): c for c in df.columns}
    for n in names:
        if n.lower() in low:
            return low[n.lower()]
    return None


def find_commodity(df, name, today=None):
    """Dhan scrip-master (compact kinva detailed) madhun commodity chya option-chain sathi underlying id, lot, strike-gap.
    Underlying = option contract chya UNDERLYING_SECURITY_ID (detailed file); nasel tar nearest FUTURE cha security id.
    Kahihi na milyas None + karan."""
    today = pd.Timestamp(today or datetime.now()).normalize()
    c_ex, c_id = _col(df, ["EXCH_ID", "SEM_EXM_EXCH_ID"]), _col(df, ["SECURITY_ID", "SEM_SMST_SECURITY_ID"])
    c_in, c_exp = _col(df, ["INSTRUMENT", "SEM_INSTRUMENT_NAME"]), _col(df, ["SM_EXPIRY_DATE", "SEM_EXPIRY_DATE"])
    c_sym = [c for c in (_col(df, ["UNDERLYING_SYMBOL"]), _col(df, ["SYMBOL_NAME", "SM_SYMBOL_NAME"])) if c]
    c_lot, c_k, c_und = _col(df, ["LOT_SIZE", "SEM_LOT_UNITS"]), _col(df, ["STRIKE_PRICE", "SEM_STRIKE_PRICE"]), _col(df, ["UNDERLYING_SECURITY_ID"])
    if not (c_id and c_in and c_exp and c_sym):
        return {"ok": False, "why": "instrument list madhe apekshit stambh (security id / instrument / expiry / symbol) sapadle nahit"}
    d = df.copy()
    if c_ex:
        d = d[d[c_ex].astype(str).str.upper().str.strip() == "MCX"]
    nm = name.strip().upper()
    m = None
    for c in c_sym:
        mm = d[c].astype(str).str.upper().str.strip() == nm
        m = mm if m is None else (m | mm)
    d = d[m]
    d = d.assign(_exp=pd.to_datetime(d[c_exp], errors="coerce")).dropna(subset=["_exp"])
    d = d[d["_exp"] >= today]
    if d.empty:
        return {"ok": False, "why": f"MCX madhe '{name}' sathi chalu expiry asleli contracts sapadli nahit (naav barobar ahe ka?)"}
    inst = d[c_in].astype(str).str.upper()
    opts, futs = d[inst.str.startswith("OPT")], d[inst.str.startswith("FUT")]
    if opts.empty:
        return {"ok": False, "why": f"'{name}' sathi option contracts nahit (fakt FUTURE)"}
    oexp = opts["_exp"].min()
    o1 = opts[opts["_exp"] == oexp]
    und = None
    if c_und:
        v = pd.to_numeric(o1[c_und], errors="coerce").dropna()
        v = v[v > 0]
        if len(v):
            und = int(v.mode().iloc[0])
    how = "option contract cha underlying id"
    if und is None:
        f2 = futs[futs["_exp"] >= oexp].sort_values("_exp")
        if f2.empty:
            f2 = futs.sort_values("_exp")
        if f2.empty:
            return {"ok": False, "why": "underlying FUTURE sapadla nahi"}
        und, how = int(pd.to_numeric(f2.iloc[0][c_id])), "nearest FUTURE cha id"
    lot = None
    if c_lot:
        lv = pd.to_numeric(o1[c_lot], errors="coerce").dropna()
        lot = int(lv.mode().iloc[0]) if len(lv) else None
    step = None
    if c_k:
        ks = sorted(set(pd.to_numeric(o1[c_k], errors="coerce").dropna()))
        gaps = [b - a for a, b in zip(ks, ks[1:]) if b - a > 0]
        step = float(pd.Series(gaps).mode().iloc[0]) if gaps else None
    return {"ok": True, "id": und, "lot": lot, "step": step, "expiry": oexp.strftime("%Y-%m-%d"), "how": how}


# ═════════════════════════════ PART 2: STREAMLIT UI ═════════════════════════════
INDEX_MAP = {"NIFTY": dict(id=13, step=50, lot=65), "BANKNIFTY": dict(id=25, step=100, lot=30),
             "SENSEX": dict(id=51, step=100, lot=20),
             "कमोडिटी (MCX, हाताने भरा)": dict(id=0, step=1, lot=1, custom=True)}


def eod_time(window_end):
    """Vyapar-bandi chya 20 minute nantar sagle open trade EXPIRED (index sathi 15:00 -> 15:20)."""
    h, m = map(int, window_end.split(":"))
    tot = min(h * 60 + m + 20, 23 * 60 + 59)
    return dtime(tot // 60, tot % 60)


def main():
    import json
    import os
    from dataclasses import replace
    import time
    from datetime import timedelta, timezone
    from pathlib import Path

    import requests
    import streamlit as st

    IST = timezone(timedelta(hours=5, minutes=30))
    JOURNAL = Path(__file__).with_name("entry_journal.json")
    SIGLOG = Path(__file__).with_name("signal_log.json")
    MISSLOG = Path(__file__).with_name("missed_moves.json")

    @st.cache_data(ttl=43200, show_spinner="Dhan instrument list उतरवत आहे (मोठी फाईल, पहिल्यांदा थोडा वेळ)...")
    def load_scrip_master():
        import io
        last = None
        for url in ("https://images.dhan.co/api-data/api-scrip-master-detailed.csv",
                    "https://images.dhan.co/api-data/api-scrip-master.csv"):
            try:
                r = requests.get(url, timeout=90)
                r.raise_for_status()
                return pd.read_csv(io.StringIO(r.text), low_memory=False)
            except Exception as e:
                last = e
        raise RuntimeError(last)

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
        sym = st.selectbox("Index / कमोडिटी", list(INDEX_MAP), index=0)
        meta = dict(INDEX_MAP[sym])
        meta.setdefault("seg", "IDX_I")
        meta.setdefault("csym", sym)
        meta.setdefault("cexch", "INDEX")
        if meta.get("custom"):
            cname_sel = st.selectbox("कमोडिटी", MCX_COMMON + ["इतर (नाव लिहा)"], 0)
            cname = (st.text_input("नाव (Dhan symbol)", "").strip().upper() if cname_sel.startswith("इतर") else cname_sel)
            sym = cname or "COMMODITY"
            found = st.session_state.get("mcx_found", {}).get(sym)
            if st.button("🔎 Dhan list वरून id, lot, strike-gap शोधा"):
                try:
                    st.session_state.setdefault("mcx_found", {})[sym] = find_commodity(load_scrip_master(), sym)
                except Exception as e_:
                    st.session_state.setdefault("mcx_found", {})[sym] = {"ok": False, "why": f"instrument list उतरवता आली नाही: {e_}"}
                found = st.session_state["mcx_found"][sym]
            if found:
                if found["ok"]:
                    st.success(f"सापडले: id {found['id']} ({found['how']}) · lot {found['lot']} · strike-gap {found['step']} · जवळचा expiry {found['expiry']}")
                else:
                    st.warning(found["why"] + " : खाली हाताने भरा.")
            okf = found if (found and found["ok"]) else {}
            meta["id"] = int(st.number_input("Underlying security id (शोधले नाही तर हाताने)", 0, 100000000,
                                             int(okf.get("id") or 0), 1, key=f"cm_id_{sym}_{okf.get('id')}"))
            meta["seg"] = st.text_input("option chain segment", "MCX_COMM").strip()
            meta["csym"] = st.text_input("चार्ट symbol (Tradehull)", sym).strip()
            meta["cexch"] = st.text_input("चार्ट exchange (Tradehull)", "MCX").strip()
            meta["step"] = float(st.number_input("Strike अंतर", 0.05, 100000.0, float(okf.get("step") or 50.0), 0.05,
                                                 key=f"cm_step_{sym}_{okf.get('step')}"))
            meta["lot"] = int(okf.get("lot") or 1)
            meta["tol"] = float(st.number_input("Chain spot आणि चार्ट भावातला फरक किती चालेल (%)", 0.5, 15.0, 4.0, 0.5,
                                                help="MCX option चा underlying एका विशिष्ट महिन्याचा future असतो, चार्ट बहुधा जवळच्या महिन्याचा. "
                                                     "NATURALGAS मध्ये दोन महिन्यांच्या भावात 2-5% फरक सामान्य आहे."))
        st.subheader("💰 भांडवल व जोखीम")
        capital = st.number_input("भांडवल (₹)", 10000, 100000000, 100000, 10000)
        risk_pct = st.slider("एका ट्रेडमध्ये जोखीम %", 0.25, 3.0, 1.0, 0.25)
        lot_size = st.number_input(f"{sym} lot size (broker वर तपासा)", 1, 100000, meta["lot"], 1)
        strike_mode = st.selectbox("Strike", ["ATM", "1 ITM (जास्त delta, जास्त premium)"], 0)
        st.subheader("⏱ वेळ")
        w_start = st.text_input("ट्रेड सुरू (HH:MM)", "09:30" if not meta.get("custom") else "09:15")
        w_end = st.text_input("ट्रेड बंद (HH:MM)", "15:00" if not meta.get("custom") else "23:00")
        cooldown = st.slider("त्याच दिशेचा पुढचा सिग्नल किमान (मिनिटे)", 5, 60, 15, 5)
        st.subheader("🌊 OI flow")
        flow_gate = st.checkbox("OI flow विरोधात असेल तर थांबा", False)
        with st.expander("🟡 CAS (writers radar)"):
            cas_early = st.slider("EARLY score किमान", 30, 80, 50, 5)
            cas_strong = st.slider("STRONG score किमान", 50, 95, 70, 5)
            cas_late = st.slider("spot इतका हलला तर 'LATE' %", 0.1, 1.0, 0.40, 0.05)
        run_v40_on = st.checkbox("V40.2 OI इंजिन (तुमचा कोड) चालवा", True)
        run_cont = st.checkbox("🔁 Trend continuation / pullback (प्रायोगिक)", True,
                               help="ताजा cross नसलेल्या मजबूत ट्रेंडमध्ये EMA9 पर्यंत परत येऊन पुन्हा पुढे जाताना चार्टचे मत. एकटा entry देत नाही; सहमतीत एक मत म्हणून आणि tracker मध्ये नोंदला जातो.")
        run_v34_on = st.checkbox("V34 इंजिन (तुमचा कोड) चालवा (SENSEX / NIFTY)", False)
        run_month = st.checkbox("📅 Monthly HOLD इंजिन (प्रायोगिक)", False,
                                help="महिन्याच्या expiry चा वेगळा तारीख निवडा. KST, DMI, MOM, MACD, ALMA, HMA, VI, EMA9 सर्व एकाच बाजूला आले की entry आणि Telegram. "
                                     "Journal/सहमतीत मिसळत नाही; फक्त Signal tracker मध्ये 'MONTHLY HOLD' म्हणून नोंद.")
        with st.expander("📅 Monthly HOLD सेटिंग्ज"):
            m_mode = st.selectbox("Strike कसा निवडायचा", ["Delta वर (होल्ड करायला सोपे)", "Premium वर (₹ जवळचा)"], 0,
                                  help="Delta 0.3–0.5 = ATM च्या जवळ; index हलला की premium बऱ्यापैकी हलतो आणि शून्य होण्याची शक्यता कमी. "
                                       "Premium ₹10 सारखा स्वस्त strike खूप लांबचा असतो (लॉटरीसारखा).")
            m_dlo, m_dhi = st.slider("Delta पट्टा (Delta वर निवडल्यास)", 0.10, 0.90, (0.30, 0.50), 0.05)
            m_target = st.number_input("लक्ष्य premium ₹ (फक्त Premium वर निवडल्यास)", 1.0, 2000.0, 10.0, 1.0,
                                       help="₹10 चा monthly strike खूप लांबचा (OTM) असतो; भाव क्वचित हलतो आणि नंतर शून्य होऊ शकतो. ITM/ATM हवा असेल तर ही रक्कम वाढवा.")
            m_min_al = st.slider("किमान किती इंडिकेटर एकाच बाजूला (8 = सर्व)", 4, 8, 8)
            m_tf = st.selectbox("Monthly साठी candle", ["चार्टचाच (वर निवडलेला)", "15 मिनिट", "60 मिनिट", "1 DAY"], 0,
                                help="1 DAY निवडल्यास इंडिकेटर रोजच्या candle वर चालतात (किमान 70 दिवसांचा डेटा लागतो). तेव्हा खिडकी आपोआप 10 दिवस, "
                                     "15 मिनिट = 25 candle (1 दिवस), 60 मिनिट = 6 candle. Tradehull ने हे timeframe दिले की नाही तपासलेले नाही; त्रुटी दिसली तर कळवा.")
            m_fresh = st.slider("किमान किती इंडिकेटरचा cross खिडकीत झालेला (8 = सर्व)", 0, 8, 8)
            m_adx = st.slider("ADX किमान (सरळ बाजार टाळण्यासाठी)", 10, 40, 20)
            m_cross_n = st.slider("cross ची खिडकी: गेल्या किती candle", 2, 400, 75,
                                  help="5 मिनिटांच्या candle वर 75 candle = सुमारे 1 ट्रेडिंग दिवस. एखादा इंडिकेटर आदल्या दिवशी आणि बाकीचे नंतर आले तरी चालते; "
                                       "फक्त आत्ता सर्व एकाच बाजूला आणि प्रत्येकाचा cross खिडकीत हवा. 15 मिनिटांच्या candle वर 25 = 1 दिवस.")
            m_sl, m_t1, m_t2 = (st.number_input("SL % (premium)", 10.0, 90.0, 50.0, 5.0),
                                st.number_input("T1 % (अर्धे विका)", 20.0, 500.0, 100.0, 10.0),
                                st.number_input("T2 %", 50.0, 1000.0, 200.0, 10.0))
            m_late = st.slider("मूव्ह इतके ATR झाला की 'निघून गेला' (strike दाखवू नका)", 0.5, 5.0, 1.5, 0.25,
                               help="सर्व इंडिकेटर जुळल्यापासून भाव इतक्या ATR पेक्षा जास्त हलला असेल तर नवी entry/strike दाखवत नाही; फक्त pullback ची वाट पाहतो.")
            m_retr = st.slider("Pullback किती % (मूव्हच्या)", 15, 75, 40, 5,
                               help="मूव्हच्या टोकापासून तेवढा % परत आला की pullback entry. 38–50% सामान्य मानतात (तपासलेले नाही).")
            m_pb_al = st.slider("Pullback वेळी किमान किती इंडिकेटर अजून एकाच बाजूला", 2, 8, 3,
                                help="Pullback मध्ये MOM, EMA9, HMA असे वेगवान इंडिकेटर आधी उलटतात; म्हणून इथे सर्व 8 मागू नका.")
            m_oi = st.number_input("किमान OI (strike वर)", 0.0, 1e7, 1000.0, 500.0)
            m_spread = st.slider("कमाल bid-ask spread %", 3, 40, 15)
        tf_min = int(st.selectbox("⏱ Candle (मिनिटे)", [5, 3, 1], 0,
                                  help="लहान candle = सिग्नल लवकर पण जास्त खोटे. 3 किंवा 1 मिनिट Dhan/Tradehull ने समर्थित असावे (तपासलेले नाही). "
                                       "चुकले तर 'Candle डेटा त्रुटी' दिसेल; 5 वर परत या."))
        mode = st.selectbox("🎚 सिग्नल कडकपणा", ["कडक (कमी, चांगले)", "मध्यम", "सैल (जास्त, कमजोर)"], 0,
                            help="कडक: ≥2 ताजे cross, तिन्ही एका बाजूला, volume 1.2x. मध्यम: 1 ताजा cross, 2/3 एका बाजूला, volume 1.0x. "
                                 "सैल: मध्यम + volume बंद + cross 4 candle पर्यंत. मध्यम/सैल निवडल्यास खालचे काही फिल्टर यांनी बदलले जातात. "
                                 "Journal मध्ये प्रत्येक entry चा कडकपणा नोंदला जातो, म्हणून कोणता चांगला ते 30+ निकालांनंतर पाहता येते.")
        min_agree = int(st.selectbox("🤝 सहमती: किमान किती इंजिन एकमत", [2, 3, 4], 1,
                                     help="चार इंजिन: चार्ट, OI flow, CAS, V40. 2 = सैल (आजचे सर्व तोट्याचे entries 2/4 होते). "
                                          "3 = कडक (शिफारस), 4 = अतिकडक (क्वचित). विरोधात कुणी नसावे ही अट कायम."))
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

    if mode.startswith("मध्यम"):
        cfg = replace(cfg, min_crosses=1, min_aligned=2, vol_min=1.0)
    elif mode.startswith("सैल"):
        cfg = replace(cfg, min_crosses=1, min_aligned=2, use_volume=False, cross_n=max(cfg.cross_n, 4))
    mode_name = mode.split(" ")[0]

    if "journal" not in st.session_state:
        try:
            st.session_state["journal"] = json.loads(JOURNAL.read_text())
        except Exception:
            st.session_state["journal"] = []
    if "siglog" not in st.session_state:
        try:
            st.session_state["siglog"] = json.loads(SIGLOG.read_text())
        except Exception:
            st.session_state["siglog"] = []
    if "missed" not in st.session_state:
        try:
            st.session_state["missed"] = json.loads(MISSLOG.read_text())
        except Exception:
            st.session_state["missed"] = []
    st.session_state.setdefault("spot_hist", {})
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
        resp = d_api.option_chain(under_security_id=sec_id, under_exchange_segment=meta["seg"], expiry=expiry)
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

        # ───────── Stock scanner (1 DAY candle, F&O stocks) ─────────
        with st.expander("📊 Stock scanner (1 DAY candle · F&O stocks)", expanded=False):
            st.caption("Monthly HOLD चाच फॉर्म्युला (KST, DMI, MOM, MACD, ALMA, HMA, VI, EMA9) रोजच्या candle वर. Strike इथे नाही; उमेदवार स्टॉक्स मिळतात. बॅकटेस्ट नाही.")
            _stx = st.text_area("Stocks (स्वल्पविराम किंवा नवी ओळ)", ", ".join(STOCK_LIST), height=90, key="ss_txt")
            _c1, _c2, _c3, _c4 = st.columns(4)
            _sw = _c1.number_input("खिडकी (दिवस)", 2, 60, 10, key="ss_w")
            _sa = _c2.number_input("किमान एकमत /8", 4, 8, 7, key="ss_a")
            _sf = _c3.number_input("किमान ताजे cross", 0, 8, 5, key="ss_f")
            _sx = _c4.number_input("ADX किमान", 10.0, 40.0, 20.0, 1.0, key="ss_x")
            _sall = st.checkbox("WAIT सुद्धा दाखवा", False, key="ss_all")
            if st.button("🔍 स्कॅन करा (1 DAY)", key="ss_go"):
                _syms = [x.strip().upper() for x in _stx.replace("\n", ",").split(",") if x.strip()]
                _bar, _rows, _fail = st.progress(0.0), [], []
                for _i, _sy in enumerate(_syms, 1):
                    try:
                        _raw = tsl.get_historical_data(_sy, "NSE", "DAY")
                        _r = scan_stock_daily(_raw, now, int(_sw), int(_sa), int(_sf), float(_sx))
                        _r["symbol"] = _sy
                        _rows.append(_r)
                    except Exception as _e:
                        _fail.append(f"{_sy}: {str(_e)[:80]}")
                    _bar.progress(_i / max(1, len(_syms)))
                    time.sleep(1.0)
                st.session_state["stock_scan"] = {"rows": _rows, "fail": _fail, "t": now}
            _res = st.session_state.get("stock_scan")
            if _res:
                _pr = {"PULLBACK READY": 0, "FRESH": 1, "LATE": 2, "WAIT": 3}
                _show = [r for r in _res["rows"] if _sall or r["status"] != "WAIT"]
                _show.sort(key=lambda r: (_pr.get(r["status"], 9), -r["aligned"], -r["adx"]))
                st.caption(f"निकाल {_res['t']:%d-%b %H:%M} · {len(_res['rows'])} पैकी {len(_show)} दाखवले")
                if _show:
                    _sd = pd.DataFrame(_show)[["symbol", "status", "direction", "aligned", "adx", "move_atr", "level", "close", "note"]]
                    st.dataframe(_sd.round(2), hide_index=True, use_container_width=True)
                    st.download_button("⬇ CSV", _sd.to_csv(index=False).encode("utf-8-sig"), "stock_scan.csv", "text/csv", key="ss_dl")
                else:
                    st.info("एकही stock अटीत बसत नाही.")
                if _res["fail"]:
                    st.warning("तुटलेले (symbol/timeframe तपासा): " + "; ".join(_res["fail"][:10]))

        if meta["id"] <= 0:
            st.info("कमोडिटी: sidebar मध्ये 'Underlying security id' (चालू महिन्याचे FUTURE चे Dhan id) भरा, मग हे सुरू होईल.")
            return

        # 1) Expiry (dynamic)
        try:
            exp_list = cached(f"exp_{sym}", 600, lambda: extract_dates(
                d_api.expiry_list(under_security_id=meta["id"], under_exchange_segment=meta["seg"])))
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
        spot_note = ""
        if meta.get("custom"):                       # commodity: chain cha spot dusrya mahinyacha future asu shakto -> option bhavatun khara spot
            imp_ = implied_spot(oc)
            if imp_ and spot and abs(imp_ / spot - 1) > 0.005:
                spot_note = (f"ℹ️ Chain चा underlying {spot:,.1f} आहे, पण option भावांवरून (put-call parity) खरा underlying {imp_:,.1f} निघतो "
                             f"(फरक {abs(imp_ / spot - 1) * 100:.1f}%). ATM आणि strike निवडीसाठी {imp_:,.1f} वापरला आहे.")
                spot = imp_
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
            raw = cached(f"h_{sym}_{tf_min}", 20, lambda: tsl.get_historical_data(meta["csym"], meta["cexch"], str(tf_min)))
        except Exception as e:
            st.error(f"Candle डेटा त्रुटी: {e}")
            return
        df, note = drop_forming_candle(raw, tf_min, now)
        sig = evaluate_signal(df, cfg)
        cont = continuation_signal(df, cfg) if run_cont else {"direction": None, "checks": []}
        # Chukleli chaal nondni: spot 30 min madhe motha halla ani konihi sanket dila nahi -> tya veli kay ada hota te nondva
        _h = st.session_state["spot_hist"].setdefault(sym, [])
        _h.append((now_ts, float(spot)))
        del _h[:max(0, len(_h) - 400)]
        _gap_block = bool(sig["close"] and spot and abs(spot / sig["close"] - 1) > meta.get("tol", 1.5) / 100)
        _mm = missed_move(_h, st.session_state["siglog"], now_ts, sym, spot, 1.2 if meta.get("custom") else 0.4)
        if _mm and not any(r["sym"] == sym and now_ts - r["t"] < 60 * 60 for r in st.session_state["missed"]):
            st.session_state["missed"].append({
                "t": now_ts, "sym": sym, "expiry": expiry, "dir": _mm["dir"], "mv_pct": _mm["mv"], "exp_day": (expiry == today),
                "spot_gap_block": _gap_block, "chart_dir": sig.get("direction"), "cont_dir": cont.get("direction"), "leaning": sig.get("leaning"),
                "failed": "; ".join(c[0] + " → " + str(c[2]) for c in sig.get("checks", []) if not c[1]),
                "flow_dir": flow_dir, "mode": mode_name})
            try:
                MISSLOG.write_text(json.dumps(st.session_state["missed"]))
            except Exception:
                pass
        if _gap_block:
            st.error(f"⚠ Chain चा spot ({spot:,.0f}) आणि चार्टचा भाव ({sig['close']:,.0f}) जुळत नाहीत "
                     f"(फरक {abs(spot / sig['close'] - 1) * 100:.1f}%, परवानगी {meta.get('tol', 1.5):.1f}%). "
                     f"Security id ({meta['id']}) {sym} चा आहे का तपासा. कमोडिटी असेल तर option चा future महिना आणि चार्टचा महिना वेगळा असू शकतो; "
                     f"sidebar मध्ये फरकाची मर्यादा वाढवा. निर्णय थांबवला.")
            return

        # 4) Nirnay
        reasons, opt, plan = [], None, None
        direction = sig["direction"]
        rel = flow_relation(flow_dir, direction) if direction in ("UP", "DOWN") else "none"
        if not in_trade_window(now, cfg.window_start, cfg.window_end):
            reasons.append(f"ट्रेड वेळेबाहेर ({cfg.window_start}–{cfg.window_end}, सोम–शुक्र)")
        if direction == "WAIT":
            failed = [c[0] for c in sig["checks"] if not c[1]]
            reasons.append("बाकी अटी: " + "; ".join(failed[:4]) if failed else "सर्व अटी एकत्र जुळल्या नाहीत")
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
            tr_ = new_trade(sym, direction, expiry, opt, plan, now_ts, sig["candle"], rel)
            tr_["mode"] = mode_name
            st.session_state["journal"].append(tr_)
            ok = send_telegram(fmt_alert(sym, direction, expiry, opt, plan, sig, flow_txt))
            st.toast("Telegram: " + ("पाठवला" if ok else "पाठवला नाही (secrets तपासा)"))

        eod = now.time() >= eod_time(cfg.window_end)
        for t in st.session_state["journal"]:
            if t["sym"] == sym and t["expiry"] == expiry and t["status"] in OPEN_STATES:
                update_trade(t, chain_ltp(oc, t["strike"], t["side"]), now_ts, eod, cfg.max_trade_minutes)
        save_journal()

        # ───────── UI ─────────
        if spot_note:
            st.info(spot_note)
        st.caption(f"{now:%d-%b %H:%M:%S} IST · Spot {spot:,.1f} · {note} · ATR {sig['atr']:.1f} · "
                   f"ADX {sig['adx']:.0f} · RSI {sig['rsi']:.0f}")
        top = st.container()          # sahmati nirnay ithe (V40 nantar bharto)
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
            if run_cont and cont["checks"]:
                with st.expander("🔁 Trend continuation / pullback (प्रायोगिक): " + (
                        {"UP": "🟢 UP मत", "DOWN": "🔴 DOWN मत"}[cont["direction"]] if cont["direction"] else "मत नाही")):
                    st.dataframe(pd.DataFrame([{"": "✅" if ok else "❌", "अट": n, "आत्ता": d} for n, ok, d in cont["checks"]]),
                                 hide_index=True, use_container_width=True)
                    st.caption("ताजा cross नसताना मजबूत ट्रेंडमध्ये pullback नंतर पुन्हा पुढे जाताना हे मत येते. एकटा entry नाही; सहमतीत एक मत आणि tracker मध्ये "
                               "'CHART CONT (pullback)' म्हणून नोंद. प्रयोगात्मक, backtest नाही.")
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
            st.caption("ℹ️ 'Strike' स्तंभ = जिथे हालचाल दिसली (पुरावा, तो खरेदी करायचा नाही). घ्यायचा नेमका strike खालच्या 📍 बॉक्समध्ये येतो (ATM किंवा तुमच्या निवडीनुसार).")
            mv = spot_move_pct(st.session_state["flow_store"], cfg.flow_window_min, sym)
            st.caption(f"spot ची हालचाल (शेवटच्या {cfg.flow_window_min:.0f} मि): "
                       + (f"{mv:+.2f}% (~{mv / 100 * spot:+.0f} पॉइंट)" if mv is not None else "इतिहास जमतोय")
                       + " · Premium 'spot वगळून' = कच्चा premium − (delta × spot बदल). दोन्ही सारखे असतील तर spot ने फारसा हललाच नाही.")
            _dr = st.session_state["flow_store"].get("__flow_dropped__") or []
            if _dr:
                with st.expander(f"🛡 बाजूला ठेवलेले संकेत ({len(_dr)}): डेटा संशयास्पद किंवा spot उलट दिशेने"):
                    for _x in _dr[:15]:
                        st.write("• " + _x)
            st.dataframe(pd.DataFrame([{
                "Strike": f"{x['strike']:.0f} {x['side'].upper()}", "दिशा": x["direction"], "Score": x["score"],
                "संकेत (घ्यायचा option)": ("PE" if x["direction"] == "DOWN" else "CE") + f" बाजूला, ATM {atm:.0f} जवळ",
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
            _dr = st.session_state["flow_store"].get("__flow_dropped__") or []
            if _dr:
                with st.expander(f"🛡 बाजूला ठेवलेले संकेत ({len(_dr)}): डेटा संशयास्पद किंवा spot उलट दिशेने"):
                    for _x in _dr[:15]:
                        st.write("• " + _x)

        st.subheader("🟡 CAS: writers चा radar, move च्या आधी (फक्त माहिती)")
        cas_list = cas_detect(st.session_state["flow_store"], sym, expiry, oc, spot, cfg, meta["step"])
        # Signal tracker: sagle engines che sanket nondva ani jhalelyancha nikal tapasa (fakt maahiti, order nahi)
        SL_ = st.session_state["siglog"]
        _exp_day = (expiry == today)
        if sig["direction"] in ("UP", "DOWN"):
            siglog_add(SL_, now_ts, sym, expiry, "CHART", sig["direction"], None, None, spot, None, _exp_day)
        if cont["direction"]:
            siglog_add(SL_, now_ts, sym, expiry, "CHART CONT (pullback)", cont["direction"], None, None, spot, None, _exp_day)
        if enter:
            siglog_add(SL_, now_ts, sym, expiry, "ENTER (sahmati)", direction, opt["strike"], opt["side"], spot, None, _exp_day)
        if flow_ok and flow_best:
            siglog_add(SL_, now_ts, sym, expiry, "OI FLOW", flow_best["direction"], flow_best["strike"], flow_best["side"], spot,
                       flow_best.get("score"), _exp_day)
        for c_ in cas_list:
            if c_["stage"] in ("EARLY", "STRONG"):
                siglog_add(SL_, now_ts, sym, expiry, "CAS " + c_["stage"], "UP" if c_["bias"] == "BULLISH" else "DOWN",
                           c_["strike"], c_["side"], spot, c_["score"], _exp_day)
        siglog_update(SL_, now_ts, sym, spot)
        try:
            SIGLOG.write_text(json.dumps(SL_))
        except Exception:
            pass
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

        with st.expander("📊 Signal tracker: कोणता engine किती वेळा बरोबर? (spot च्या दिशेने 0.15%, 60 मिनिटांत)"):
            summ = siglog_summary(st.session_state["siglog"])
            n_open = sum(1 for r in st.session_state["siglog"] if r["status"] == "OPEN")
            if summ:
                st.dataframe(pd.DataFrame(summ), hide_index=True, use_container_width=True)
            else:
                st.caption("अजून निकाल नाही (सिग्नल आल्यानंतर 60 मिनिटांपर्यंत ॲप चालू ठेवा).")
            st.caption(f"चालू (निकाल बाकी): {n_open}. HIT = सिग्नलच्या दिशेने spot +{SIG_X}% आधी गेला; MISS = उलट -{SIG_X}% आधी; "
                       f"NO MOVE = {SIG_HORIZON_MIN} मिनिटांत दोन्ही नाही. हे फक्त spot वर आधारित आहे (option premium, theta, SL धरलेले नाही). "
                       "Refresh च्या मधला wick चुकू शकतो. 30+ सिग्नल होईपर्यंत टक्केवारीवर विश्वास ठेवू नका. Streamlit Cloud वर फाईल रीस्टार्टवर पुसली जाते, म्हणून CSV उतरवून ठेवा.")
            if st.session_state["missed"]:
                st.markdown("**⚠ चुकलेल्या चाली** (spot 30 मिनिटांत खूप हलला आणि कुणीही आधी सिग्नल दिला नाही; तेव्हा कोणत्या अटी अपयशी होत्या)")
                _md = pd.DataFrame(st.session_state["missed"])
                _md["वेळ"] = pd.to_datetime(_md["t"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.strftime("%d-%b %H:%M")
                st.dataframe(_md[["वेळ", "sym", "dir", "mv_pct", "spot_gap_block", "chart_dir", "flow_dir", "failed", "exp_day"]].iloc[::-1],
                             hide_index=True, use_container_width=True)
                st.download_button("⬇ चुकलेल्या चाली CSV", _md.drop(columns=["वेळ"]).to_csv(index=False).encode(), "missed_moves.csv", "text/csv")
            if st.session_state["siglog"]:
                _df = pd.DataFrame(st.session_state["siglog"])
                _df["वेळ"] = pd.to_datetime(_df["t"], unit="s", utc=True).dt.tz_convert("Asia/Kolkata").dt.strftime("%d-%b %H:%M")
                st.dataframe(_df[["वेळ", "sym", "engine", "dir", "strike", "side", "spot0", "exp_day", "status", "result", "fav", "adv"]].iloc[::-1],
                             hide_index=True, use_container_width=True)
                st.download_button("⬇ CSV उतरवा", _df.drop(columns=["वेळ"]).to_csv(index=False).encode(), "signal_log.csv", "text/csv")

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
            by_mode = {}
            for t in J:
                by_mode.setdefault(t.get("mode", "—"), []).append(t)
            if len(by_mode) > 1:
                st.caption("कडकपणानुसार निकाल (जिंकले = T1 लागला, हरले = SL):")
                st.dataframe(pd.DataFrame([{"कडकपणा": k, "एकूण": len(v), "जिंकले": summarize(v)["wins"], "हरले": summarize(v)["losses"],
                                            "Win rate %": None if summarize(v)["win_rate"] is None else round(summarize(v)["win_rate"])}
                                           for k, v in by_mode.items()]), hide_index=True, use_container_width=True)
            rows = [{"वेळ": datetime.fromtimestamp(t["t"], IST).strftime("%d-%b %H:%M"), "Index": t["sym"],
                     "Option": f"{t['strike']:.0f} {t['side'].upper()}", "Expiry": t["expiry"], "Entry": t["entry"],
                     "आत्ता": t["last"], "SL": t["sl"], "T1": t["t1"], "T2": t["t2"], "Lots": t["lots"],
                     "OI flow": {"agree": "✅", "oppose": "⚠", "none": "—"}.get(t.get("flow", "none"), "—"),
                     "कडकपणा": t.get("mode", "—"),
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

        v34_win = None
        if run_v34_on:
            st.divider()
            v34_file = Path(__file__).with_name("v34_engine.py")
            if meta.get("custom"):
                st.info("ℹ️ V34 index (SENSEX / NIFTY) साठी आहे, कमोडिटीसाठी नाही.")
            elif not v34_file.exists():
                st.warning("v34_engine.py सापडली नाही (samadhan_terminal.py च्या शेजारी ठेवा).")
            else:
                ctx34 = (sym, expiry)
                if st.session_state.get("v34_ctx") != ctx34:            # expiry/index badalla = V34 chi memory navi
                    for k_ in ("trend_history", "previous_direction", "premium_history", "delta_history", "stage3_counter", "leader_lock_history"):
                        st.session_state.pop(k_, None)
                    st.session_state["v34_ctx"] = ctx34
                g34 = {"__name__": "v34_engine", "st": st, "oc": oc, "spot": spot, "df_5": df, "main_sym": sym}
                with st.expander("🏆 V34 Sensex desk (तुमचा कोड, जसा आहे तसा)", expanded=True):
                    try:
                        exec(compile(v34_file.read_text(encoding="utf-8"), str(v34_file), "exec"), g34)
                    except Exception as e_:
                        st.error(f"V34 इंजिन त्रुटी: {e_}")
                v34_win = v34_winner(g34)
                if v34_win:
                    _q = g34.get("market_bias")          # V34 cha 5-scan queue kal: CALL / PUT / NEUTRAL
                    _rel = ("queue तटस्थ" if _q not in ("CALL", "PUT") else
                            ("queue सोबत" if (_q == "CALL") == (v34_win["dir"] == "UP") else "queue उलट"))
                    _tgk = f"v34_tg_{sym}_{v34_win['dir']}"
                    if now_ts - st.session_state.get(_tgk, 0) > 15 * 60:        # 15 min madhe tyach dishecha duhera Telegram nako
                        st.session_state[_tgk] = now_ts
                        _ok = send_telegram(f"🏆 V34 WINNER · {sym} {v34_win['option']} ({'CALL ↑' if v34_win['dir'] == 'UP' else 'PUT ↓'})\n"
                                            f"{v34_win['stage']} · rank {v34_win['score']:.0f}% · premium ₹{(v34_win['premium'] or 0):.2f} · "
                                            f"{v34_win['alloc']} ({v34_win['action']})\n"
                                            f"Spot {spot:,.1f} · Expiry {expiry} · {_rel} · {now:%H:%M}\n"
                                            "फक्त माहिती; order आपोआप जात नाही. सल्ला नाही.")
                        st.toast("V34 Telegram: " + ("पाठवला" if _ok else "पाठवला नाही (secrets तपासा)"))
                    siglog_add(SL_, now_ts, sym, expiry, f"V34 WINNER ({_rel})", v34_win["dir"], v34_win["strike"], v34_win["side"],
                               spot, v34_win["score"], _exp_day)
                    siglog_update(SL_, now_ts, sym, spot)
                    try:
                        SIGLOG.write_text(json.dumps(SL_))
                    except Exception:
                        pass

        # ───────── Monthly HOLD (वेगळा expiry, वेगळे इंडिकेटर; सहमतीत मिसळत नाही) ─────────
        if run_month:
            st.divider()
            with st.expander("📅 Monthly HOLD इंजिन", expanded=True):
                m_list = monthly_expiries(exp_list) or exp_list
                if not m_list:
                    st.info("Expiry यादी मिळाली नाही.")
                else:
                    m_exp = st.selectbox("Monthly expiry (वेगळी तारीख)", m_list, 0, key=f"mexp_{sym}")
                    _tfmap = {"15 मिनिट": ("15", 15, 25), "60 मिनिट": ("60", 60, 6), "1 DAY": ("DAY", 1440, 10)}
                    mdf, mwin, m_tf_txt = df, m_cross_n, f"{tf_min} मिनिटे"
                    if m_tf in _tfmap:
                        _code, _min, mwin = _tfmap[m_tf]
                        try:
                            _raw = cached(f"hm_{sym}_{_code}", 120, lambda: tsl.get_historical_data(meta["csym"], meta["cexch"], _code))
                            mdf, _n = drop_forming_candle(_raw, _min, now)
                            m_tf_txt = m_tf
                        except Exception as e_:
                            st.error(f"Monthly candle ({m_tf}) डेटा त्रुटी: {e_}. चार्टचाच candle वापरतो आहे.")
                            mdf, mwin, m_tf_txt = df, m_cross_n, f"{tf_min} मिनिटे (म्हणजे {m_tf} मिळाले नाही)"
                    msig = monthly_hold_signal(mdf, mwin, m_min_al, m_fresh, float(m_adx))
                    st.caption(f"{sym} · candle {m_tf_txt} · खिडकी {mwin} · झुकाव {msig['leaning']} · {msig['aligned']}/8 एकाच बाजूला · ताजे cross {msig.get('fresh', 0)}")
                    if msig.get("note"):
                        st.info(msig["note"])
                    if msig["checks"]:
                        st.dataframe(pd.DataFrame([{"": "✅" if ok_ else "❌", "इंडिकेटर / अट": n_, "आत्ता": d_} for n_, ok_, d_ in msig["checks"]]),
                                     hide_index=True, use_container_width=True)
                    m_strike_txt, m_reason = "—", "सर्व अटी जुळल्या नाहीत (WAIT)"
                    pb = monthly_pullback(mdf, mwin, m_min_al, float(m_adx), m_retr / 100.0, m_pb_al)
                    m_late_flag = bool(pb and pb["move_atr"] > m_late)
                    entry_dir, entry_kind = None, None
                    if msig["direction"] in ("UP", "DOWN") and not m_late_flag:
                        entry_dir, entry_kind = msig["direction"], "FRESH"
                    elif m_late_flag and pb["ready"]:
                        entry_dir, entry_kind = pb["direction"], "PULLBACK"
                    if m_late_flag:
                        arrow = "↑" if pb["direction"] == "UP" else "↓"
                        st.warning(f"⏭ मूव्ह निघून गेला ({pb['direction']} {arrow}, {pb['move_atr']:.1f} ATR, {pb['bars_ago']} candle आधी सर्व इंडिकेटर जुळले होते). "
                                   f"नवी entry/strike दाखवत नाही. Pullback पातळी: spot **{pb['level']:,.1f}** "
                                   f"({m_retr}% परत). आत्ता spot {spot:,.1f}, {pb['aligned_now']}/8 इंडिकेटर अजून एकाच बाजूला. "
                                   + ("✅ pullback आला आहे, खाली entry दिसेल." if pb["ready"] else
                                      ("⚠ भाव मूव्हच्या सुरुवातीच्या पातळीच्या आत गेला (setup बिघडला)." if not pb["held"] else "⏳ pullback ची वाट पाहा.")))
                    if m_late_flag:
                        m_reason = ("मूव्ह निघून गेला; pullback आला (entry खाली)" if pb["ready"]
                                    else f"मूव्ह निघून गेला ({pb['move_atr']:.1f} ATR); strike लपवला; pullback spot {pb['level']:,.1f} ची वाट")
                    elif msig["direction"] in ("UP", "DOWN"):
                        m_reason = "FRESH सिग्नल"
                    if entry_dir:
                        try:
                            mch = cached(f"c_{sym}_{m_exp}", 20, lambda: fetch_chain(d_api, meta["id"], m_exp))
                            mopt = (pick_by_delta(mch["oc"], entry_dir, float(m_dlo), float(m_dhi), spot, meta["step"], float(m_oi), float(m_spread))
                                    if m_mode.startswith("Delta") else
                                    pick_by_premium(mch["oc"], entry_dir, float(m_target), spot, float(m_oi), float(m_spread)))
                        except Exception as e_:
                            mopt = None
                            m_reason = f"Monthly chain त्रुटी: {e_}"
                            st.error(f"Monthly option chain त्रुटी: {e_}")
                        if mopt:
                            mp = month_plan(mopt, int(lot_size), float(capital), float(risk_pct), float(m_sl), float(m_t1), float(m_t2))
                            kind = "CALL (CE)" if entry_dir == "UP" else "PUT (PE)"
                            for w_ in mopt.get("warn", []):
                                st.warning("⚠ " + w_)
                            for i_ in mopt.get("issues", []):
                                st.warning("Option: " + i_)
                            if mp["ok"] and mopt["ok"]:
                                m_strike_txt = f"{mopt['strike']:.0f} {kind} · Entry ~₹{mp['entry']} · SL ₹{mp['sl']} · T1 ₹{mp['t1']} · {mp['lots']} lot"
                                m_reason = f"{entry_kind} ENTRY"
                                st.success(f"📅 MONTHLY ENTRY ({entry_kind}): {sym} {mopt['strike']:.0f} {kind} · expiry {m_exp} · Entry ~₹{mp['entry']} · "
                                           f"SL ₹{mp['sl']} · T1 ₹{mp['t1']} (अर्धे विका) · T2 ₹{mp['t2']} · {mp['lots']} lot · "
                                           f"कमाल तोटा ₹{mp['max_loss']:.0f}")
                                _mk = f"month_tg_{sym}_{msig['direction']}"
                                if (in_trade_window(now, cfg.window_start, cfg.window_end)
                                        and now_ts - st.session_state.get(_mk, 0) > 60 * 60):
                                    st.session_state[_mk] = now_ts
                                    _ok = send_telegram(f"📅 MONTHLY {entry_kind} · {sym} {mopt['strike']:.0f} {kind} (expiry {m_exp})\n"
                                                        f"Entry ~₹{mp['entry']} · SL ₹{mp['sl']} · T1 ₹{mp['t1']} · T2 ₹{mp['t2']} · {mp['lots']} lot · "
                                                        f"कमाल तोटा ₹{mp['max_loss']:.0f}\nSpot {spot:,.1f} · {now:%H:%M}\n"
                                                        f"{msig['aligned']}/8 इंडिकेटर एकमत. फक्त माहिती; order आपोआप जात नाही. सल्ला नाही.")
                                    st.toast("Monthly Telegram: " + ("पाठवला" if _ok else "पाठवला नाही (secrets तपासा)"))
                                    siglog_add(SL_, now_ts, sym, m_exp, f"MONTHLY {entry_kind}", entry_dir, mopt["strike"], mopt["side"],
                                               spot, float(msig["aligned"]), _exp_day)
                                    try:
                                        SIGLOG.write_text(json.dumps(SL_))
                                    except Exception:
                                        pass
                            else:
                                m_strike_txt = f"{mopt['strike']:.0f} {kind} (भाव ₹{mopt['ltp']:g}) — योजना नाही"
                                m_reason = "सिग्नल आहे, पण entry योजना नाही: " + (mp["note"] or "; ".join(mopt.get("issues", [])) or "option अटी अपूर्ण")
                                st.info(m_reason)
                    elif not m_late_flag:
                        st.caption("सर्व अटी जुळल्यावरच entry दिसेल. आठही एकत्र जुळणे दुर्मिळ आहे; हवे तर सेटिंगमध्ये 7/8 किंवा 6/8 करा.")
                    st.dataframe(pd.DataFrame([
                        {"बाब": "Symbol / candle", "माहिती": f"{sym} · {m_tf_txt} · खिडकी {mwin} · spot {spot:,.1f}"},
                        {"बाब": "Monthly expiry", "माहिती": str(m_exp)},
                        {"बाब": "झुकाव / एकमत", "माहिती": f"{msig['leaning']} · {msig['aligned']}/8"},
                        {"बाब": "मूव्ह (ATR)", "माहिती": f"{pb['move_atr']:.1f} ({pb['direction']}, {pb['bars_ago']} candle आधी)" if pb else "—"},
                        {"बाब": "स्थिती", "माहिती": m_reason},
                        {"बाब": "Strike", "माहिती": m_strike_txt}]), hide_index=True, use_container_width=True)
                    st.caption("प्रायोगिक; backtest नाही. ₹10 चा monthly strike खूप लांबचा असतो, तो शून्यही होऊ शकतो. आर्थिक सल्ला नाही.")

        # ───────── सहमती: चार्ट + OI flow + CAS + V40 (+V34) ─────────
        v40_vs = st.session_state.get("v40_last_signal") if run_v40_on else None
        v40_act = bool(v40_vs and v40_vs.get("scan") == st.session_state.get("v40_scan_count"))
        cas_strong = next((c for c in cas_list if c["stage"] == "STRONG"), None)
        votes = {"चार्ट": chart_vote(sig) or cont["direction"],
                 "OI flow": flow_dir,
                 "CAS": (None if not cas_strong else ("UP" if cas_strong["bias"] == "BULLISH" else "DOWN")),
                 "V40": (("UP" if v40_vs["type"] == "CE" else "DOWN") if v40_act else None)}
        if run_v34_on and not meta.get("custom"):
            votes["V34"] = v34_win["dir"] if v34_win else None
        n_eng = len(votes)
        c_dir, c_agree, c_opp = consensus(votes, min_agree)
        with top:
            vrow = {k: {"UP": "🟢 UP", "DOWN": "🔴 DOWN", None: "—"}[v] for k, v in votes.items()}
            if c_dir != "WAIT":
                copt = pick_option(oc, spot, c_dir, meta["step"], cfg, itm_steps=1 if strike_mode.startswith("1") else 0)
                cplan = trade_plan(copt, sig["atr"], cfg, int(lot_size), float(capital), float(risk_pct)) if copt["ok"] else None
                if cplan and cplan["ok"]:
                    side_t = "CALL (CE)" if c_dir == "UP" else "PUT (PE)"
                    st.success(f"🎯 **सहमती {len(c_agree)}/{n_eng}: {side_t} खरेदी · {sym} {copt['strike']:.0f}** · Entry ~₹{cplan['entry']} · "
                               f"SL ₹{cplan['sl']} · T1 ₹{cplan['t1']} · T2 ₹{cplan['t2']} · {cplan['lots']} lot · "
                               f"कमाल तोटा ₹{cplan['max_loss']:.0f}\n\nसहमत: {', '.join(c_agree)}"
                               + (f" · विरोधी: {', '.join(c_opp)}" if c_opp else ""))
                    if (in_trade_window(now, cfg.window_start, cfg.window_end)
                            and cooldown_ok(st.session_state["journal"], sym, c_dir, now_ts, cfg.cooldown_min, "cons-" + sig["candle"])):
                        tr2 = new_trade(sym, c_dir, expiry, copt, cplan, now_ts, "cons-" + sig["candle"], "agree" if flow_dir == c_dir else "none")
                        tr2["mode"] = f"सहमती {len(c_agree)}/{n_eng}"
                        st.session_state["journal"].append(tr2)
                        save_journal()
                        send_telegram(f"🎯 {sym} सहमती {len(c_agree)}/{n_eng} ({', '.join(c_agree)}): {side_t} {copt['strike']:.0f} | "
                                      f"Entry {cplan['entry']} SL {cplan['sl']} T1 {cplan['t1']} T2 {cplan['t2']} | {cplan['lots']} lot, "
                                      f"कमाल तोटा ₹{cplan['max_loss']:.0f}. सल्ला नाही.")
                else:
                    st.warning(f"सहमती {c_dir} ({', '.join(c_agree)}), पण option/योजना बनत नाही: "
                               + ("; ".join(copt.get("issues", [])) if not copt["ok"] else (cplan or {}).get("note", "")))
            else:
                st.info(f"⏳ **सहमती नाही**: किमान {min_agree} इंजिन एकाच दिशेला आणि कुणी विरोधात नसावे लागते.")
            st.dataframe(pd.DataFrame([vrow]), hide_index=True, use_container_width=True)
            st.caption("चार्ट = पूर्ण सिग्नल किंवा फक्त 1 अट बाकी · OI flow = 2 मिनिटे टिकलेला · CAS = STRONG आणि LATE नाही · V40 = आत्ता सक्रिय सिग्नल · V34 = रँक ≥ 65 चा WINNER. "
                       "जास्त इंजिन सहमत म्हणजे जास्त खात्रीशीर असे सिद्ध नाही; Journal मध्ये 'सहमती 2/4', '3/4' चे निकाल वेगळे पाहून ठरवा.")

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
