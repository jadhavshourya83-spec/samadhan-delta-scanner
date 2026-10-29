# ==============================================================================
# SAMADHAN TERMINAL V40.2 - SIMPLIFIED TRAP-PROTECTED OI ENGINE
# ==============================================================================
# Improved version of V40.1. Same flow:
#   OI UNWINDING -> VOLUME -> PREMIUM -> DELTA -> TRAP -> PERSISTENCE
#   -> CE/PE BATTLE -> ENTRY
# Difference: fewer conditions, no contradicting rules, and reruns no longer corrupt the data.
#
# Expected globals from the main app:
#   OptionChain : Dhan option-chain response
#   spot        : live underlying spot
#   df_5        : optional fallback dataframe with "close"
#   expiry      : (optional) expiry date string "YYYY-MM-DD" - used for the delta fallback
# ==============================================================================

import datetime
import math
import os

import pandas as pd
import streamlit as st

# ==============================================================================
# CONFIGURATION  (change all numbers here)
# ==============================================================================

SCAN_STRIKES_EACH_SIDE = 8     # strikes to scan above and below ATM
WARMUP_SCANS = 6               # minimum scans before a signal is counted (was 20)
VOLUME_WINDOW = 20             # previous scans for average volume (20 scans = 10 min at 30s)
MEMORY_LIMIT = 120

# --- 4 GATES (all four must pass) ---
UNWIND_OI_MIN = 1.5            # OI must fall by at least this many % over the lookback window below
OI_LOOKBACK_SCANS = 4          # OI change is measured over the last N scans (4 x 30s = 2 min)
                               # because Dhan OI does not refresh on every scan.
                               # set to 1 to measure only scan-to-scan (old behaviour)
VOLUME_RATIO_MIN = 1.8         # new volume / average volume
MIN_NEW_VOLUME = 500           # AND the new volume in one scan must be at least this many units
                               # (blocks fake spikes on thin strikes; set ~20-30% of a normal 30s volume)
# premium must be rising (> 0%)
DELTA_MIN = 0.10               # was 0.15-0.20 (too narrow)
DELTA_MAX = 0.30
DELTA_IDEAL = 0.20

# --- CONFIRMATION ---
PERSIST_WINDOW = 3             # look at the last N scans
PERSIST_REQUIRED = 2           # scans (out of the window) in which all gates must pass
                               # set PERSIST_WINDOW = PERSIST_REQUIRED for strict "consecutive" mode

# --- SCORE: value that earns full points (tuned for 30s scans) ---
SCORE_FULL_OI = 4.0            # % OI unwinding (over the window) for full OI points
SCORE_FULL_VOL = 3.0           # x volume ratio for full volume points
SCORE_FULL_PREM = 2.0          # % premium rise for full premium points

# --- FINAL ENTRY ---
MIN_ENTRY_SCORE = 55.0         # score is now scaled to the gates (was 70)
MIN_GAP = 10.0                 # minimum score gap between CE and PE
SIDE_FLIP_GAP = 15.0           # minimum gap needed to flip sides
SIDE_MEMORY_SCANS = 15         # old "confirmed side" is forgotten after this many scans
SIGNAL_MEMORY_SCANS = 10       # keep showing the "last signal" for this many scans (10 x 30s = 5 min)

# --- EXTRA CONFIRMATIONS (soft: they change the score, they never block on their own) ---
SPOT_LOOKBACK_SCANS = 4        # spot move is measured over the last N scans
SPOT_MIN_MOVE_PCT = 0.02       # % of spot; a smaller move counts as FLAT
SPOT_BONUS = 10.0              # score +/- when spot agrees / disagrees (CE wants UP, PE wants DOWN)
OPP_OI_MIN = 0.5               # % rise in the OPPOSITE side's total OI (over the OI window)
OPP_OI_BONUS = 5.0             # score + when the opposite side's writers are building

# --- SPREAD (soft trap flag) ---
MAX_SPREAD_PCT = 5.0           # (ask - bid) / LTP in %, wider than this = MEDIUM trap

# --- SIGNAL WATCH after an entry signal (information only, NOT a stop-loss) ---
WATCH_MAX_SCANS = 40           # stop watching after this many scans (40 x 30s = 20 min)
WATCH_OI_RISE_PCT = 0.5        # warn if OI (over the window) rises by this much
WATCH_DROP_PCT = 10.0          # warn if LTP falls this % below the signal LTP

# --- LOG and ALERT ---
LOG_ENABLED = True             # writes v40_scan_log.csv and v40_decision_log.csv
LOG_DIR = "."                  # folder for the CSV files
# Alerts. Keep passwords and tokens OUT of this file: set them as environment variables before starting streamlit.
TELEGRAM_BOT_TOKEN = os.environ.get("V40_TELEGRAM_TOKEN", "")     # optional: Telegram alert (fast, push on the phone)
TELEGRAM_CHAT_ID = os.environ.get("V40_TELEGRAM_CHAT", "")
EMAIL_HOST = os.environ.get("V40_EMAIL_HOST", "smtp.gmail.com")   # optional: e-mail alert
EMAIL_PORT = int(os.environ.get("V40_EMAIL_PORT", "587"))          # 587 (STARTTLS) or 465 (SSL)
EMAIL_USER = os.environ.get("V40_EMAIL_USER", "")                  # the sending address
EMAIL_PASS = os.environ.get("V40_EMAIL_PASS", "")                  # for Gmail: an APP PASSWORD, not your normal password
EMAIL_TO = os.environ.get("V40_EMAIL_TO", "")                      # where the alert goes (can be the same address)

# --- TRAP (premium moves per 30s scan) ---
TRAP_PREM_JUMP = 5.0           # % premium jump in one scan that needs strong volume
TRAP_PREM_EXTREME = 10.0       # % premium jump in one scan = chase risk

TRAP_MEDIUM_MIN = 30.0
TRAP_HIGH_MIN = 60.0

# --- DELTA FALLBACK (when the API gives no delta) ---
FALLBACK_DAYS_TO_EXPIRY = 3
RISK_FREE = 0.06

# --- FORMULAS: soft score adjustments (they never block a signal on their own) ---
EXCESS_MIN_PCT = 1.0           # excess premium (premium change NOT explained by the spot move), in %
EXCESS_BONUS = 5.0             # score + when excess >= this, - when excess <= minus this
Z_MIN_SAMPLES = 8              # z-score needs at least this much history
Z_MIN = 2.0                    # volume z >= 2 and OI z <= -2 = "unusual for this strike"
Z_BONUS = 5.0

# --- BID/ASK IMBALANCE, MAX PAIN, VOLUME MIGRATION, IV STATE ---
IMB_MIN = 0.30                 # (bid qty - ask qty) / (bid qty + ask qty) beyond +/- this counts as lopsided
IMB_BONUS = 3.0                # score + when buyers dominate the book (and the trend is not falling), - when sellers do
MAXPAIN_NEAR_PCT = 0.10        # max pain within this % of spot counts as "at spot" (no direction)
MIG_RECENT_SCANS = 10          # volume migration: last N scans versus the N scans before
MIG_MIN_SHIFT_STEPS = 0.30     # centre must move this many strike steps to count as UP / DOWN

# --- SECOND PATTERN: fresh build-up (OI RISING with premium and volume rising) ---
ALLOW_LONG_BUILDUP = False     # False = only the OI-unwinding pattern (fewer signals). True = also fresh build-up.
BUILDUP_OI_MIN = 1.5           # OI must rise at least this many % over the OI window for the build-up pattern

# --- EXTRA TRAP FLAGS (each adds TRAP_EXTRA_SCORE; 30 = MEDIUM = WAIT, 60 = HIGH = NO ENTRY) ---
TRAP_EXTRA_SCORE = 30.0        # premium rise is mostly IV / premium rises AGAINST the spot / both CE and PE unwinding

# --- PRICE STRUCTURE (a small, objective part of "smart money" analysis) ---
SMC_SWING_K = 2                # a swing high/low = the extreme of 2 bars on each side
SMC_BOS_LOOKBACK = 6           # a break of structure counts if it happened in the last 6 bars
SMC_SWEEP_LOOKBACK = 3         # a liquidity sweep counts if it happened in the last 3 bars
SMC_MIN_BARS = 12              # minimum bars needed
SMC_BAR_SCANS = 10             # if df_5 is missing, bars are built from 10 scans of spot (5 min at 30s)
SMC_BONUS = 5.0                # score +/- when the price structure agrees / disagrees

# --- 15-MINUTE BIAS (a filter, it never makes you wait for a candle to close) ---
HTF_BAR_SCANS = 30             # 15 minutes = 30 scans of 30 seconds (used when df_5 is missing)
HTF_MIN_BARS = 3               # completed 15-min candles needed before a bias is shown
HTF_EMA_BARS = 20              # EMA length on 15-min closes
HTF_EMA_MIN_BARS = 8           # the EMA is used only when this many candles exist
HTF_BONUS = 5.0                # score + when the 15-min bias agrees  (set 0 to switch the 15-min bias off completely)
HTF_PENALTY = 0.0              # score - when it disagrees. 0 = a disagreeing 15-min bias can NEVER hold back an entry

# --- PREMIUM ACCELERATION (is the rise speeding up?) ---
ACCEL_K = 2                    # compare the last 2 scans (1 minute) with the 2 scans before
ACCEL_MIN = 0.30               # acceleration beyond +/- this many percentage points counts
ACCEL_BONUS = 3.0              # score + when the premium rise is speeding up
ACCEL_PENALTY = 3.0            # score - when the rise is clearly fading

# --- 1-MINUTE BREAKOUT CONFIRMATION of the spot ---
BREAK_BAR_SCANS = 2            # a 1-minute bar = 2 scans of 30 seconds (or df_1 if your app has it)
BREAK_LOOKBACK_BARS = 10       # the range to break = the last 10 one-minute bars
BREAK_ATR_BARS = 14            # ATR over the last 14 one-minute bars
BREAK_MIN_B = 0.0              # B >= this = the bar closed beyond the range
BREAK_BONUS = 4.0              # score + on a confirmed breakout (no penalty, so it never holds an entry back)

# --- TRAP: a recent liquidity sweep against the side ---
TRAP_ON_SWEEP = True           # a sweep against the side adds a trap flag (30 = MEDIUM = WAIT)

# --- THETA (time decay) ---
THETA_HOLD_MIN = 30            # how long you expect to hold, in minutes
SESSION_MIN = 375              # trading minutes in a day (9:15 to 15:30)
THETA_WARN_PCT = 3.0           # decay over the holding time above this % of the premium = warning
THETA_PENALTY = 3.0            # score - when the decay warning is on

# --- RISK:REWARD (indicative only) ---
RR_MIN_RISK_PCT = 5.0          # the assumed stop is never tighter than this % below the price
RR_CAP = 9.9                   # risk:reward is never shown above this

# --- EARLY WARNING: a heads-up BEFORE the OI gate confirms (tracked separately as entry style "EARLY") ---
EARLY_ENABLED = True
EARLY_VOL_RATIO = 2.5          # volume at least this many times the average ...
EARLY_OI_MAX = 0.5             # ... while OI is NOT rising by more than this % (it has not confirmed yet)
EARLY_PERSIST_REQUIRED = 2     # the early conditions must hold in 2 of the last 3 scans (a real burst lasts; one random spike does not)
EARLY_Z_MIN = 2.0              # the volume must also be unusual for THIS strike (z-score)
EARLY_MAX_PER_SCAN = 1         # at most this many new warnings per scan (the strongest first)
EARLY_COOLDOWN_SCANS = 40      # one warning per strike and side per 40 scans (20 minutes)
EARLY_ALERT = False            # True = also send mail/Telegram for early warnings (can be noisy)
EARLY_SHOW = 6                 # how many recent warnings are shown

# --- ENTRY TIMING (after a signal) ---
RUNUP_SCANS = 6                # the move base is the lowest price in the scans before the signal
CHASE_MAX_PCT = 12.0           # premium already up this much from the base = "already ran"
PULLBACK_MIN = 30.0            # pullback (% of the run) from where an ENTRY ZONE starts
PULLBACK_FAIL = 80.0           # pullback this deep = the move is undone
SWING_SCANS = 6                # stop reference = lowest price of the last N scans
MIN_RR = 1.5                   # risk:reward below this is flagged
IV_SPIKE_PTS = 1.5             # IV rose this many points over the window = premium rise is partly IV
ATP_EXPENSIVE_PCT = 8.0        # LTP this % above the day's average traded price = expensive

# --- PRICE-WISE VOLUME (estimated from 30s snapshots) ---
VAP_BUCKET = 0.5               # price bucket size in rupees
BURST_RATIO = 3.0              # a scan with volume >= 3x average is logged as a "burst"
VAP_SHOW_ROWS = 8

# --- EDGE (EV) TRACKER ---
EV_TARGET_PCT = 20.0           # HIT  = premium +20% from the signal price first
EV_STOP_PCT = 10.0             # MISS = premium -10% first
EV_HORIZON_SCANS = 60          # judge after this many scans (60 x 30s = 30 min)
EV_MIN_SIGNALS = 30            # fewer finished signals than this = too early to conclude
LOGIT_MIN_SAMPLES = 100        # the probability model switches on after this many finished signals
OUTCOMES_FILE = "v40_outcomes_v5.csv"     # new file name: the recorded features changed
EV_COST_PCT = 2.0              # assumed round-trip cost per trade in % of the premium (brokerage + slippage + taxes). SET YOUR OWN.


# ==============================================================================
# SAFE HELPERS
# ==============================================================================

def to_float(value, default=0.0):
    try:
        if value is None or value == "":
            return float(default)
        return float(value)
    except Exception:
        return float(default)


def option_value(node, *names, default=0.0):
    """Reads a value using several possible Dhan field names."""
    if not isinstance(node, dict):
        return default
    for name in names:
        if name in node and node[name] not in (None, ""):
            return node[name]
    lowered = {str(k).lower(): v for k, v in node.items()}
    for name in names:
        key = str(name).lower()
        if key in lowered and lowered[key] not in (None, ""):
            return lowered[key]
    return default


# ==============================================================================
# OPTION CHAIN
# ==============================================================================

def extract_raw_oc():
    raw = None
    if "OptionChain" in globals() and OptionChain:
        for path in (
            ("data", "data", "oc"),
            ("data", "oc"),
            ("oc",),
        ):
            try:
                node = OptionChain
                for p in path:
                    node = node[p]
                raw = node
                break
            except Exception:
                continue
    if raw is None and "oc" in globals():
        raw = globals()["oc"]
    return raw


def normalize_option_chain(raw_oc):
    """{strike: {"ce": {...}, "pe": {...}}}"""
    if not isinstance(raw_oc, dict):
        return {}
    out = {}
    for raw_strike, payload in raw_oc.items():
        try:
            strike = float(raw_strike)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        ce = payload.get("ce", payload.get("CE", {}))
        pe = payload.get("pe", payload.get("PE", {}))
        out[strike] = {
            "ce": ce if isinstance(ce, dict) else {},
            "pe": pe if isinstance(pe, dict) else {},
        }
    return out


def _leg_gamma(leg):
    greeks = option_value(leg, "greeks", default={})
    g = to_float(option_value(greeks, "gamma")) if isinstance(greeks, dict) else 0.0
    return g if g != 0.0 else to_float(option_value(leg, "gamma"))


def _leg_vega(leg):
    greeks = option_value(leg, "greeks", default={})
    v = to_float(option_value(greeks, "vega")) if isinstance(greeks, dict) else 0.0
    return v if v != 0.0 else to_float(option_value(leg, "vega"))


def _leg_theta(leg):
    greeks = option_value(leg, "greeks", default={})
    t = to_float(option_value(greeks, "theta")) if isinstance(greeks, dict) else 0.0
    return t if t != 0.0 else to_float(option_value(leg, "theta"))


def read_leg(leg):
    return {
        "ltp": to_float(option_value(leg, "last_price", "lastPrice", "ltp", "LTP")),
        "oi": to_float(option_value(leg, "oi", "OI", "open_interest", "openInterest")),
        "vol": to_float(option_value(leg, "volume", "Volume", "traded_volume", "tradedVolume")),
        "bid": to_float(option_value(leg, "top_bid_price", "bid", "best_bid_price", "bidPrice")),
        "ask": to_float(option_value(leg, "top_ask_price", "ask", "best_ask_price", "askPrice")),
        "iv": to_float(option_value(leg, "implied_volatility", "impliedVolatility", "iv")),
        "avg": to_float(option_value(leg, "average_price", "avg_price", "averagePrice", "atp")),
        "gamma": _leg_gamma(leg),
        "vega": _leg_vega(leg),
        "theta": _leg_theta(leg),
        "bid_qty": to_float(option_value(leg, "top_bid_quantity", "bid_quantity", "bidQty", "best_bid_quantity")),
        "ask_qty": to_float(option_value(leg, "top_ask_quantity", "ask_quantity", "askQty", "best_ask_quantity")),
    }


# ==============================================================================
# DELTA - API delta first, Black-Scholes only as fallback
# ==============================================================================

def get_time_to_expiry():
    for name in ("expiry", "expiry_date", "selected_expiry"):
        val = globals().get(name)
        if val:
            try:
                d = pd.to_datetime(val).date()
                days = max((d - datetime.date.today()).days, 0.5)
                return days / 365.0
            except Exception:
                pass
    return FALLBACK_DAYS_TO_EXPIRY / 365.0


def bs_delta(S, K, T, r, sigma, side):
    try:
        if min(S, K, T, sigma) <= 0:
            return 0.0
        d1 = (math.log(S / K) + (r + sigma ** 2 / 2.0) * T) / (sigma * math.sqrt(T))
        cdf = 0.5 * (1.0 + math.erf(d1 / math.sqrt(2.0)))
        return cdf if side == "CE" else cdf - 1.0
    except Exception:
        return 0.0


def get_abs_delta(leg, S, K, T, side):
    greeks = option_value(leg, "greeks", default={})
    d = to_float(option_value(greeks, "delta")) if isinstance(greeks, dict) else 0.0
    if d == 0.0:
        d = to_float(option_value(leg, "delta"))
    if d != 0.0:
        return abs(d)
    iv = to_float(option_value(leg, "implied_volatility", "impliedVolatility", "iv"))
    sigma = iv / 100.0 if iv > 1 else iv
    return abs(bs_delta(S, K, T, RISK_FREE, sigma, side))


# ==============================================================================
# SESSION MEMORY
# ==============================================================================

def reset_v40():
    st.session_state.v40_hist = {}
    st.session_state.v40_scan_count = 0
    st.session_state.v40_signature = None
    st.session_state.v40_cache = None
    st.session_state.v40_last_side = None
    st.session_state.v40_last_side_scan = -9999
    st.session_state.v40_last_signal = None
    st.session_state.v40_spot_hist = []
    st.session_state.v40_watch = None
    st.session_state.v40_vap = {}
    st.session_state.v40_bursts = {}
    st.session_state.v40_track = []
    st.session_state.v40_mig = []
    st.session_state.v40_early_last = {}
    st.session_state.v40_early_log = []
    st.session_state.v40_day = datetime.date.today().isoformat()


if (
    "v40_hist" not in st.session_state
    or st.session_state.get("v40_day") != datetime.date.today().isoformat()
):
    reset_v40()   # new day = fresh start (volume/OI reset daily)


st.session_state.setdefault("v40_spot_hist", [])
st.session_state.setdefault("v40_watch", None)
st.session_state.setdefault("v40_last_signal", None)
st.session_state.setdefault("v40_vap", {})
st.session_state.setdefault("v40_bursts", {})
st.session_state.setdefault("v40_track", [])
st.session_state.setdefault("v40_mig", [])
st.session_state.setdefault("v40_early_last", {})
st.session_state.setdefault("v40_early_log", [])
st.session_state.setdefault("v40_logit_cache", None)


def update_metrics(key, ltp, oi, cum_vol):
    h = st.session_state.v40_hist.setdefault(
        key, {"oi": [], "cum": [], "new": [], "ltp": [], "persist": 0}
    )

    # Bad-data guard: an API glitch often returns 0 for OI / volume / price.
    # A zero (or a volume that goes backwards) is treated as "no new data":
    # the previous value is carried forward instead of corrupting the metrics.
    if h["oi"]:
        if oi <= 0:
            oi = h["oi"][-1]
        if ltp <= 0:
            ltp = h["ltp"][-1]
        if cum_vol < h["cum"][-1]:
            cum_vol = h["cum"][-1]

    oi_pct = prem_pct = new_vol = avg_vol = 0.0
    vol_ratio = 1.0

    if h["oi"]:
        # OI is compared with its value OI_LOOKBACK_SCANS scans ago (or the oldest we have)
        oi_ref = h["oi"][-min(OI_LOOKBACK_SCANS, len(h["oi"]))]
        oi_pct = (oi - oi_ref) / max(abs(oi_ref), 1.0) * 100.0
        prem_pct = (ltp - h["ltp"][-1]) / max(abs(h["ltp"][-1]), 0.05) * 100.0
        new_vol = max(cum_vol - h["cum"][-1], 0.0)

        window = h["new"][-VOLUME_WINDOW:]
        avg_vol = sum(window) / len(window) if window else 0.0
        vol_ratio = new_vol / avg_vol if avg_vol > 0 else 1.0
        h["new"].append(new_vol)       # the fake 0 of the first scan is no longer stored

    h["oi"].append(oi)
    h["cum"].append(cum_vol)
    h["ltp"].append(ltp)

    for name in ("oi", "cum", "new", "ltp"):
        if len(h[name]) > MEMORY_LIMIT:
            h[name].pop(0)

    return h, {
        "oi_pct": round(oi_pct, 2),
        "prem_pct": round(prem_pct, 2),
        "vol_ratio": round(vol_ratio, 2),
        "new_vol": round(new_vol, 2),
        "samples": len(h["oi"]),
    }


# ==============================================================================
# SCORE (100) and TRAP
# ==============================================================================

def direction_score(oi_pct, vol_ratio, prem_pct, delta):
    oi = min(35.0, max(0.0, -oi_pct) / SCORE_FULL_OI * 35.0)
    vol = min(30.0, max(0.0, vol_ratio) / SCORE_FULL_VOL * 30.0)
    prem = min(25.0, max(0.0, prem_pct) / SCORE_FULL_PREM * 25.0)
    dlt = max(0.0, 10.0 * (1.0 - abs(delta - DELTA_IDEAL) / 0.15))
    return round(oi + vol + prem + dlt, 1)


def trap_check(prem_pct, vol_ratio, spread_pct=None):
    """Only 3 clear traps. Each rule has exactly one meaning."""
    score, flags = 0.0, []
    if prem_pct >= TRAP_PREM_JUMP and vol_ratio < 2.5:
        score += 40.0
        flags.append("premium jump but weak volume")
    if vol_ratio >= 3.0 and prem_pct < 1.5:
        score += 40.0
        flags.append("volume spike but premium not following")
    if prem_pct >= TRAP_PREM_EXTREME:
        score += 30.0
        flags.append("extreme one-scan premium jump (chase risk)")

    if spread_pct is not None and spread_pct > MAX_SPREAD_PCT:
        score += 30.0
        flags.append(f"wide bid-ask spread ({spread_pct:.1f}%)")

    if score >= TRAP_HIGH_MIN:
        level = "HIGH"
    elif score >= TRAP_MEDIUM_MIN:
        level = "MEDIUM"
    else:
        level = "LOW"
    return score, level, flags


def evaluate_leg(side, strike, leg_data, delta):
    key = f"{side}_{int(strike)}"
    h, m = update_metrics(key, leg_data["ltp"], leg_data["oi"], leg_data["vol"])
    m.update(update_extras(h, key, leg_data, m))

    unwind_ok = m["oi_pct"] <= -UNWIND_OI_MIN
    build_ok = ALLOW_LONG_BUILDUP and m["oi_pct"] >= BUILDUP_OI_MIN
    pattern = "UNWIND" if unwind_ok else ("BUILDUP" if build_ok else "-")
    oi_for_score = -abs(m["oi_pct"])          # the score rewards the SIZE of the OI change
    gates = {
        "oi": unwind_ok or build_ok,
        "vol": m["vol_ratio"] >= VOLUME_RATIO_MIN and m["new_vol"] >= MIN_NEW_VOLUME,
        "prem": m["prem_pct"] > 0,
        "delta": DELTA_MIN <= delta <= DELTA_MAX,
    }
    gates_passed = sum(gates.values())
    # early warning: volume jumped and the premium is rising, delta fits, but the OI gate has not confirmed yet
    early_now = bool(
        EARLY_ENABLED and not (unwind_ok or build_ok) and m["samples"] >= WARMUP_SCANS
        and gates["delta"] and m["prem_pct"] > 0 and m["accel_pct"] >= 0.0
        and m["vol_ratio"] >= EARLY_VOL_RATIO and m["new_vol"] >= MIN_NEW_VOLUME
        and m["z_vol"] >= EARLY_Z_MIN and m["oi_pct"] <= EARLY_OI_MAX
    )
    early_hits = h.setdefault("ehits", [])
    early_hits.append(1 if early_now else 0)
    if len(early_hits) > 10:
        early_hits.pop(0)
    m["early"] = early_now and sum(early_hits[-3:]) >= EARLY_PERSIST_REQUIRED
    valid = m["samples"] >= WARMUP_SCANS and gates_passed == 4

    # persistence = how many of the last PERSIST_WINDOW scans passed all gates.
    # one weak scan no longer kills a good signal, and old signals fade out.
    hits = h.setdefault("hits", [])
    hits.append(1 if valid else 0)
    if len(hits) > 20:
        hits.pop(0)
    h["persist"] = sum(hits[-PERSIST_WINDOW:])

    spread_pct = None
    if leg_data["bid"] > 0 and leg_data["ask"] > 0 and leg_data["ltp"] > 0:
        spread_pct = (leg_data["ask"] - leg_data["bid"]) / leg_data["ltp"] * 100.0
    m["spread_pct"] = spread_pct

    trap_score, trap_lvl, trap_flags = trap_check(m["prem_pct"], m["vol_ratio"], spread_pct)

    candidate = None
    if valid and h["persist"] >= PERSIST_REQUIRED:
        candidate = {
            "strike": int(strike),
            "type": side,
            "action": f"BUY {side}",
            "oi_pct": m["oi_pct"],
            "prem_pct": m["prem_pct"],
            "vol_ratio": m["vol_ratio"],
            "delta": round(delta, 3),
            "score": direction_score(oi_for_score, m["vol_ratio"], m["prem_pct"], delta),
            "base_score": direction_score(oi_for_score, m["vol_ratio"], m["prem_pct"], delta),
            "ltp": leg_data["ltp"],
            "spread_pct": None if spread_pct is None else round(spread_pct, 2),
            "spot_dir": "-",
            "opp_oi_pct": 0.0,
            "trap_score": trap_score,
            "trap_level": trap_lvl,
            "trap_flags": trap_flags,
            "persistence": h["persist"],
            "z_vol": m["z_vol"],
            "z_oi": m["z_oi"],
            "iv_chg": m["iv_chg"],
            "avg_gap_pct": m["avg_gap_pct"],
            "excess_pct": 0.0,
            "spot_agree": 0,
            "bid_imb": m["bid_imb"],
            "imb_trend": m["imb_trend"],
            "maxpain_dir": 0,
            "pattern": pattern,
            "smc_agree": 0,
            "theta_burn_pct": 0.0,
            "htf_agree": 0,
            "accel_pct": m["accel_pct"],
            "breakout_b": 0.0,
        }
    return m, gates_passed, candidate


# ==============================================================================
# EXTRA ANALYTICS
#   price-wise volume, excess premium, z-scores, entry timing,
#   edge (expected value) tracker and a small logistic model
# ==============================================================================

def z_score(x, history):
    """How unusual is x compared with this strike's own recent history."""
    if len(history) < Z_MIN_SAMPLES:
        return 0.0
    mean = sum(history) / len(history)
    sd = (sum((v - mean) ** 2 for v in history) / len(history)) ** 0.5
    return (x - mean) / sd if sd > 1e-9 else 0.0


def classify_side(ltp, prev_ltp, bid, ask):
    """Estimate who pushed the trade: quote rule (inside the bid-ask) first, tick rule as fallback."""
    if bid > 0 and ask > bid:
        mid, quarter = (bid + ask) / 2.0, (ask - bid) / 4.0
        if ltp >= mid + quarter:
            return "BUY"
        if ltp <= mid - quarter:
            return "SELL"
    if prev_ltp > 0:
        if ltp > prev_ltp:
            return "BUY"
        if ltp < prev_ltp:
            return "SELL"
    return "MIXED"


def update_extras(h, key, leg, m):
    """Runs once per real scan for every leg. Keeps z-scores, IV change and the price-wise volume."""
    ss = st.session_state
    ltp = h["ltp"][-1]
    prev_ltp = h["ltp"][-2] if len(h["ltp"]) >= 2 else 0.0

    # z-scores (the OI z-score uses overlapping windows, so treat it as approximate)
    oi_hist = h.setdefault("oipct", [])
    z_oi = z_score(m["oi_pct"], oi_hist[-40:])
    prev_vols = h["new"][:-1][-VOLUME_WINDOW:] if len(h["new"]) > 1 else []
    z_vol = z_score(m["new_vol"], prev_vols)
    oi_hist.append(m["oi_pct"])
    if len(oi_hist) > 60:
        oi_hist.pop(0)

    # implied volatility change over the OI window (in IV points)
    iv_hist = h.setdefault("iv", [])
    iv = leg["iv"] if leg["iv"] > 0 else (iv_hist[-1] if iv_hist else 0.0)
    iv_chg = (iv - iv_hist[-min(OI_LOOKBACK_SCANS, len(iv_hist))]) if (iv_hist and iv > 0) else 0.0
    iv_hist.append(iv)
    if len(iv_hist) > MEMORY_LIMIT:
        iv_hist.pop(0)

    # LTP versus the day's average traded price
    avg_gap = (ltp / leg["avg"] - 1.0) * 100.0 if (leg["avg"] > 0 and ltp > 0) else None

    # price-wise volume: this scan's new volume is booked at the average of the last two prices
    new_vol = m["new_vol"]
    if new_vol > 0 and prev_ltp > 0:
        side_est = classify_side(ltp, prev_ltp, leg["bid"], leg["ask"])
        price = round(round(((ltp + prev_ltp) / 2.0) / VAP_BUCKET) * VAP_BUCKET, 2)
        row = ss.v40_vap.setdefault(key, {}).setdefault(price, [0.0, 0.0, 0.0])
        row[0] += new_vol
        if side_est == "BUY":
            row[1] += new_vol
        elif side_est == "SELL":
            row[2] += new_vol
        else:
            row[1] += new_vol / 2.0
            row[2] += new_vol / 2.0
        if m["vol_ratio"] >= BURST_RATIO and new_vol >= MIN_NEW_VOLUME:
            bursts = ss.v40_bursts.setdefault(key, [])
            bursts.append({"time": datetime.datetime.now().strftime("%H:%M:%S"),
                           "price": price, "qty": new_vol, "side": side_est})
            if len(bursts) > 30:
                bursts.pop(0)

    # top-of-book quantity imbalance: +1 = only bids, -1 = only asks (can be spoofed, flips fast)
    tot_q = leg["bid_qty"] + leg["ask_qty"]
    imb = (leg["bid_qty"] - leg["ask_qty"]) / tot_q if tot_q > 0 else 0.0
    imb_hist = h.setdefault("imb", [])
    recent_imb = imb_hist[-3:]
    imb_trend = imb - sum(recent_imb) / len(recent_imb) if recent_imb else 0.0
    imb_hist.append(imb)
    if len(imb_hist) > 60:
        imb_hist.pop(0)

    # premium acceleration: the rise of the last minute minus the rise of the minute before (percentage points)
    k = ACCEL_K
    accel = 0.0
    if len(h["ltp"]) >= 2 * k + 1:
        l0, l1, l2 = h["ltp"][-1], h["ltp"][-1 - k], h["ltp"][-1 - 2 * k]
        accel = (l0 / max(l1, 0.05) - 1.0) * 100.0 - (l1 / max(l2, 0.05) - 1.0) * 100.0

    return {
        "z_vol": round(z_vol, 2), "z_oi": round(z_oi, 2),
        "iv_chg": round(iv_chg, 2),
        "avg_gap_pct": None if avg_gap is None else round(avg_gap, 2),
        "bid_imb": round(imb, 2), "imb_trend": round(imb_trend, 2),
        "accel_pct": round(accel, 2),
    }


def excess_premium_pct(h, spot_hist, side, delta, gamma):
    """
    Premium change over the OI window minus the change the spot move alone explains:
        expected = sign * delta * dSpot + 0.5 * gamma * dSpot^2     (sign: CE +1, PE -1)
    Positive = somebody is buying the option harder than the spot move justifies.
    Returned as % of the starting premium.
    """
    n = min(OI_LOOKBACK_SCANS + 1, len(h["ltp"]), len(spot_hist))
    if n < 2:
        return 0.0
    ltp_ref = h["ltp"][-n]
    d_s = spot_hist[-1] - spot_hist[-n]
    sign = 1.0 if side == "CE" else -1.0
    expected = sign * delta * d_s + 0.5 * gamma * d_s * d_s
    actual = h["ltp"][-1] - ltp_ref
    return (actual - expected) / max(ltp_ref, 0.05) * 100.0


def entry_timing(watch, ltp, oi_pct):
    """Where are we in the move since the signal? Returns (state text, run-up %, pullback %)."""
    base, peak = watch["base_ltp"], watch["peak"]
    run = max(peak - base, 0.0)
    run_pct = run / max(base, 0.05) * 100.0
    retrace = (peak - ltp) / run * 100.0 if run > 0 else 0.0
    if oi_pct >= WATCH_OI_RISE_PCT:
        state = "INVALID - OI is rising again"
    elif retrace >= PULLBACK_FAIL:
        state = "INVALID - the move is undone"
    elif retrace >= PULLBACK_MIN:
        state = "ENTRY ZONE - pullback while OI holds"
    elif run_pct >= CHASE_MAX_PCT:
        state = "WAIT FOR PULLBACK - already ran"
    else:
        state = "ENTRY OK - early, not chased"
    return state, run_pct, retrace


def risk_reward(h, ltp, run):
    """
    Indicative only. Target = one more leg of the same size as the run so far.
    Stop = lowest price of the last SWING_SCANS scans, but never tighter than RR_MIN_RISK_PCT below the price
    (a stop that is a few paise away would give a silly, huge ratio).
    """
    if ltp <= 0:
        return None, 0.0
    swing_low = min(h["ltp"][-SWING_SCANS:])
    risk = max(ltp - swing_low, ltp * RR_MIN_RISK_PCT / 100.0)
    return min(run / risk, RR_CAP), swing_low


# ---------------------------- edge (EV) tracker ----------------------------

LOGIT_FEATURES = ["oi_pct", "vol_ratio", "prem_pct", "excess_pct", "z_vol", "z_oi", "delta", "score", "spot_agree",
                  "bid_imb", "iv_chg", "maxpain_dir", "smc_agree", "theta_burn_pct", "htf_agree", "accel_pct", "breakout_b"]


def feats_of(c):
    return {k: float(c.get(k, 0.0) or 0.0) for k in LOGIT_FEATURES}


def tracker_open(items, variant, c, entry, scan_no, feats):
    items.append({"variant": variant, "side": c["type"], "strike": c["strike"], "entry": entry,
                  "scan0": scan_no, "score": c["score"], "feats": feats,
                  "last_pct": 0.0, "outcome": None, "pnl": 0.0})


def tracker_update(items, leg_metrics, scan_no):
    """Moves every open item forward with the latest price; returns the items that just finished."""
    finished, still = [], []
    for it in items:
        lm = leg_metrics.get(f"{it['side']}_{it['strike']}")
        if lm and lm["ltp"] > 0 and it["entry"] > 0:
            it["last_pct"] = (lm["ltp"] / it["entry"] - 1.0) * 100.0
        pct = it["last_pct"]
        if it["outcome"] is None:
            if pct >= EV_TARGET_PCT:
                it["outcome"], it["pnl"] = "HIT", EV_TARGET_PCT
            elif pct <= -EV_STOP_PCT:
                it["outcome"], it["pnl"] = "MISS", min(pct, -EV_STOP_PCT)
            elif scan_no - it["scan0"] >= EV_HORIZON_SCANS:
                it["outcome"], it["pnl"] = "NO MOVE", pct
        (finished if it["outcome"] else still).append(it)
    items[:] = still
    return finished


def write_v40_outcomes(finished):
    if not (LOG_ENABLED and finished):
        return
    try:
        rows = []
        for it in finished:
            row = {"time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                   "variant": it["variant"], "side": it["side"], "strike": it["strike"],
                   "entry": round(it["entry"], 2), "outcome": it["outcome"], "pnl_pct": round(it["pnl"], 2)}
            row.update({k: round(v, 3) for k, v in it["feats"].items()})
            rows.append(row)
        path = os.path.join(LOG_DIR, OUTCOMES_FILE)
        pd.DataFrame(rows).to_csv(path, mode="a", header=not os.path.exists(path), index=False)
    except Exception:
        pass


def load_outcomes():
    path = os.path.join(LOG_DIR, OUTCOMES_FILE)
    if not os.path.exists(path):
        return None
    try:
        df = pd.read_csv(path)
    except Exception:
        return None
    return df if not df.empty else None


def edge_table(df):
    """Per entry style: how often HIT / MISS / NO MOVE and the average result (expected value)."""
    rows = []
    for variant, g in df.groupby("variant"):
        n = len(g)
        rows.append({"ENTRY STYLE": variant, "SIGNALS": n,
                     "HIT %": round((g["outcome"] == "HIT").mean() * 100.0, 1),
                     "MISS %": round((g["outcome"] == "MISS").mean() * 100.0, 1),
                     "NO MOVE %": round((g["outcome"] == "NO MOVE").mean() * 100.0, 1),
                     "EV % per trade": round(float(g["pnl_pct"].mean()), 2),
                     "EV after costs %": round(float(g["pnl_pct"].mean()) - EV_COST_PCT, 2)})
    return pd.DataFrame(rows)


def fit_logit(df):
    """Small logistic regression (numpy only): P(HIT) from the features recorded at signal time."""
    import numpy as np
    d = df[df["variant"] == "IMMEDIATE"].dropna(subset=LOGIT_FEATURES + ["outcome"])
    n = len(d)
    if n < LOGIT_MIN_SAMPLES:
        return {"ok": False, "n": n, "why": f"needs {LOGIT_MIN_SAMPLES} finished signals, has {n}"}
    y = (d["outcome"] == "HIT").astype(float).values
    if y.sum() < 10 or (n - y.sum()) < 10:
        return {"ok": False, "n": n, "why": "needs at least 10 HIT and 10 non-HIT signals"}
    X = d[LOGIT_FEATURES].astype(float).values
    cut = int(n * 0.8)                       # oldest 80% train, newest 20% test (honest check)
    mu, sd = X[:cut].mean(axis=0), X[:cut].std(axis=0)
    sd[sd == 0] = 1.0
    Z = (X - mu) / sd
    w, b = np.zeros(Z.shape[1]), 0.0
    for _ in range(2000):
        p = 1.0 / (1.0 + np.exp(-(Z[:cut] @ w + b)))
        w -= 0.1 * (Z[:cut].T @ (p - y[:cut]) / cut + 0.1 * w)    # 0.1 * w = L2 penalty
        b -= 0.1 * float((p - y[:cut]).mean())
    pt = 1.0 / (1.0 + np.exp(-(Z[cut:] @ w + b)))
    acc = float(((pt >= 0.5) == (y[cut:] == 1)).mean())
    base = float(max(y[cut:].mean(), 1.0 - y[cut:].mean()))      # accuracy of always guessing the majority
    n_test = n - cut
    se = (max(acc * (1.0 - acc), 0.01) / n_test) ** 0.5       # standard error of the test accuracy
    return {"ok": True, "n": n, "mu": mu, "sd": sd, "w": w, "b": b, "acc": acc, "base": base,
            "hit_rate": float(y.mean()),
            "useful": (acc - base) >= 2.0 * se}               # must beat guessing by 2 standard errors


def model_p_hit(model, feats):
    import numpy as np
    x = (np.array([feats[k] for k in LOGIT_FEATURES]) - model["mu"]) / model["sd"]
    return float(1.0 / (1.0 + np.exp(-(x @ model["w"] + model["b"]))))


# ---------------------------- price-wise volume table ----------------------------

def vap_summary(key, ltp):
    """Returns (table DataFrame, summary dict) or (None, None) when nothing is recorded yet."""
    vap = st.session_state.v40_vap.get(key, {})
    if not vap:
        return None, None
    df = pd.DataFrame([{"price": p, "vol": v[0], "buy": v[1], "sell": v[2]} for p, v in vap.items()])
    df = df.sort_values("vol", ascending=False).reset_index(drop=True)
    total, buy_vol, sell_vol = df["vol"].sum(), df["buy"].sum(), df["sell"].sum()
    poc = float(df.iloc[0]["price"])
    nearest = float(min(df["price"], key=lambda p: abs(p - ltp)))
    top = df.head(VAP_SHOW_ROWS).copy()
    top["share"] = (top["vol"] / total * 100.0).round(1)
    top["note"] = [("POC " if p == poc else "") + ("<- LTP" if p == nearest else "") for p in top["price"]]
    summary = {
        "total": total, "poc": poc,
        "net_buy_pct": (buy_vol - sell_vol) / (buy_vol + sell_vol) * 100.0 if (buy_vol + sell_vol) > 0 else 0.0,
        "buyer_avg": float((df["price"] * df["buy"]).sum() / buy_vol) if buy_vol > 0 else None,
        "seller_avg": float((df["price"] * df["sell"]).sum() / sell_vol) if sell_vol > 0 else None,
    }
    return top, summary


# ---------------------------- chain structure, IV state, volume migration ----------------------------

def chain_structure(norm_oc):
    """Max pain, biggest call/put OI strikes (walls) and put-call ratio from the WHOLE chain."""
    strikes, ce_oi, pe_oi = [], {}, {}
    for k in sorted(norm_oc):
        ce_oi[k] = read_leg(norm_oc[k]["ce"])["oi"]
        pe_oi[k] = read_leg(norm_oc[k]["pe"])["oi"]
        strikes.append(k)
    tot_c, tot_p = sum(ce_oi.values()), sum(pe_oi.values())
    if not strikes or (tot_c + tot_p) <= 0:
        return {"max_pain": None, "call_wall": None, "put_wall": None, "pcr": None}
    best_p, best_pain = None, None
    for price in strikes:      # total pay-out to option BUYERS if expiry settles at this price
        pain = sum(ce_oi[k] * max(price - k, 0.0) + pe_oi[k] * max(k - price, 0.0) for k in strikes)
        if best_pain is None or pain < best_pain:
            best_p, best_pain = price, pain
    call_wall = max(strikes, key=lambda k: ce_oi[k])
    put_wall = max(strikes, key=lambda k: pe_oi[k])
    return {"max_pain": best_p, "call_wall": call_wall, "call_wall_oi": ce_oi[call_wall],
            "put_wall": put_wall, "put_wall_oi": pe_oi[put_wall],
            "pcr": (tot_p / tot_c) if tot_c > 0 else None}


def iv_state(prem_win_pct, iv_chg):
    """Premium move versus IV move over the OI window: is the rise real or just volatility?"""
    if prem_win_pct > 0 and iv_chg >= IV_SPIKE_PTS:
        return "IV SPIKE - the premium rise is partly volatility (risk of an IV crush)"
    if prem_win_pct > 0:
        return "PRICE-LED RISE - premium up without an IV spike (healthier)"
    if iv_chg <= -IV_SPIKE_PTS:
        return "IV CRUSH - premium is falling because volatility is dropping"
    if iv_chg >= IV_SPIKE_PTS:
        return "IV rising but the premium is not following - watch for a trap"
    return "NEUTRAL"


def atm_iv_summary(hist, atm_strike):
    """Average ATM implied volatility now and its change over the OI window (IV points)."""
    now_vals, ref_vals = [], []
    for side in ("CE", "PE"):
        h = hist.get(f"{side}_{int(atm_strike)}")
        if h and h.get("iv") and h["iv"][-1] > 0:
            now_vals.append(h["iv"][-1])
            ref_vals.append(h["iv"][-min(OI_LOOKBACK_SCANS + 1, len(h["iv"]))])
    if not now_vals:
        return None
    now = sum(now_vals) / len(now_vals)
    return now, now - sum(ref_vals) / len(ref_vals)


def migration_summary(step):
    """Where is the traded volume moving? Volume-weighted centre strike, last N scans versus the N before."""
    mig = st.session_state.v40_mig
    n = MIG_RECENT_SCANS
    if len(mig) < n + 3:
        return None
    recent = mig[-n:]
    prev = mig[max(0, len(mig) - 2 * n): len(mig) - n]
    out = {}
    for side in ("CE", "PE"):
        def centre(chunk):
            tot = sum(v for d in chunk for v in d[side].values())
            return sum(k * v for d in chunk for k, v in d[side].items()) / tot if tot > 0 else None
        c_now, c_prev = centre(recent), centre(prev)
        if c_now is None or c_prev is None:
            out[side] = None
            continue
        shift = c_now - c_prev
        direction = ("UP" if shift >= MIG_MIN_SHIFT_STEPS * step
                     else "DOWN" if shift <= -MIG_MIN_SHIFT_STEPS * step else "STABLE")
        out[side] = {"now": c_now, "prev": c_prev, "shift": shift, "dir": direction}
    return out


# ---------------------------- price structure (swings, break of structure, sweeps) ----------------------------

def trap_level(score):
    if score >= TRAP_HIGH_MIN:
        return "HIGH"
    if score >= TRAP_MEDIUM_MIN:
        return "MEDIUM"
    return "LOW"


def smc_bars(spot_hist, df5=None):
    """Bars for the structure check: df_5 if it has high/low/close, else built from the spot history."""
    try:
        if df5 is not None and hasattr(df5, "columns") and len(df5) >= SMC_MIN_BARS:
            cols = {str(c).lower(): c for c in df5.columns}
            if all(k in cols for k in ("high", "low", "close")):
                d = df5.tail(60)
                return [(float(a), float(b), float(c)) for a, b, c in
                        zip(d[cols["high"]], d[cols["low"]], d[cols["close"]])], "df_5"
    except Exception:
        pass
    n = SMC_BAR_SCANS
    bars = []
    for i in range(0, len(spot_hist) - n + 1, n):
        chunk = spot_hist[i:i + n]
        bars.append((max(chunk), min(chunk), chunk[-1]))
    return bars, "spot"


def smc_bias(bars):
    """
    Small, objective subset of price-structure analysis:
      swing high / low  = extreme of SMC_SWING_K bars on each side
      break of structure = a close beyond the last swing high (up) or low (down)
      liquidity sweep    = a wick beyond the last swing level that closes back inside
    BULLISH = up-break or sweep of the lows; BEARISH = down-break or sweep of the highs.
    """
    n, k = len(bars), SMC_SWING_K
    if n < SMC_MIN_BARS:
        return {"bias": "n/a", "why": f"needs {SMC_MIN_BARS} bars, has {n}", "events": [],
                "sweep_bull": False, "sweep_bear": False}
    highs, lows, closes = [b[0] for b in bars], [b[1] for b in bars], [b[2] for b in bars]
    sh = sl = None
    for i in range(k, n - k):
        if highs[i] == max(highs[i - k:i + k + 1]):
            sh = (i, highs[i])
        if lows[i] == min(lows[i - k:i + k + 1]):
            sl = (i, lows[i])
    events, bull, bear = [], False, False
    sweep_bull = sweep_bear = False
    first_bos = max(n - SMC_BOS_LOOKBACK, 0)
    first_swp = max(n - SMC_SWEEP_LOOKBACK, 0)
    if sh:
        if any(closes[j] > sh[1] for j in range(max(first_bos, sh[0] + 1), n)):
            bull = True
            events.append(f"closed above the swing high {sh[1]:.0f} (break of structure up)")
        if any(highs[j] > sh[1] and closes[j] < sh[1] for j in range(max(first_swp, sh[0] + 1), n)):
            bear = True
            sweep_bear = True
            events.append(f"swept the highs above {sh[1]:.0f} and fell back (liquidity sweep)")
    if sl:
        if any(closes[j] < sl[1] for j in range(max(first_bos, sl[0] + 1), n)):
            bear = True
            events.append(f"closed below the swing low {sl[1]:.0f} (break of structure down)")
        if any(lows[j] < sl[1] and closes[j] > sl[1] for j in range(max(first_swp, sl[0] + 1), n)):
            bull = True
            sweep_bull = True
            events.append(f"swept the lows below {sl[1]:.0f} and rose back (liquidity sweep)")
    bias = "BULLISH" if (bull and not bear) else ("BEARISH" if (bear and not bull) else "NEUTRAL")
    return {"bias": bias, "events": events, "swing_high": sh[1] if sh else None, "swing_low": sl[1] if sl else None,
            "sweep_bull": sweep_bull, "sweep_bear": sweep_bear}


# ---------------------------- 15-minute bias (a filter, never a wait) ----------------------------

def _df5_times(d):
    """Time of every df_5 row: a datetime index, or a time column (epoch seconds / milliseconds, or text)."""
    try:
        if isinstance(d.index, pd.DatetimeIndex):
            return d.index
        for col in d.columns:
            if str(col).lower().replace("_", "") in ("timestamp", "datetime", "date", "time", "starttime"):
                v = d[col]
                if pd.api.types.is_numeric_dtype(v):
                    return pd.DatetimeIndex(pd.to_datetime(v, unit="ms" if float(v.iloc[-1]) > 1e11 else "s"))
                return pd.DatetimeIndex(pd.to_datetime(v))
    except Exception:
        return None
    return None


def htf_bars(spot_hist, df5=None):
    """15-minute (open, high, low, close) bars. From df_5 (3 bars each) if available, else from the spot history."""
    try:
        if df5 is not None and hasattr(df5, "columns") and len(df5) >= 9:
            cols = {str(c).lower(): c for c in df5.columns}
            if all(k in cols for k in ("open", "high", "low", "close")):
                d = df5.tail(150)
                times = _df5_times(d)
                if times is not None:                         # clock-aligned candles (09:15, 09:30, ...)
                    x = pd.DataFrame({"o": d[cols["open"]].astype(float).values, "h": d[cols["high"]].astype(float).values,
                                      "l": d[cols["low"]].astype(float).values, "c": d[cols["close"]].astype(float).values},
                                     index=times).sort_index()
                    r = x.resample("15min").agg({"o": "first", "h": "max", "l": "min", "c": "last"}).dropna()
                    return list(zip(r["o"], r["h"], r["l"], r["c"])), "df_5 by time"
                d = d.iloc[len(d) % 3:]                       # no time information: every 3 rows, ending at the last row
                o, h, l, c = (d[cols[k]].astype(float).tolist() for k in ("open", "high", "low", "close"))
                return [(o[i], max(h[i:i + 3]), min(l[i:i + 3]), c[i + 2]) for i in range(0, len(o) - 2, 3)], "df_5"
    except Exception:
        pass
    n = HTF_BAR_SCANS
    start = len(spot_hist) % n                                # rolling windows that end at the current scan
    bars = []
    for i in range(start, len(spot_hist) - n + 1, n):
        chunk = spot_hist[i:i + n]
        bars.append((chunk[0], max(chunk), min(chunk), chunk[-1]))
    return bars, "spot"


def htf_bias(bars, live_spot):
    """
    Direction bias from COMPLETED 15-minute candles (the last bar is still forming and is not used):
      +1 / -1  last candle closed above the previous high / below the previous low
      +1 / -1  last close is above / below its EMA and the EMA is rising / falling
      +1 / -1  live spot is above / below the last completed candle's high / low
    """
    done = bars[:-1]
    if len(done) < HTF_MIN_BARS:
        return {"bias": "n/a", "why": f"needs {HTF_MIN_BARS} completed 15-min candles, has {len(done)}", "notes": []}
    last, prev = done[-1], done[-2]
    score, notes = 0, []
    if last[3] > prev[1]:
        score += 1
        notes.append("last 15-min candle closed above the previous high")
    elif last[3] < prev[2]:
        score -= 1
        notes.append("last 15-min candle closed below the previous low")
    closes = [b[3] for b in done]
    if len(closes) >= HTF_EMA_MIN_BARS:
        n = min(HTF_EMA_BARS, len(closes))
        alpha, ema = 2.0 / (n + 1), [closes[0]]
        for c in closes[1:]:
            ema.append(alpha * c + (1 - alpha) * ema[-1])
        if closes[-1] > ema[-1] and ema[-1] > ema[-2]:
            score += 1
            notes.append(f"above a rising EMA{n}")
        elif closes[-1] < ema[-1] and ema[-1] < ema[-2]:
            score -= 1
            notes.append(f"below a falling EMA{n}")
    if live_spot > last[1]:
        score += 1
        notes.append("spot is above the last candle's high")
    elif live_spot < last[2]:
        score -= 1
        notes.append("spot is below the last candle's low")
    bias = "BULLISH" if score >= 1 else ("BEARISH" if score <= -1 else "NEUTRAL")
    return {"bias": bias, "score": score, "notes": notes}


# ---------------------------- 1-minute breakout confirmation (in ATR units) ----------------------------

def onemin_bars(spot_hist, df1=None):
    """1-minute (open, high, low, close) bars: from df_1 if the app has one, else from the 30-second spot samples."""
    try:
        if df1 is not None and hasattr(df1, "columns") and len(df1) >= BREAK_LOOKBACK_BARS + 3:
            cols = {str(c).lower(): c for c in df1.columns}
            if all(k in cols for k in ("open", "high", "low", "close")):
                d = df1.tail(60)
                return list(zip(*(d[cols[k]].astype(float).tolist() for k in ("open", "high", "low", "close")))), "df_1"
    except Exception:
        pass
    n = BREAK_BAR_SCANS
    start = len(spot_hist) % n
    bars = []
    for i in range(start, len(spot_hist) - n + 1, n):
        chunk = spot_hist[i:i + n]
        bars.append((chunk[0], max(chunk), min(chunk), chunk[-1]))
    return bars, "spot"


def breakout_b(bars, side):
    """
    B = (close - highest high of the previous N bars) / ATR   for CE
    B = (lowest low of the previous N bars - close) / ATR     for PE
    B >= 0 means the last 1-minute bar CLOSED beyond the range (a wick alone does not count).
    Returns None while there is not enough history. Clipped to +/-3.
    """
    if len(bars) < BREAK_LOOKBACK_BARS + 2:
        return None
    prior, last = bars[-(BREAK_LOOKBACK_BARS + 1):-1], bars[-1]
    top, bottom = max(b[1] for b in prior), min(b[2] for b in prior)
    trs = []
    for i in range(max(1, len(bars) - BREAK_ATR_BARS), len(bars)):
        pc = bars[i - 1][3]
        trs.append(max(bars[i][1] - bars[i][2], abs(bars[i][1] - pc), abs(bars[i][2] - pc)))
    atr = max(sum(trs) / len(trs), last[3] * 0.00005)
    b = (last[3] - top) / atr if side == "CE" else (bottom - last[3]) / atr
    return max(-3.0, min(3.0, b))


# ==============================================================================
# ALERT and LOG helpers
# ==============================================================================

def _send_email(text):
    """Runs in a background thread so a slow mail server never stops the scan. Any error is ignored."""
    try:
        import smtplib
        import ssl
        from email.message import EmailMessage
        msg = EmailMessage()
        msg["Subject"] = text[:90]
        msg["From"] = EMAIL_USER
        msg["To"] = EMAIL_TO
        msg.set_content(text + "\n\nSent by the V40 engine. A signal is not advice - use your own stop-loss.")
        ctx = ssl.create_default_context()
        if EMAIL_PORT == 465:
            with smtplib.SMTP_SSL(EMAIL_HOST, EMAIL_PORT, timeout=10, context=ctx) as server:
                server.login(EMAIL_USER, EMAIL_PASS)
                server.send_message(msg)
        else:
            with smtplib.SMTP(EMAIL_HOST, EMAIL_PORT, timeout=10) as server:
                server.starttls(context=ctx)
                server.login(EMAIL_USER, EMAIL_PASS)
                server.send_message(msg)
    except Exception:
        pass


def send_alert(text):
    """On-screen toast always; Telegram and e-mail only when their settings are present."""
    try:
        st.toast(text)
    except Exception:
        pass
    if TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID:
        try:
            import urllib.parse
            import urllib.request
            url = f"https://api.telegram.org/bot{TELEGRAM_BOT_TOKEN}/sendMessage"
            data = urllib.parse.urlencode({"chat_id": TELEGRAM_CHAT_ID, "text": text}).encode()
            urllib.request.urlopen(url, data=data, timeout=3)
        except Exception:
            pass
    if EMAIL_USER and EMAIL_PASS and EMAIL_TO:
        import threading
        threading.Thread(target=_send_email, args=(text,), daemon=True).start()


def write_logs(scan_rows, decision_row):
    """Appends to CSV files. Any error is ignored so the terminal never breaks."""
    if not LOG_ENABLED:
        return
    try:
        import csv
        import os
        for name, rows in (("v40_scan_log.csv", scan_rows), ("v40_decision_log.csv", [decision_row])):
            if not rows:
                continue
            path = os.path.join(LOG_DIR, name)
            new_file = not os.path.exists(path)
            with open(path, "a", newline="") as f:
                w = csv.DictWriter(f, fieldnames=list(rows[0].keys()))
                if new_file:
                    w.writeheader()
                w.writerows(rows)
    except Exception:
        pass


# ==============================================================================
# DATA INTAKE
# ==============================================================================
def v40_run():
    """The whole live part. A plain return (never st.stop) when data is missing,
    so the rest of a bigger dashboard keeps running."""

    st.markdown("---")
    st.markdown("## V40.2 SIMPLIFIED OPERATOR TERMINAL")

    if st.button("Send test alert"):
        send_alert("V40 TEST ALERT - if you can read this, your alerts work")
    if st.button("V40 memory reset"):
        reset_v40()

    raw_oc = extract_raw_oc()
    if not raw_oc:
        st.error("OPTION CHAIN DATA not available.")
        return

    normalized_oc = normalize_option_chain(raw_oc)
    if not normalized_oc:
        st.error("Could not normalize OPTION CHAIN.")
        return

    live_spot = 0.0
    try:
        if "spot" in globals() and spot:
            live_spot = float(spot)
        elif "df_5" in globals() and not df_5.empty:
            live_spot = float(df_5["close"].iloc[-1])
    except Exception:
        live_spot = 0.0

    if live_spot <= 0:
        st.error("LIVE SPOT not available.")
        return

    all_strikes = sorted(normalized_oc.keys())
    atm_strike = min(all_strikes, key=lambda s: abs(s - live_spot))   # nearest strike in the chain
    atm_idx = all_strikes.index(atm_strike)
    target_strikes = all_strikes[
        max(0, atm_idx - SCAN_STRIKES_EACH_SIDE): atm_idx + SCAN_STRIKES_EACH_SIDE + 1
    ]

    # ------------------------------------------------------------------------------
    # Streamlit rerun protection:
    # if the data has not changed (rerun caused by a button/widget), do not count a new scan.
    # Earlier, such reruns filled history with "0 volume" and corrupted all metrics.
    # ------------------------------------------------------------------------------
    legs = {}
    for s in target_strikes:
        legs[s] = {
            "ce": read_leg(normalized_oc[s]["ce"]),
            "pe": read_leg(normalized_oc[s]["pe"]),
        }

    signature = tuple(
        (s, d["ce"]["ltp"], d["ce"]["oi"], d["ce"]["vol"],
            d["pe"]["ltp"], d["pe"]["oi"], d["pe"]["vol"])
        for s, d in legs.items()
    )

    is_new_scan = (
        signature != st.session_state.v40_signature
        or st.session_state.v40_cache is None
    )

    if is_new_scan:
        st.session_state.v40_scan_count += 1
        st.session_state.v40_signature = signature

        T = get_time_to_expiry()
        all_candidates, live_rows, scan_log_rows = [], [], []
        leg_metrics = {}
        scan_vol = {"CE": {}, "PE": {}}
        early_found = []
        now_text = datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S")

        for strike in target_strikes:
            row = {"STRIKE": int(strike), "ATM": "ATM" if strike == atm_strike else ""}
            for side, leg_key in (("CE", "ce"), ("PE", "pe")):
                leg_data = legs[strike][leg_key]
                delta = get_abs_delta(normalized_oc[strike][leg_key], live_spot, strike, T, side)
                m, gates_passed, cand = evaluate_leg(side, strike, leg_data, delta)
                if cand:
                    all_candidates.append(cand)
                if m.get("early"):
                    early_found.append({
                        "type": side, "strike": int(strike), "ltp": leg_data["ltp"],
                        "oi_pct": m["oi_pct"], "vol_ratio": m["vol_ratio"], "prem_pct": m["prem_pct"],
                        "delta": delta, "z_vol": m["z_vol"], "z_oi": m["z_oi"],
                        "bid_imb": m["bid_imb"], "iv_chg": m["iv_chg"], "accel_pct": m["accel_pct"]})
                leg_metrics[f"{side}_{int(strike)}"] = {
                    "oi_pct": m["oi_pct"], "vol_ratio": m["vol_ratio"], "ltp": leg_data["ltp"],
                    "iv_chg": m["iv_chg"], "avg_gap_pct": m["avg_gap_pct"], "spread_pct": m["spread_pct"],
                    "z_vol": m["z_vol"], "z_oi": m["z_oi"], "excess_pct": 0.0,
                    "delta": delta, "gamma": leg_data["gamma"], "vega": leg_data["vega"], "theta": leg_data["theta"],
                    "bid_imb": m["bid_imb"], "imb_trend": m["imb_trend"], "accel_pct": m["accel_pct"],
                    "prem_win_pct": 0.0, "iv_part_pct": 0.0, "excess_ex_iv": 0.0,
                    "theta_burn_pct": 0.0, "theta_be_pts": 0.0,
                }
                scan_vol[side][int(strike)] = m["new_vol"]
                scan_log_rows.append({
                    "time": now_text, "scan": st.session_state.v40_scan_count, "spot": live_spot,
                    "side": side, "strike": int(strike), "ltp": leg_data["ltp"],
                    "oi": leg_data["oi"], "volume": leg_data["vol"],
                    "oi_pct_window": m["oi_pct"], "vol_ratio": m["vol_ratio"],
                    "prem_pct": m["prem_pct"], "delta": round(delta, 3),
                    "gates": gates_passed,
                    "spread_pct": "" if m["spread_pct"] is None else round(m["spread_pct"], 2),
                })
                row.update({
                    f"{side} LTP": round(leg_data["ltp"], 2),
                    f"{side} OI chg% (window)": m["oi_pct"],
                    f"{side} VOL x": m["vol_ratio"],
                    f"{side} DELTA": round(delta, 2),
                    f"{side} GATES": f"{gates_passed}/4",
                })
            live_rows.append(row)

        # ---- spot direction (one entry per real scan) ----
        spot_hist = st.session_state.v40_spot_hist
        spot_hist.append(live_spot)
        if len(spot_hist) > MEMORY_LIMIT:
            spot_hist.pop(0)
        spot_move_pct = 0.0
        if len(spot_hist) > 1:
            spot_ref = spot_hist[-min(SPOT_LOOKBACK_SCANS + 1, len(spot_hist))]
            spot_move_pct = (live_spot - spot_ref) / spot_ref * 100.0
        if len(spot_hist) < 2:
            spot_dir = "-"
        elif spot_move_pct >= SPOT_MIN_MOVE_PCT:
            spot_dir = "UP"
        elif spot_move_pct <= -SPOT_MIN_MOVE_PCT:
            spot_dir = "DOWN"
        else:
            spot_dir = "FLAT"

        # ---- total OI change of one side over the OI window (for the opposite-side check) ----
        def side_oi_change_pct(side_name):
            now_sum = ref_sum = 0.0
            for s_ in target_strikes:
                hh = st.session_state.v40_hist.get(f"{side_name}_{int(s_)}")
                if not hh or len(hh["oi"]) < 2:
                    continue
                now_sum += hh["oi"][-1]
                ref_sum += hh["oi"][-min(OI_LOOKBACK_SCANS + 1, len(hh["oi"]))]
            return (now_sum - ref_sum) / ref_sum * 100.0 if ref_sum > 0 else 0.0

        pe_total_oi_chg = side_oi_change_pct("PE")
        ce_total_oi_chg = side_oi_change_pct("CE")

        # ---- excess premium for every leg (premium change the spot move does not explain) ----
        # and the part of the premium move that IV (vega) explains
        for key_, lm_ in leg_metrics.items():
            hh_ = st.session_state.v40_hist.get(key_)
            if hh_:
                lm_["excess_pct"] = round(
                    excess_premium_pct(hh_, spot_hist, key_.split("_")[0], lm_["delta"], lm_["gamma"]), 2)
                n_ = min(OI_LOOKBACK_SCANS + 1, len(hh_["ltp"]))
                ref_ = max(hh_["ltp"][-n_], 0.05)
                lm_["prem_win_pct"] = round((hh_["ltp"][-1] / ref_ - 1.0) * 100.0, 2)
                lm_["iv_part_pct"] = round(lm_["vega"] * lm_["iv_chg"] / ref_ * 100.0, 2)
                lm_["excess_ex_iv"] = round(lm_["excess_pct"] - lm_["iv_part_pct"], 2)
                theta_cost = abs(lm_["theta"]) * THETA_HOLD_MIN / SESSION_MIN      # premium lost to time while you hold
                lm_["theta_burn_pct"] = round(theta_cost / max(lm_["ltp"], 0.05) * 100.0, 2)
                lm_["theta_be_pts"] = round(theta_cost / lm_["delta"], 1) if lm_["delta"] > 0 else 0.0

        # ---- chain structure (max pain, walls, PCR) and volume migration ----
        structure = chain_structure(normalized_oc)
        st.session_state.v40_mig.append(scan_vol)
        if len(st.session_state.v40_mig) > 80:
            st.session_state.v40_mig.pop(0)
        strike_step = min([b - a for a, b in zip(all_strikes, all_strikes[1:]) if b > a] or [50.0])
        smc_list, smc_src = smc_bars(spot_hist, globals().get("df_5"))
        smc = smc_bias(smc_list)
        smc["source"] = smc_src
        htf_list, htf_src = htf_bars(spot_hist, globals().get("df_5"))
        htf = htf_bias(htf_list, live_spot)
        htf["source"] = htf_src
        brk_list, brk_src = onemin_bars(spot_hist, globals().get("df_1"))
        brk = {"CE": breakout_b(brk_list, "CE"), "PE": breakout_b(brk_list, "PE"), "source": brk_src}

        # ---- soft score adjustments ----
        for c in all_candidates:
            adj = 0.0
            want = "UP" if c["type"] == "CE" else "DOWN"
            spot_agree = 0
            if spot_dir in ("UP", "DOWN"):
                spot_agree = 1 if spot_dir == want else -1
                adj += SPOT_BONUS * spot_agree
            opp_chg = pe_total_oi_chg if c["type"] == "CE" else ce_total_oi_chg
            if opp_chg >= OPP_OI_MIN:
                adj += OPP_OI_BONUS
            lmc = leg_metrics.get(f"{c['type']}_{c['strike']}", {})
            exc = lmc.get("excess_ex_iv", lmc.get("excess_pct", 0.0))     # excess after removing the IV part
            if exc >= EXCESS_MIN_PCT:
                adj += EXCESS_BONUS
            elif exc <= -EXCESS_MIN_PCT:
                adj -= EXCESS_BONUS
            imb_now, imb_tr = lmc.get("bid_imb", 0.0), lmc.get("imb_trend", 0.0)
            if imb_now >= IMB_MIN and imb_tr >= 0:
                adj += IMB_BONUS                 # buyers dominate the book and it is not weakening
            elif imb_now <= -IMB_MIN:
                adj -= IMB_BONUS                 # a wall of sellers at the ask
            mp = structure["max_pain"]
            toward = 0
            if mp is not None and mp > live_spot * (1 + MAXPAIN_NEAR_PCT / 100.0):
                toward = 1
            elif mp is not None and mp < live_spot * (1 - MAXPAIN_NEAR_PCT / 100.0):
                toward = -1
            c["maxpain_dir"] = toward if c["type"] == "CE" else -toward   # +1 = max pain on the side this option profits from
            c["bid_imb"] = imb_now
            # price structure agrees or disagrees with this side
            smc_agree = 0
            if smc["bias"] in ("BULLISH", "BEARISH"):
                smc_agree = 1 if ((smc["bias"] == "BULLISH") == (c["type"] == "CE")) else -1
                adj += SMC_BONUS * smc_agree
            c["smc_agree"] = smc_agree
            htf_agree = 0
            if htf["bias"] in ("BULLISH", "BEARISH"):
                htf_agree = 1 if ((htf["bias"] == "BULLISH") == (c["type"] == "CE")) else -1
                adj += HTF_BONUS if htf_agree == 1 else -HTF_PENALTY
            c["htf_agree"] = htf_agree
            # premium acceleration: is the rise speeding up or fading?
            accel = lmc.get("accel_pct", 0.0)
            c["accel_pct"] = accel
            if accel >= ACCEL_MIN:
                adj += ACCEL_BONUS
            elif accel <= -ACCEL_MIN:
                adj -= ACCEL_PENALTY
            # 1-minute breakout of the spot, in ATR units (bonus only: it never holds an entry back)
            b_val = brk.get(c["type"])
            c["breakout_b"] = 0.0 if b_val is None else round(b_val, 2)
            if b_val is not None and b_val >= BREAK_MIN_B:
                adj += BREAK_BONUS
            # time decay while you hold
            tb = lmc.get("theta_burn_pct", 0.0)
            c["theta_burn_pct"] = tb
            if tb >= THETA_WARN_PCT:
                adj -= THETA_PENALTY
            # extra trap flags that need information from the whole scan
            extra = []
            if lmc.get("iv_chg", 0.0) >= IV_SPIKE_PTS and lmc.get("excess_ex_iv", 0.0) < EXCESS_MIN_PCT:
                extra.append("premium rise is mostly IV, not buying")
            if spot_agree == -1 and abs(spot_move_pct) >= 2 * SPOT_MIN_MOVE_PCT and c["prem_pct"] > 0:
                extra.append("premium rising against the spot move")
            other_side = "PE" if c["type"] == "CE" else "CE"
            olm = leg_metrics.get(f"{other_side}_{c['strike']}")
            if olm and olm["oi_pct"] <= -UNWIND_OI_MIN and olm["vol_ratio"] >= VOLUME_RATIO_MIN:
                extra.append("both CE and PE are unwinding (not directional)")
            if TRAP_ON_SWEEP and ((c["type"] == "CE" and smc.get("sweep_bear")) or
                                  (c["type"] == "PE" and smc.get("sweep_bull"))):
                extra.append("recent liquidity sweep against this side (possible fake move)")
            for flag in extra:
                c["trap_score"] += TRAP_EXTRA_SCORE
                c["trap_flags"] = list(c["trap_flags"]) + [flag]
            c["trap_level"] = trap_level(c["trap_score"])
            if c["z_vol"] >= Z_MIN and c["z_oi"] <= -Z_MIN:
                adj += Z_BONUS
            c["spot_dir"] = spot_dir
            c["spot_agree"] = spot_agree
            c["excess_pct"] = exc
            c["opp_oi_pct"] = round(opp_chg, 2)
            c["score"] = round(max(0.0, min(100.0, c["base_score"] + adj)), 1)

        # ---- early warnings: a heads-up before the OI gate confirms (tracked as its own entry style) ----
        scan_now = st.session_state.v40_scan_count
        taken = 0
        for ew in sorted(early_found, key=lambda e: -e["vol_ratio"]):
            if taken >= EARLY_MAX_PER_SCAN:
                break
            key_e = (ew["type"], ew["strike"])
            if scan_now - st.session_state.v40_early_last.get(key_e, -10 ** 9) < EARLY_COOLDOWN_SCANS:
                continue
            if spot_dir in ("UP", "DOWN") and spot_dir != ("UP" if ew["type"] == "CE" else "DOWN"):
                continue                      # the spot is moving against this side: no heads-up
            taken += 1
            st.session_state.v40_early_last[key_e] = scan_now
            feats_e = {k: 0.0 for k in LOGIT_FEATURES}
            feats_e.update({k: float(ew[k]) for k in
                            ("oi_pct", "vol_ratio", "prem_pct", "delta", "z_vol", "z_oi", "bid_imb", "iv_chg", "accel_pct")})
            tracker_open(st.session_state.v40_track, "EARLY",
                         {"type": ew["type"], "strike": ew["strike"], "score": 0.0}, ew["ltp"], scan_now, feats_e)
            st.session_state.v40_early_log.append({"time": datetime.datetime.now().strftime("%H:%M:%S"),
                                                   "type": ew["type"], "strike": ew["strike"],
                                                   "ltp": ew["ltp"], "scan": scan_now})
            if len(st.session_state.v40_early_log) > 30:
                st.session_state.v40_early_log.pop(0)
            if EARLY_ALERT:
                send_alert(f"V40 EARLY WARNING (not an entry signal): {ew['type']} {ew['strike']} at {ew['ltp']:.2f}")

        st.session_state.v40_cache = {
            "candidates": all_candidates, "rows": live_rows, "leg_metrics": leg_metrics,
            "spot_dir": spot_dir, "spot_move_pct": spot_move_pct, "log_rows": scan_log_rows,
            "structure": structure, "strike_step": strike_step, "smc": smc, "htf": htf, "brk": brk,
        }
    else:
        cache = st.session_state.v40_cache
        all_candidates = cache["candidates"]
        live_rows = cache["rows"]
        leg_metrics = cache["leg_metrics"]
        spot_dir = cache["spot_dir"]
        spot_move_pct = cache["spot_move_pct"]
        structure = cache["structure"]
        strike_step = cache["strike_step"]
        smc = cache["smc"]
        htf = cache["htf"]
        brk = cache["brk"]
        scan_log_rows = []          # already written when this scan was new

    current_scan = st.session_state.v40_scan_count

    st.caption(
        f"SPOT: {live_spot:.2f}  |  ATM: {atm_strike:.0f}  |  "
        f"STRIKES: {len(target_strikes)}  |  SCAN #{current_scan}  |  "
        f"SPOT MOVE ({SPOT_LOOKBACK_SCANS} scans): {spot_move_pct:+.3f}% {spot_dir}"
    )

    if current_scan < WARMUP_SCANS:
        st.info(f"Warm-up: {current_scan}/{WARMUP_SCANS} scans - signals are not counted yet.")

    # ==============================================================================
    # LIVE CHAIN (GATES 4/4 means all conditions pass)
    # ==============================================================================

    if live_rows:
        st.markdown("### LIVE OPTION CHAIN")
        st.dataframe(pd.DataFrame(live_rows), use_container_width=True, hide_index=True)

    # ==============================================================================
    # CE / PE BATTLE
    # ==============================================================================

    def best_of(side):
        items = [c for c in all_candidates if c["type"] == side]
        return max(items, key=lambda c: c["score"]) if items else None


    top_ce, top_pe = best_of("CE"), best_of("PE")

    st.markdown("### OPERATOR BATTLE ZONE")
    battle_rows = [
        {
            "SIDE": c["type"], "STRIKE": c["strike"], "SCORE": c["score"],
            "TRAP": c["trap_level"], "OI chg% (window)": c["oi_pct"], "VOL x": c["vol_ratio"],
            "PREM chg%": c["prem_pct"], "DELTA": c["delta"], "PERSIST": c["persistence"],
            "SPOT": c["spot_dir"], "OPP OI%": c["opp_oi_pct"],
            "SPREAD%": "" if c["spread_pct"] is None else c["spread_pct"],
        }
        for c in (top_ce, top_pe) if c
    ]
    if battle_rows:
        st.dataframe(pd.DataFrame(battle_rows), use_container_width=True, hide_index=True)
    else:
        st.info("No valid CE/PE candidate yet.")

    # ==============================================================================
    # FINAL DECISION
    # ==============================================================================

    strong = opposite = None
    strength_gap = 0.0

    if top_ce and top_pe:
        strong, opposite = (top_ce, top_pe) if top_ce["score"] >= top_pe["score"] else (top_pe, top_ce)
        strength_gap = strong["score"] - opposite["score"]
    else:
        strong = top_ce or top_pe

    # forget the confirmed side if it is too old
    last_side = st.session_state.v40_last_side
    if last_side and current_scan - st.session_state.v40_last_side_scan > SIDE_MEMORY_SCANS:
        last_side = None
        st.session_state.v40_last_side = None

    final_entry = None
    decision, reason = "NO ENTRY", "No valid operator candidate"

    if strong:
        if strong["trap_level"] == "HIGH":
            decision = "NO ENTRY"
            reason = "HIGH TRAP - " + "; ".join(strong["trap_flags"])
        elif strong["trap_level"] == "MEDIUM":
            decision = "WAIT"
            reason = "MEDIUM TRAP - " + "; ".join(strong["trap_flags"])
        elif opposite and strength_gap < MIN_GAP:
            decision = "BATTLE - NO ENTRY"
            reason = f"CE/PE score gap {strength_gap:.1f} < {MIN_GAP}"
        elif strong["score"] < MIN_ENTRY_SCORE:
            decision = "WAIT"
            reason = f"Score {strong['score']:.1f} < {MIN_ENTRY_SCORE}"
        elif last_side and last_side != strong["type"] and strength_gap < SIDE_FLIP_GAP:
            decision = "WAIT"
            reason = f"Side flip blocked - gap {strength_gap:.1f} < {SIDE_FLIP_GAP}"
        else:
            final_entry = strong
            decision = strong["action"]
            reason = "4 gates + persistence + low trap + score OK"
            st.session_state.v40_last_side = strong["type"]
            st.session_state.v40_last_side_scan = current_scan
            prev_sig = st.session_state.v40_last_signal
            fresh = (
                prev_sig is None
                or prev_sig.get("type") != strong["type"]
                or prev_sig.get("strike") != strong["strike"]
                or current_scan - prev_sig["scan"] > 1
            )
            st.session_state.v40_last_signal = {
                "text": f"{strong['action']} | strike {strong['strike']} | score {strong['score']:.1f}",
                "type": strong["type"],
                "strike": strong["strike"],
                "scan": current_scan,
                "time": datetime.datetime.now().strftime("%H:%M:%S"),
            }
            if fresh and is_new_scan:
                send_alert(f"V40 SIGNAL: {strong['action']} strike {strong['strike']} "
                           f"score {strong['score']:.1f} (spot {live_spot:.2f})")
                ltp_hist = st.session_state.v40_hist.get(
                    f"{strong['type']}_{strong['strike']}", {"ltp": [strong["ltp"]]})["ltp"]
                sig_feats = feats_of(strong)
                st.session_state.v40_watch = {
                    "type": strong["type"], "strike": strong["strike"],
                    "entry_scan": current_scan, "entry_ltp": strong["ltp"],
                    "low_vol_count": 0, "last_vol_scan": current_scan,
                    "base_ltp": min(ltp_hist[-(RUNUP_SCANS + 1):]), "peak": strong["ltp"],
                    "pb_done": False, "feats": sig_feats, "score": strong["score"],
                    "pattern": strong.get("pattern", "UNWIND"),
                }
                tracker_open(st.session_state.v40_track,
                             "IMMEDIATE" if strong.get("pattern", "UNWIND") == "UNWIND" else "IMMEDIATE_BUILDUP",
                             strong, strong["ltp"],
                             current_scan, sig_feats)

    # ==============================================================================
    # OUTPUT
    # ==============================================================================

    st.markdown("---")
    st.markdown("## FINAL OPERATOR DECISION")

    if final_entry:
        st.success(
            f"{final_entry['action']}  |  STRIKE {final_entry['strike']}  |  "
            f"SCORE {final_entry['score']:.1f}/100"
        )
        st.write(
            f"Trap: **{final_entry['trap_level']}**  |  "
            f"OI chg: **{final_entry['oi_pct']:.2f}%**  |  "
            f"Volume: **{final_entry['vol_ratio']:.2f}x**  |  "
            f"Premium: **{final_entry['prem_pct']:.2f}%**  |  "
            f"Delta: **{final_entry['delta']:.2f}**  |  "
            f"Persistence: **{final_entry['persistence']}**  |  "
            f"Spot: **{final_entry['spot_dir']}**  |  "
            f"Opposite OI: **{final_entry['opp_oi_pct']:+.2f}%**  |  "
            f"Pattern: **{final_entry['pattern']}**  |  "
            f"Price structure: **{'agrees' if final_entry['smc_agree'] == 1 else 'disagrees' if final_entry['smc_agree'] == -1 else 'neutral / n/a'}**  |  "
            f"15-min bias: **{'agrees' if final_entry['htf_agree'] == 1 else 'disagrees' if final_entry['htf_agree'] == -1 else 'neutral / n/a'}**  |  "
            f"Premium acceleration: **{final_entry['accel_pct']:+.2f}**  |  "
            f"1-min breakout: **{'n/a' if brk.get(final_entry['type']) is None else format(brk[final_entry['type']], '+.2f') + ' ATR'}**"
        )
    else:
        if decision == "BATTLE - NO ENTRY":
            st.warning(f"{decision}")
        elif decision == "WAIT":
            st.warning(f"{decision}")
        else:
            st.error(f"{decision}")
        st.caption(reason)

        # A signal lasts only as long as the gates hold, so keep the last one visible for a while.
        last_sig = st.session_state.get("v40_last_signal")
        if last_sig:
            age = current_scan - last_sig["scan"]
            if 0 < age <= SIGNAL_MEMORY_SCANS:
                st.info(
                    f"Last signal: {last_sig['text']} at {last_sig['time']} "
                    f"({age} scans ago) - NOT active now, re-check the live chain."
                )

    # ------------------------------------------------------------------------------
    # CE vs PE summary - always shows both sides and which one is stronger
    # ------------------------------------------------------------------------------
    def side_line(c, name):
        if not c:
            return f"{name}: no valid candidate"
        return (
            f"{name}: strike {c['strike']}, score {c['score']:.1f}, "
            f"trap {c['trap_level']}, persist {c['persistence']}"
        )


    stronger = strong["type"] if strong else "-"
    gap_text = f"{strength_gap:.1f}" if (top_ce and top_pe) else "n/a (only one side)"
    st.write(
        f"**{side_line(top_ce, 'CE')}**  |  **{side_line(top_pe, 'PE')}**  |  "
        f"Stronger side: **{stronger}**  |  Score gap: **{gap_text}**"
    )

    # ------------------------------------------------------------------------------
    # SIGNAL WATCH - tracks the last entry signal. Information only, NOT a stop-loss.
    # ------------------------------------------------------------------------------
    watch = st.session_state.v40_watch
    if watch and current_scan - watch["entry_scan"] > WATCH_MAX_SCANS:
        st.session_state.v40_watch = watch = None

    if watch:
        lm = leg_metrics.get(f"{watch['type']}_{watch['strike']}")
        if lm and is_new_scan and watch["last_vol_scan"] != current_scan:
            watch["low_vol_count"] = watch["low_vol_count"] + 1 if lm["vol_ratio"] < 1.0 else 0
            watch["last_vol_scan"] = current_scan
        st.markdown("### SIGNAL WATCH")
        if not lm:
            st.caption(f"{watch['type']} {watch['strike']}: strike is outside the scan range now - no data.")
        else:
            warn = []
            if lm["oi_pct"] >= WATCH_OI_RISE_PCT:
                warn.append("OI is rising again")
            if watch["low_vol_count"] >= 2:
                warn.append("volume has dried up")
            ltp_chg = (lm["ltp"] / watch["entry_ltp"] - 1.0) * 100.0 if watch["entry_ltp"] > 0 else 0.0
            if ltp_chg <= -WATCH_DROP_PCT:
                warn.append(f"LTP is {ltp_chg:.1f}% below the signal price")
            status = "HOLDING" if not warn else ("WEAKENING" if len(warn) == 1 else "SIGNAL FAILED - review")
            st.write(
                f"**{watch['type']} {watch['strike']}**  |  signal LTP {watch['entry_ltp']:.2f}  |  "
                f"now {lm['ltp']:.2f} ({ltp_chg:+.1f}%)  |  OI chg {lm['oi_pct']:+.2f}%  |  "
                f"Volume {lm['vol_ratio']:.2f}x  |  Status: **{status}**"
            )
            if warn:
                st.caption("Warnings: " + "; ".join(warn) + ".  (information only - not a stop-loss)")

    # ------------------------------------------------------------------------------
    # MARKET STRUCTURE - max pain, OI walls, volume migration, ATM IV  (context only)
    # ------------------------------------------------------------------------------
    st.markdown("### MARKET STRUCTURE")
    if structure["max_pain"] is not None:
        pcr_txt = "n/a" if structure["pcr"] is None else f"{structure['pcr']:.2f}"
        st.write(
            f"Max pain: **{structure['max_pain']:.0f}** ({structure['max_pain'] - live_spot:+.0f} pts from spot)  |  "
            f"biggest CALL OI: **{structure['call_wall']:.0f}** (resistance)  |  "
            f"biggest PUT OI: **{structure['put_wall']:.0f}** (support)  |  PCR: **{pcr_txt}**"
        )
    mig_sum = migration_summary(strike_step)
    if mig_sum is None:
        st.caption(f"Volume migration: needs {MIG_RECENT_SCANS + 3} scans of history.")
    else:
        parts = []
        for side_name in ("CE", "PE"):
            r_ = mig_sum.get(side_name)
            parts.append(f"{side_name} volume centre: n/a" if r_ is None else
                         f"{side_name} volume centre **{r_['now']:.0f}** (was {r_['prev']:.0f}, "
                         f"{r_['shift']:+.0f} pts, {r_['dir']})")
        st.write("  |  ".join(parts))
    atm_iv = atm_iv_summary(st.session_state.v40_hist, atm_strike)
    if atm_iv is not None:
        st.write(f"ATM implied volatility: **{atm_iv[0]:.1f}**  ({atm_iv[1]:+.1f} pts over the last {OI_LOOKBACK_SCANS} scans)")
    if smc["bias"] == "n/a":
        st.caption("Price structure (swings, breaks, sweeps): " + smc.get("why", "n/a"))
    else:
        st.write(f"Price structure ({smc['source']}): **{smc['bias']}**  |  "
                 + "; ".join(smc["events"] or ["no recent break or sweep"]))
    if htf["bias"] == "n/a":
        st.caption("15-minute bias: " + htf.get("why", "n/a"))
    else:
        st.write(f"15-minute bias ({htf['source']}): **{htf['bias']}**  |  "
                 + ("; ".join(htf["notes"]) or "no clear 15-min direction"))
    st.caption(f"The 15-minute bias never delays an entry: it adds {HTF_BONUS:.0f} points when it agrees and takes away "
               f"{HTF_PENALTY:.0f} when it disagrees. The 30-second engine decides.")
    st.caption("Context only - it does not block or approve a signal. Max pain is a theory: evidence that price is "
               "pulled to it is mixed and it matters mostly on expiry day. Volume migration covers the scanned "
               "strikes only. A falling IV usually comes from time and events, not from one operator.")

    # ------------------------------------------------------------------------------
    # EARLY WARNINGS - a heads-up before the OI gate confirms (NOT an entry signal)
    # ------------------------------------------------------------------------------
    st.markdown("### EARLY WARNINGS (a heads-up, NOT an entry signal)")
    ew_log = st.session_state.v40_early_log[-EARLY_SHOW:]
    if not ew_log:
        st.caption("None yet. A warning appears when the volume jumps and the premium starts rising on a strike "
                   "while OI has not confirmed yet.")
    else:
        ew_rows = []
        for e_ in reversed(ew_log):
            now_l = leg_metrics.get(f"{e_['type']}_{e_['strike']}", {}).get("ltp")
            ew_rows.append({"TIME": e_["time"], "SIDE": e_["type"], "STRIKE": e_["strike"],
                            "PRICE THEN": e_["ltp"], "PRICE NOW": "" if now_l is None else now_l,
                            "CHANGE %": "" if not now_l else round((now_l / e_["ltp"] - 1.0) * 100.0, 1),
                            "SCANS AGO": current_scan - e_["scan"]})
        st.dataframe(pd.DataFrame(ew_rows), use_container_width=True, hide_index=True)
    st.caption("An early warning comes BEFORE the OI confirms, so it is earlier but less reliable. Every one is "
               "tracked in the EDGE TRACKER as entry style EARLY - trade them only if their EV there is positive.")

    # ------------------------------------------------------------------------------
    # ENTRY TIMING, FORMULAS and PRICE-WISE VOLUME for the focus strike
    # ------------------------------------------------------------------------------
    focus_key = None
    if watch:
        focus_key = f"{watch['type']}_{watch['strike']}"
    elif strong:
        focus_key = f"{strong['type']}_{strong['strike']}"
    f_lm = leg_metrics.get(focus_key) if focus_key else None
    f_h = st.session_state.v40_hist.get(focus_key) if focus_key else None

    if watch and f_lm and f_h:
        watch.setdefault("base_ltp", watch["entry_ltp"])
        watch.setdefault("peak", watch["entry_ltp"])
        watch.setdefault("pb_done", False)
        watch.setdefault("feats", {k: 0.0 for k in LOGIT_FEATURES})
        watch.setdefault("score", 0.0)
        if is_new_scan:
            watch["peak"] = max(watch["peak"], f_lm["ltp"])
        t_state, run_pct, retrace = entry_timing(watch, f_lm["ltp"], f_lm["oi_pct"])
        rr, swing_low = risk_reward(f_h, f_lm["ltp"], max(watch["peak"] - watch["base_ltp"], 0.0))

        # second entry style for the edge tracker: the first time the pullback zone appears
        if is_new_scan and t_state.startswith("ENTRY ZONE") and not watch["pb_done"]:
            watch["pb_done"] = True
            tracker_open(st.session_state.v40_track,
                         "PULLBACK" if watch.get("pattern", "UNWIND") == "UNWIND" else "PULLBACK_BUILDUP",
                         {"type": watch["type"], "strike": watch["strike"], "score": watch["score"]},
                         f_lm["ltp"], current_scan, watch["feats"])

        notes = []
        if f_lm["iv_chg"] >= IV_SPIKE_PTS:
            notes.append(f"IV is up {f_lm['iv_chg']:.1f} pts - part of the premium rise is IV and can fade fast")
        if f_lm["avg_gap_pct"] is not None and f_lm["avg_gap_pct"] >= ATP_EXPENSIVE_PCT:
            notes.append(f"LTP is {f_lm['avg_gap_pct']:.1f}% above the day's average price - expensive")
        if rr is not None and rr < MIN_RR:
            notes.append(f"risk:reward {rr:.1f} is below {MIN_RR}")
        if f_lm["spread_pct"] is not None and f_lm["spread_pct"] > MAX_SPREAD_PCT:
            notes.append(f"wide bid-ask spread ({f_lm['spread_pct']:.1f}%)")
        if f_lm["excess_ex_iv"] < EXCESS_MIN_PCT:
            notes.append("no excess premium after removing IV - the rise is only following the spot move")
        if f_lm["bid_imb"] <= -IMB_MIN:
            notes.append(f"sellers dominate the order book (imbalance {f_lm['bid_imb']:+.2f})")
        if f_lm["theta_burn_pct"] >= THETA_WARN_PCT:
            notes.append(f"time decay is {f_lm['theta_burn_pct']:.1f}% of the premium over {THETA_HOLD_MIN} min")

        st.markdown("### ENTRY TIMING")
        st.write(
            f"**{t_state}**  |  run-up {run_pct:.1f}%  |  pullback from peak {retrace:.0f}%  |  "
            f"swing low {swing_low:.2f}  |  risk:reward (indicative) {'n/a' if rr is None else f'{rr:.1f}'}"
        )
        if notes:
            st.caption("Checks: " + "; ".join(notes) + ".")
        else:
            st.caption("Checks: IV, price vs average, risk:reward, spread and excess premium all look fine.")

    if f_lm:
        st.markdown("### FORMULAS (focus strike " + str(focus_key) + ")")
        st.write(
            f"Excess premium: **{f_lm['excess_pct']:+.2f}%**  |  "
            f"z-volume: **{f_lm['z_vol']:.1f}**  |  z-OI: **{f_lm['z_oi']:.1f}**  |  "
            f"IV change: **{f_lm['iv_chg']:+.1f} pts**"
        )
        st.caption("Excess premium = premium change minus what delta and gamma say the spot move explains. "
                   "z-scores compare this scan with the strike's own history (2 or more = unusual).")
        st.write(
            f"Excess without the IV part: **{f_lm['excess_ex_iv']:+.2f}%**  (IV/vega part: {f_lm['iv_part_pct']:+.2f}%)  |  "
            f"Bid/ask imbalance: **{f_lm['bid_imb']:+.2f}** (trend {f_lm['imb_trend']:+.2f})"
        )
        st.write("IV versus premium: **" + iv_state(f_lm["prem_win_pct"], f_lm["iv_chg"]) + "**")
        st.write(
            f"Theta cost for {THETA_HOLD_MIN} min: **{f_lm['theta_burn_pct']:.1f}%** of the premium  |  "
            f"the spot must move about **{f_lm['theta_be_pts']:.0f} points** just to cover it"
        )
        st.caption("Theta is rough: decay is not even through the day and it speeds up near expiry.")
        f_side = str(focus_key).split("_")[0]
        f_brk = brk.get(f_side)
        st.write(
            f"Premium acceleration: **{f_lm['accel_pct']:+.2f}** (positive = the rise is speeding up)  |  "
            f"1-minute spot breakout ({brk['source']}): **{'n/a' if f_brk is None else format(f_brk, '+.2f') + ' ATR'}**"
        )
        st.caption("Breakout = (1-minute close - highest high of the last " + str(BREAK_LOOKBACK_BARS) + " bars) / ATR for CE, "
                   "mirrored for PE. It is built from 30-second spot samples unless your app has a df_1 frame, so it is coarse.")
        st.caption("Imbalance = (bid qty - ask qty) / (bid qty + ask qty) at the best bid/ask only; it can be faked and "
                   "changes within seconds, so it is a hint, never a signal. Vega part needs vega from the API.")

        vap_top, vap_sum = vap_summary(focus_key, f_lm["ltp"])
        st.markdown("### PRICE-WISE VOLUME (estimated)")
        if vap_top is None:
            st.info("No volume recorded for this strike yet.")
        else:
            view = vap_top[["price", "vol", "buy", "sell", "share", "note"]].rename(columns={
                "price": "PRICE", "vol": "VOLUME", "buy": "BUY (est)", "sell": "SELL (est)",
                "share": "SHARE %", "note": "NOTE"})
            st.dataframe(view.round(0).assign(PRICE=vap_top["price"].values),
                         use_container_width=True, hide_index=True)
            ba = "n/a" if vap_sum["buyer_avg"] is None else f"{vap_sum['buyer_avg']:.2f}"
            sa = "n/a" if vap_sum["seller_avg"] is None else f"{vap_sum['seller_avg']:.2f}"
            st.write(
                f"Most traded price (POC): **{vap_sum['poc']:.2f}**  |  LTP **{f_lm['ltp']:.2f}**  |  "
                f"net buying: **{vap_sum['net_buy_pct']:+.0f}%**  |  "
                f"avg buy price **{ba}**  |  avg sell price **{sa}**"
            )
            bursts = st.session_state.v40_bursts.get(focus_key, [])[-5:]
            if bursts:
                st.caption("Recent heavy bursts (scan volume >= " + f"{BURST_RATIO:.0f}x average): " + "; ".join(
                    f"{b['time']} {b['qty']:.0f} @ {b['price']:.2f} ({b['side']})" for b in bursts))
            st.caption("Estimated from 30-second snapshots and the best bid/ask. It shows where volume traded and "
                       "who probably pushed it - it cannot tell who the buyer or seller was.")

    # edge tracker: move open signals forward and store the finished ones
    if is_new_scan:
        write_v40_outcomes(tracker_update(st.session_state.v40_track, leg_metrics, current_scan))

    # ------------------------------------------------------------------------------
    # CSV log (one write per real scan)
    # ------------------------------------------------------------------------------
    if is_new_scan:
        write_logs(scan_log_rows, {
            "time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "scan": current_scan, "spot": live_spot, "spot_dir": spot_dir,
            "decision": decision, "reason": reason,
            "side": strong["type"] if strong else "", "strike": strong["strike"] if strong else "",
            "score": strong["score"] if strong else "",
            "trap": strong["trap_level"] if strong else "",
            "ce_best_score": top_ce["score"] if top_ce else "",
            "pe_best_score": top_pe["score"] if top_pe else "",
        })

    # ------------------------------------------------------------------------------
    # EDGE TRACKER - is this signal worth trading? (expected value from YOUR own history)
    # ------------------------------------------------------------------------------
    st.markdown("### EDGE TRACKER")
    st.caption(
        f"Every signal is judged {EV_HORIZON_SCANS} scans later: HIT = premium +{EV_TARGET_PCT:.0f}% first, "
        f"MISS = -{EV_STOP_PCT:.0f}% first. IMMEDIATE = entry at the signal, PULLBACK = entry when the pullback "
        f"zone appears, EARLY = entry at an early warning. EV = average result per trade; the last column subtracts an ASSUMED cost of {EV_COST_PCT:.1f}% per trade (set your own)."
    )
    out_df = load_outcomes()
    open_n = len(st.session_state.v40_track)
    if out_df is None:
        st.info(f"No finished signals yet ({open_n} being tracked). Results appear {EV_HORIZON_SCANS} scans "
                f"({EV_HORIZON_SCANS // 2} min at 30s) after a signal, and add up every day.")
    else:
        et = edge_table(out_df)
        st.dataframe(et, use_container_width=True, hide_index=True)
        imm = et[et["ENTRY STYLE"] == "IMMEDIATE"]
        if len(imm):
            n_imm, ev_imm = int(imm.iloc[0]["SIGNALS"]), float(imm.iloc[0]["EV after costs %"])
            if n_imm < EV_MIN_SIGNALS:
                st.warning(f"Only {n_imm} finished IMMEDIATE signals - too early to conclude (need {EV_MIN_SIGNALS}).")
            elif ev_imm <= 0:
                st.error(f"EV after costs is {ev_imm:+.2f}% per trade over {n_imm} signals: on your own history these signals "
                         "lose money on average. Do not trade them as they are - tune the settings first.")
            else:
                st.success(f"EV after an assumed cost of {EV_COST_PCT:.1f}% per trade is {ev_imm:+.2f}% over {n_imm} signals.")

        # logistic model: retrained only when new finished signals arrive
        cache = st.session_state.get("v40_logit_cache")
        if cache is None or cache[0] != len(out_df):
            cache = (len(out_df), fit_logit(out_df))
            st.session_state.v40_logit_cache = cache
        model = cache[1]
        if not model["ok"]:
            st.caption("Probability model: " + model["why"] + ".")
        else:
            st.caption(f"Probability model trained on {model['n']} signals - test accuracy {model['acc'] * 100:.0f}% "
                       f"vs {model['base'] * 100:.0f}% for always guessing the majority; HIT rate {model['hit_rate'] * 100:.0f}%.")
            if not model["useful"]:
                st.warning("The model is not better than guessing yet - ignore its number.")
            elif strong:
                st.write(f"Model P(HIT) for the current signal: **{model_p_hit(model, feats_of(strong)) * 100:.0f}%**")

    # ==============================================================================
    # This engine checks conditions from option-chain data. It does not guarantee operator intent
    # or future direction. Keep stop-loss / risk control separate.
    # ==============================================================================


v40_run()
