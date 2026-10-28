"""Chachni (bananaat data var). Chalva:  python3 test_terminal.py"""
from datetime import datetime

import numpy as np
import pandas as pd

import samadhan_terminal as L

cfg = L.Cfg()


def make_candles(closes, seed=1, tick=4.0, start="2026-10-26 09:15"):
    rng = np.random.default_rng(seed)
    closes = np.asarray(closes, float)
    opens = np.r_[closes[0], closes[:-1]]
    hi = np.maximum(opens, closes) + rng.uniform(0.2, 1.0, len(closes)) * tick
    lo = np.minimum(opens, closes) - rng.uniform(0.2, 1.0, len(closes)) * tick
    ts = pd.date_range(start, periods=len(closes), freq="5min")
    return pd.DataFrame({"timestamp": ts, "open": opens, "high": hi, "low": lo, "close": closes, "volume": 0.0})


def reversal_series(seed=3, n_down=120, n_up=60, mirror=False):
    rng = np.random.default_rng(seed)
    down = 25000 - np.cumsum(np.abs(rng.normal(1.0, 1.5, n_down)) + rng.normal(0, 3, n_down)) * 0.6
    up = down[-1] + np.cumsum(np.abs(rng.normal(3.5, 2.0, n_up)))
    c = np.r_[down, up]
    return (2 * c[0] - c) if mirror else c


# 1) bars_since_cross
a = pd.Series([1, 1, -1, -1, 1, 1.0])
b = pd.Series([0, 0, 0, 0, 0, 0.0])
assert L.bars_since_cross(a, b, "UP", 3) == 1, "cross 1 candle aadhi zala"
assert L.bars_since_cross(a, b, "UP", 1) is None, "lookback 1 madhe nahi"
assert L.bars_since_cross(a, b, "DOWN", 3) is None, "ulti bajula"
print("1 OK bars_since_cross")

# 2) signal ULTA-TURN javal yeto, trend madhye mothya nantar nahi (taaja cross, state nahi)
for mirror, want in ((False, "UP"), (True, "DOWN")):
    c = reversal_series(mirror=mirror)
    fired = []
    for t in range(70, len(c) + 1):
        s = L.evaluate_signal(make_candles(c[:t]), cfg)
        if s["direction"] == want:
            fired.append(t)
    assert fired, f"{want}: ekda pan signal ala nahi (bananat reversal var)"
    first = fired[0]
    # turn pasun (index 120) kiti candle nantar pahila signal?
    print(f"2 {want}: pahila signal turn nantar {first - 120} candle ni; ekun signal candles {len(fired)}")
    late = L.evaluate_signal(make_candles(c), cfg)
    assert late["direction"] == "WAIT", f"{want}: 60 candle chya trend nantar pan signal (stale)!"
print("2 OK taaja cross, stale nahi")

# 3) shaant (sideways) bajar = signal nahi
rng = np.random.default_rng(7)
flat = 25000 + np.cumsum(rng.normal(0, 2, 200)) * 0.2
flat = 25000 + (flat - flat.mean())
s = L.evaluate_signal(make_candles(flat), cfg)
assert s["direction"] == "WAIT"
print("3 OK sideways = WAIT")

# 4) apurna candle kadhne
df = make_candles(reversal_series())
now_mid = pd.Timestamp(df["timestamp"].iloc[-1]) + pd.Timedelta(minutes=2)
now_after = pd.Timestamp(df["timestamp"].iloc[-1]) + pd.Timedelta(minutes=6)
d1, n1 = L.drop_forming_candle(df, 5, now_mid.to_pydatetime())
d2, n2 = L.drop_forming_candle(df, 5, now_after.to_pydatetime())
assert len(d1) == len(df) - 1 and len(d2) == len(df)
d3, n3 = L.drop_forming_candle(df.drop(columns=["timestamp"]), 5, now_mid.to_pydatetime())
assert len(d3) == len(df) and "timestamp" in n3
print("4 OK forming candle:", n1, "|", n2)

# 5) option nivad ani liquidity filter
oc = {}
for k in range(24800, 25250, 50):
    oc[f"{k:.6f}"] = {
        "ce": {"last_price": max(1, 25000 - k + 80) / 1.0, "oi": 800000, "volume": 1000, "top_bid_price": 99.0,
               "top_ask_price": 100.0, "greeks": {"delta": 0.52}},
        "pe": {"last_price": max(1, k - 25000 + 80) / 1.0, "oi": 20000, "volume": 10, "top_bid_price": 60.0,
               "top_ask_price": 80.0, "greeks": {"delta": -0.48}},
    }
ce = L.pick_option(oc, 25003, "UP", 50, cfg)
assert ce["ok"] and ce["strike"] == 25000 and ce["side"] == "ce", ce
pe = L.pick_option(oc, 25003, "DOWN", 50, cfg)
assert not pe["ok"] and any("OI" in i for i in pe["issues"]) and any("spread" in i for i in pe["issues"]), pe
itm = L.pick_option(oc, 25003, "UP", 50, cfg, itm_steps=1)
assert itm["strike"] == 24950
print("5 OK option nivad:", ce["strike"], "| PE nakarla:", pe["issues"])

# 6) trade plan gannit
opt = {"ask": 100.0, "ltp": 99.5, "delta": 0.5}
p = L.trade_plan(opt, atr_u=40.0, cfg=cfg, lot_size=65, capital=100000, risk_pct=1.0)
# SL = 100 - 0.5*1*40 = 80 ; floor = 70 ; sl=80 ; risk 20 ; t1 130 ; t2 160 ; lots = floor(1000 / (20*65)) = 0
assert p["sl"] == 80.0 and p["t1"] == 130.0 and p["t2"] == 160.0 and p["lots"] == 0 and not p["ok"], p
p2 = L.trade_plan(opt, 40.0, cfg, 65, 500000, 1.0)   # budget 5000 -> 5000//1300 = 3
assert p2["lots"] == 3 and p2["max_loss"] == 3900 and p2["ok"], p2
p3 = L.trade_plan(opt, 400.0, cfg, 65, 500000, 1.0)  # ATR motha -> SL floor 30% = 70
assert p3["sl"] == 70.0 and p3["sl_pct"] == 30.0, p3
print("6 OK plan:", p2["entry"], p2["sl"], p2["t1"], p2["t2"], "lots", p2["lots"], "| lot nahi:", p["note"][:40])

# 7) tracker
o = {"strike": 25000.0, "side": "ce"}
t = L.new_trade("NIFTY", "UP", "2026-10-27", o, p2, 0.0, "c1")
L.update_trade(t, 131, 60, False, 120)
assert t["status"] == "T1 HIT" and t["sl"] == 100.0
L.update_trade(t, 99, 120, False, 120)
assert t["status"] == "BE EXIT"
t2 = L.new_trade("NIFTY", "UP", "2026-10-27", o, p2, 0.0, "c2"); L.update_trade(t2, 79, 30, False, 120)
assert t2["status"] == "SL HIT"
t3 = L.new_trade("NIFTY", "UP", "2026-10-27", o, p2, 0.0, "c3"); L.update_trade(t3, 161, 30, False, 120)
assert t3["status"] == "T2 HIT"
t4 = L.new_trade("NIFTY", "UP", "2026-10-27", o, p2, 0.0, "c4"); L.update_trade(t4, 101, 99999, False, 120)
assert t4["status"] == "EXPIRED"
sm = L.summarize([t, t2, t3, t4])
assert sm["wins"] == 2 and sm["losses"] == 1 and abs(sm["win_rate"] - 66.666) < 0.1 and sm["undecided"] == 1, sm
print("7 OK tracker:", sm)

# 8) cooldown ani dedupe
assert not L.cooldown_ok([t], "NIFTY", "UP", 100.0, 15, "other")      # 15 min aat
assert L.cooldown_ok([t], "NIFTY", "UP", 100000.0, 15, "other")
assert not L.cooldown_ok([t], "NIFTY", "UP", 100000.0, 15, "c1")      # tich candle
assert L.cooldown_ok([t], "NIFTY", "DOWN", 100.0, 15, "x")            # ulti disha chalte
print("8 OK cooldown")

# 9) vel ani expiry
assert L.in_trade_window(datetime(2026, 10, 27, 10, 0), "09:30", "15:00")
assert not L.in_trade_window(datetime(2026, 10, 27, 9, 20), "09:30", "15:00")
assert not L.in_trade_window(datetime(2026, 10, 24, 10, 0), "09:30", "15:00")   # shanivar
assert L.extract_dates({"status": "success", "data": {"data": ["2026-10-27", "2026-11-03"]}}) == ["2026-10-27", "2026-11-03"]
assert L.extract_dates(["2026-11-03T00:00:00", "x", 5]) == ["2026-11-03"]
print("9 OK vel/expiry")

# 10) OI flow: quadrants
s = L.flow_score("CE", 25000, 25000, 5.0, 5.0, None, 0)       # CE long build-up -> UP
assert s["direction"] == "UP" and s["score"] > 0, s
s = L.flow_score("CE", 25000, 25000, 5.0, -5.0, None, 0)      # CE writing -> DOWN
assert s["direction"] == "DOWN", s
s = L.flow_score("PE", 25000, 25000, 5.0, 5.0, None, 0)       # PE long build-up -> DOWN
assert s["direction"] == "DOWN", s
s = L.flow_score("PE", 25000, 25000, 5.0, -5.0, None, 0)      # PE writing -> UP
assert s["direction"] == "UP", s
s = L.flow_score("PE", 25000, 25000, -5.0, 5.0, None, 0)      # PE short covering -> DOWN
assert s["direction"] == "DOWN", s
s = L.flow_score("CE", 25000, 25000, -5.0, -5.0, None, 0)     # CE unwinding -> DOWN
assert s["direction"] == "DOWN", s
assert L.flow_score("CE", 25000, 25000, 0.2, 0.2, None, 0)["score"] == 0       # noise
near = L.flow_score("CE", 25000, 25000, 5, 5, None, 0)["score"]
far = L.flow_score("CE", 26000, 25000, 5, 5, None, 0)["score"]
assert far < near, (near, far)
print("10 OK flow quadrants / noise / distance")

# 11) flow_metrics: real window, history gating
st = {}
k = L.flow_key("NIFTY", "2026-10-30", 25000, "ce")
for i in range(0, 600, 30):       # 10 min, OI +10%, premium +10% over time
    L.flow_update(st, k, 1000 + i, 100 * (1 + i / 6000), 100000 * (1 + i / 6000), 1000 + i * 2, 12)
m = L.flow_metrics(st[k], 5.0)
assert m and 4.5 < m["span_min"] < 6 and m["oi_pct"] > 0 and m["prem_pct"] > 0, m
assert L.flow_metrics(st[k][:3], 5.0) is None                 # too little history
L.flow_update(st, k, 1000 + 570 + 2, 1, 1, 1)                  # 5s dedupe
assert len(st[k]) == 20
print("11 OK flow_metrics:", {a: round(b, 2) if b else b for a, b in m.items()})

# 12) rank gating (min OI / min LTP) + confirm
oc2 = {"25000.000000": {"ce": {"last_price": 120.0, "oi": 900000, "volume": 5000},
                        "pe": {"last_price": 2.0, "oi": 900000, "volume": 5}},
       "25050.000000": {"ce": {"last_price": 90.0, "oi": 1000, "volume": 5}, "pe": {}}}
st2 = {}
for i in range(0, 600, 30):
    for strike, side, ltp0, oi0 in ((25000, "ce", 100, 800000), (25000, "pe", 2, 800000), (25050, "ce", 80, 900)):
        L.flow_update(st2, L.flow_key("NIFTY", "e", strike, side), 5000 + i, ltp0 * (1 + i / 3000),
                      oi0 * (1 + i / 3000), 10 * i)
oc_now = {"25000.000000": {"ce": {"last_price": 120.0, "oi": 960000}, "pe": {"last_price": 2.4, "oi": 960000}},
          "25050.000000": {"ce": {"last_price": 96.0, "oi": 1080}, "pe": {}}}
rank = L.flow_rank(st2, "NIFTY", "e", oc_now, 25010, L.Cfg(flow_min_score=10.0), 50)
assert [(r["strike"], r["side"]) for r in rank] == [(25000.0, "ce")], rank   # PE: LTP<5, 25050: OI small
assert rank[0]["direction"] == "UP"
top = [(0, "a"), (60, "a"), (130, "a")]
assert L.flow_confirmed(top, "a", 130, 2.0) and not L.flow_confirmed(top, "a", 100, 2.0)
assert not L.flow_confirmed([(0, "b"), (60, "a")], "a", 200, 2.0)
assert L.flow_relation("UP", "UP") == "agree" and L.flow_relation("UP", "DOWN") == "oppose" and L.flow_relation(None, "UP") == "none"
print("12 OK flow_rank gating + confirm")

# 13) CAS v2: aadhi-oLakh radar + sarvottam hedge
def scenario(side, spot_drift_pct=0.0, prem_drop=0.04, oi_rise=0.06, burst=True):
    st3, base_spot = {}, 25000.0
    for i in range(0, 780, 30):
        ts = 9000 + i
        f = i / 780
        late = i >= 600                                  # shevtchi 3 minute: veg vadhto
        oi = 800000 * (1 + oi_rise * f * (1.0 if not late else 1.0) + (0.02 if (late and oi_rise > 0) else 0))
        ltp = 100 * (1 - prem_drop * f)
        vol = i * 10 + (60 * (i - 420) if (burst and i >= 420) else 0)
        L.flow_update(st3, L.flow_key("NIFTY", "e", 25100 if side == "ce" else 24900, side), ts, ltp, oi, vol)
        sp = base_spot * (1 + spot_drift_pct / 100 * f)
        st3.setdefault("__spot__NIFTY", []).append((ts, sp))
    return st3, base_spot * (1 + spot_drift_pct / 100)

def mk_oc(spot):
    import math
    oc = {}
    for k in range(24500, 25600, 50):
        tv = 70 * math.exp(-abs(k - spot) / 200) + 2
        ce, pe = max(spot - k, 0) + tv, max(k - spot, 0) + tv
        mk = lambda p, oi: {"last_price": round(p, 2), "oi": oi, "top_bid_price": round(p * 0.995, 2), "top_ask_price": round(p * 1.005, 2)}
        oc[f"{k:.6f}"] = {"ce": mk(ce, 900000), "pe": mk(pe, 900000)}
    return oc
cf = L.Cfg(min_oi=50000)

# a) spot ajun halla nahi -> EARLY/STRONG, BEARISH (CE writing)
stA, spA = scenario("ce", 0.0)
cA = L.cas_detect(stA, "NIFTY", "e", mk_oc(spA), spA, cf, 50)
assert cA and cA[0]["stage"] in ("EARLY", "STRONG") and cA[0]["bias"] == "BEARISH" and cA[0]["strike"] == 25100, cA
# b) PE writing -> BULLISH
stB, spB = scenario("pe", 0.0)
cB = L.cas_detect(stB, "NIFTY", "e", mk_oc(spB), spB, cf, 50)
assert cB and cB[0]["bias"] == "BULLISH" and cB[0]["side"] == "pe", cB
# c) spot aadhich khali gela (bearish saathi) -> LATE
stC, spC = scenario("ce", -0.8)
cC = L.cas_detect(stC, "NIFTY", "e", mk_oc(spC), spC, cf, 50)
assert cC and cC[0]["stage"] == "LATE", cC
# d) premium vadhla (buyers) -> writing nahi
stD, spD = scenario("ce", 0.0, prem_drop=-0.05)
assert not [x for x in L.cas_detect(stD, "NIFTY", "e", mk_oc(spD), spD, cf, 50) if x["strike"] == 25100]
# e) OI vadhla nahi -> kahi nahi
stE, spE = scenario("ce", 0.0, oi_rise=0.0)
assert not [x for x in L.cas_detect(stE, "NIFTY", "e", mk_oc(spE), spE, cf, 50) if x["strike"] == 25100]
print("13a OK CAS stages:", cA[0]["stage"], cA[0]["score"], "|", "; ".join(cA[0]["reasons"]))

# f) sarvottam hedge: purn chain madhun, liquid, jokhim limit madhe
oc = mk_oc(spA)
sp = L.cas_spread(cA[0], oc, 50, 65, 1500000, 1.0, cf)
assert sp["ok"] and sp["hedge"] > sp["sell"] and sp["credit"] > 0 and sp["lots"] >= 1 and sp["alts"], sp
best_rr = max(a["rr"] for a in sp["alts"]); assert abs(sp["rr"] - round(best_rr, 2)) < 1e-9, (sp["rr"], best_rr)
# najik cha hedge (25150) illiquid kela -> to nivadla jau nahi
oc["25150.000000"]["ce"]["oi"] = 100
sp2 = L.cas_spread(cA[0], oc, 50, 65, 1500000, 1.0, cf)
assert sp2["ok"] and sp2["hedge"] != 25150 and all(a["hedge"] != 25150 for a in sp2["alts"]), sp2
# jokhim limit khup kami -> nakar
sp3 = L.cas_spread(cA[0], oc, 50, 65, 20000, 0.5, cf)
assert not sp3["ok"] and any("jokhim" in i for i in sp3["issues"]), sp3
# PE bajula hedge khali
sp4 = L.cas_spread(cB[0], mk_oc(spB), 50, 65, 1500000, 1.0, cf)
assert sp4["ok"] and sp4["hedge"] < sp4["sell"] and sp4["name"].startswith("BULL PUT"), sp4
print("13b OK hedge:", sp["name"], "sell", sp["sell"], "hedge", sp["hedge"], "credit", sp["credit"], "rr", sp["rr"],
      "| alts:", [(a["hedge"], a["credit"]) for a in sp["alts"]])
# 14) volume ratio (adhicha saravaasari veg vs ata)
stv = {}
for i in range(0, 1800, 30):
    L.flow_update(stv, "x", 5000 + i, 100, 1000000, i * 10 if i < 1200 else 12000 + (i - 1200) * 40)
mv = L.flow_metrics(stv["x"], 5.0)
assert mv["vol_ratio"] and 2.4 < mv["vol_ratio"] < 2.8, mv   # adhicha saravaasari veg 16/s, ata 40/s
print("14 OK volume ratio:", round(mv["vol_ratio"], 2))
# 15) regression: spot itihas index pramane vegla; ITM sell nahi; breakeven spot chya aat nasava
stX = {}
for i in range(0, 300, 30):
    L.flow_ingest(stX, "NIFTY", "e", {}, 25000.0, L.Cfg(), 50, 100 + i)
    L.flow_ingest(stX, "SENSEX", "e", {}, 73000.0, L.Cfg(), 100, 100 + i)
assert abs(L.spot_move_pct(stX, 3, "SENSEX") or 0) < 0.01 and abs(L.spot_move_pct(stX, 3, "NIFTY") or 0) < 0.01
stX.setdefault("__spot__BAD", []).extend([(1, 1.0), (2, 1.0), (400, 2.0)])
assert L.spot_move_pct(stX, 3, "BAD") is None                      # 100% jump = data chukicha
oc_itm = mk_oc(25000.0)
assert not [x for x in L.cas_detect(*scenario("ce", 0.0)[:1], "NIFTY", "e", oc_itm, 25400.0, cf, 50) if x["strike"] == 25100]   # 25100 CE ata ITM (spot 25400)
c_bad = {"strike": 25100.0, "side": "ce", "spot": 25200.0}                                  # breakeven > 25100 pan spot 25200 chya khali
r_bad = L.cas_spread(c_bad, mk_oc(25000.0), 50, 65, 1500000, 1.0, cf)
assert not r_bad["ok"] and any("breakeven" in i for i in r_bad["issues"]), r_bad
print("15 OK regression: spot per-index, ITM nako, breakeven check")
print("\nSAGLYA CHACHNYA PASS")
