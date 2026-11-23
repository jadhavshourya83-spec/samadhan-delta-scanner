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
# 16) spot-neutral premium: spot khali gela, PE premium delta pramane vadhla -> flow nahi (noise)
stN = {}
kk = L.flow_key("NIFTY", "e", 25000, "pe")
for i in range(0, 420, 30):
    spot_i = 25000 - i * 0.5                      # spot 210 pt khali
    ltp_i = 100 + 0.5 * (25000 - spot_i)          # PE delta -0.5 => premium +0.5 x spot padzad
    L.flow_update(stN, kk, 7000 + i, ltp_i, 1000000 * (1 + 0.012 * i / 420), 1000, 12, spot_i, -0.5)
mN = L.flow_metrics(stN[kk], 5.0)
assert mN["prem_raw"] > 5 and abs(mN["prem_pct"]) < 0.5, mN
# ani kharya PE long build-up (premium delta peksha jast vadhla) -> ajun disto
stP = {}
for i in range(0, 420, 30):
    spot_i = 25000 - i * 0.5
    ltp_i = 100 + 0.5 * (25000 - spot_i) + i * 0.15
    L.flow_update(stP, kk, 7000 + i, ltp_i, 1000000 * (1 + 0.05 * i / 420), 1000, 12, spot_i, -0.5)
mP = L.flow_metrics(stP[kk], 5.0)
assert mP["prem_pct"] > mN["prem_pct"] + 3, (mP, mN)
print("16 OK spot-neutral premium: kaccha", round(mN["prem_raw"], 1), "% -> spot vagalun", round(mN["prem_pct"], 2), "%")
# 17) junya (5-tuple) itihasatahi spot vagalun chalte, spot_hist + aajcha delta vaparun
stO = {}
spot_h = []
for i in range(0, 420, 30):
    spot_i = 25000 - i * 0.5
    ts_i = 7000 + i
    spot_h.append((ts_i, spot_i))
    stO.setdefault(kk, []).append((ts_i, 100 + 0.5 * (25000 - spot_i), 1000000, 1000, 12))   # junya padhatichi 5-tuple nond (spot/delta nahi)
h_last = stO[kk][-1]
stO[kk][-1] = h_last + (spot_h[-1][1], -0.5)                                              # shevtchi nond navin (7-tuple)
mO = L.flow_metrics(stO[kk], 5.0, spot_hist=spot_h)
assert mO["adj"] and abs(mO["prem_pct"]) < 0.5 and mO["prem_raw"] > 5, mO
print("17 OK junya itihasatahi spot vagalun:", round(mO["prem_raw"], 1), "->", round(mO["prem_pct"], 2))
# 17) delta nasel tar andaje delta vaparle jate
stE2 = {}
oc_nd = {"25000.000000": {"ce": {"last_price": 100.0, "oi": 10}, "pe": {"last_price": 100.0, "oi": 10}},
         "25200.000000": {"ce": {"last_price": 20.0, "oi": 10}, "pe": {"last_price": 200.0, "oi": 10}}}
L.flow_ingest(stE2, "NIFTY", "e", oc_nd, 25000.0, L.Cfg(), 50, 100.0)
hc, hp, hfar = (stE2[L.flow_key("NIFTY", "e", 25000, "ce")][0], stE2[L.flow_key("NIFTY", "e", 25000, "pe")][0],
                stE2[L.flow_key("NIFTY", "e", 25200, "ce")][0])
assert abs(hc[6] - 0.5) < 0.01 and abs(hp[6] + 0.5) < 0.01 and 0 < hfar[6] < 0.2, (hc, hp, hfar)
print("17 OK delta fallback:", round(hc[6], 2), round(hp[6], 2), round(hfar[6], 2))
# 18) V40 signal -> plan (OTM, kami delta chalto); strike chain madhe nasel tar nakar
oc18 = mk_oc(25000.0)
for k_ in oc18: 
    oc18[k_]["ce"]["greeks"] = {"delta": 0.2}
v1 = L.v40_plan({"type": "CE", "strike": 25100}, oc18, 40.0, L.Cfg(), 65, 1500000, 1.0, 50)
assert v1["ok"] and v1["opt"]["strike"] == 25100 and v1["plan"]["lots"] >= 1 and v1["plan"]["sl"] < v1["plan"]["entry"] < v1["plan"]["t1"], v1
v2 = L.v40_plan({"type": "CE", "strike": 99999}, oc18, 40.0, L.Cfg(), 65, 1500000, 1.0, 50)
assert not v2["ok"] and "chain" in v2["note"], v2
print("18 OK V40 plan:", v1["opt"]["strike"], v1["plan"]["entry"], v1["plan"]["sl"], v1["plan"]["t1"], "lots", v1["plan"]["lots"])
# 19) tumcha CSV: PE OI +9.3%, kaccha premium +7.8% pan spot vagalun -1.7% -> aspasht, score 0
sc19 = L.flow_score("PE", 73800, 73800, 9.3, -1.7, 0.7, 0, prem_raw=7.8, adj=True)
assert sc19["score"] == 0 and "aspasht" in sc19["reasons"][0], sc19
sc19b = L.flow_score("PE", 73800, 73800, 9.3, -7.0, None, 0, prem_raw=-2.0, adj=True)   # donhi nakaratmak, spot vagalun motha -> chalu
assert sc19b["direction"] == "UP" and sc19b["score"] > 0, sc19b
sc19c = L.flow_score("PE", 73800, 73800, 9.3, -0.8, None, 0, prem_raw=-0.9, adj=True)   # adj premium 1% khali -> noise
assert sc19c["score"] == 0, sc19c
print("19 OK kaccha/spot-vagalun virodhabhas -> score 0:", sc19["reasons"][0][:60])
# 20) kadakpana: sail mode madhe jast (kinva barobar) signal, sideways madhe tari nahi
from dataclasses import replace as _rp
strict, med = L.Cfg(), _rp(L.Cfg(), min_crosses=1, min_aligned=2, vol_min=1.0)
c20 = reversal_series()
n_s = n_m = 0
for tt in range(70, len(c20) + 1):
    d20 = make_candles(c20[:tt])
    n_s += L.evaluate_signal(d20, strict)["direction"] != "WAIT"
    n_m += L.evaluate_signal(d20, med)["direction"] != "WAIT"
assert n_m >= n_s and n_m >= 1, (n_s, n_m)
assert L.evaluate_signal(make_candles(flat), med)["direction"] == "WAIT"
chk = L.evaluate_signal(make_candles(c20[:124]), strict)["checks"]
assert any("एकाच बाजूला" in c[0] and "/3" in c[2] for c in chk), chk
print("20 OK kadakpana: kadak", n_s, "candle, madhyam", n_m, "candle; sideways = WAIT")
# 21) sahmati
assert L.consensus({"a": "UP", "b": "UP", "c": None, "d": None})[0] == "UP"
assert L.consensus({"a": "UP", "b": None, "c": None, "d": None})[0] == "WAIT"
assert L.consensus({"a": "UP", "b": "UP", "c": "DOWN", "d": None})[0] == "WAIT"        # 2 vs 1 = virodh
assert L.consensus({"a": "UP", "b": "UP", "c": "UP", "d": "DOWN"})[0] == "UP"         # 3 vs 1 chalel
assert L.consensus({"a": "DOWN", "b": "DOWN", "c": None, "d": None})[0] == "DOWN"
assert L.consensus({"a": "UP", "b": "UP", "c": None, "d": None}, 3)[0] == "WAIT"        # min_agree=3
assert L.consensus({"a": "UP", "b": "UP", "c": "UP", "d": None}, 3)[0] == "UP"
assert L.consensus({"a": "UP", "b": "UP", "c": "UP", "d": None}, 4)[0] == "WAIT"
assert L.consensus({"a": "UP", "b": "UP", "c": "UP", "d": "UP"}, 4)[0] == "UP"
assert L.chart_vote({"direction": "UP", "checks": []}) == "UP"
assert L.chart_vote({"direction": "WAIT", "leaning": "DOWN", "checks": [("x", True, ""), ("y", False, "")]}) == "DOWN"   # 1 ati baki
assert L.chart_vote({"direction": "WAIT", "leaning": "DOWN", "checks": [("x", False, ""), ("y", False, "")]}) is None   # 2 baki
print("21 OK sahmati / chart_vote")
# 22) MCX id shodh (detailed ani compact stambh-prakar)
import pandas as _pd
rows = [
 ["MCX", "M", "FUTCOM", 111, "CRUDEOIL", "CRUDEOIL", "2026-11-19", 100, 0, "", 0],
 ["MCX", "M", "FUTCOM", 112, "CRUDEOIL", "CRUDEOIL", "2026-12-21", 100, 0, "", 0],
 ["MCX", "M", "OPTFUT", 9001, "CRUDEOIL", "CRUDEOIL", "2026-11-12", 100, 5900, "CE", 111],
 ["MCX", "M", "OPTFUT", 9002, "CRUDEOIL", "CRUDEOIL", "2026-11-12", 100, 5950, "CE", 111],
 ["MCX", "M", "OPTFUT", 9003, "CRUDEOIL", "CRUDEOIL", "2026-11-12", 100, 6000, "PE", 111],
 ["MCX", "M", "OPTFUT", 9004, "CRUDEOIL", "CRUDEOIL", "2026-12-14", 100, 6000, "PE", 112],
 ["MCX", "M", "OPTFUT", 9100, "GOLD", "GOLD", "2026-11-25", 1, 120000, "CE", 222],
 ["NSE", "D", "OPTIDX", 5, "NIFTY", "NIFTY", "2026-11-04", 75, 25000, "CE", 13],
 ["MCX", "M", "OPTFUT", 9005, "CRUDEOIL", "CRUDEOIL", "2026-10-01", 100, 5900, "CE", 100],     # junya expiry
]
cols = ["EXCH_ID", "SEGMENT", "INSTRUMENT", "SECURITY_ID", "UNDERLYING_SYMBOL", "SYMBOL_NAME", "SM_EXPIRY_DATE", "LOT_SIZE", "STRIKE_PRICE", "OPTION_TYPE", "UNDERLYING_SECURITY_ID"]
dfm = _pd.DataFrame(rows, columns=cols)
r22 = L.find_commodity(dfm, "crudeoil", today="2026-10-29")
assert r22["ok"] and r22["id"] == 111 and r22["lot"] == 100 and r22["step"] == 50.0 and r22["expiry"] == "2026-11-12", r22
assert L.find_commodity(dfm, "GOLD", today="2026-10-29")["id"] == 222
assert not L.find_commodity(dfm, "SILVER", today="2026-10-29")["ok"]
comp = dfm.drop(columns=["UNDERLYING_SECURITY_ID", "UNDERLYING_SYMBOL"]).rename(columns={
    "EXCH_ID": "SEM_EXM_EXCH_ID", "INSTRUMENT": "SEM_INSTRUMENT_NAME", "SECURITY_ID": "SEM_SMST_SECURITY_ID",
    "SYMBOL_NAME": "SM_SYMBOL_NAME", "SM_EXPIRY_DATE": "SEM_EXPIRY_DATE", "LOT_SIZE": "SEM_LOT_UNITS", "STRIKE_PRICE": "SEM_STRIKE_PRICE"})
r22b = L.find_commodity(comp, "CRUDEOIL", today="2026-10-29")
assert r22b["ok"] and r22b["id"] == 111 and "FUTURE" in r22b["how"], r22b          # compact: nearest FUTURE (option expiry nantar)
assert not L.find_commodity(_pd.DataFrame({"a": [1]}), "X")["ok"]
print("22 OK MCX shodh:", r22["id"], r22["lot"], r22["step"], r22["expiry"], "| compact:", r22b["id"])

# 23) OI data sanshayaspad + spot ulat dishene -> sanket baajula
def _flow_case(oi_path, ltp_path, spot_path):
    stx = {}
    for j, (oi_, lt_) in enumerate(zip(oi_path, ltp_path)):
        L.flow_update(stx, L.flow_key("NIFTY", "e", 25000, "ce"), 9000 + j * 30, lt_, oi_, 10 * j * 30)
    stx["__spot__NIFTY"] = [(9000 + j * 30, sp_) for j, sp_ in enumerate(spot_path)]
    ocx = {"25000.000000": {"ce": {"last_price": ltp_path[-1], "oi": oi_path[-1]}, "pe": {}}}
    cfg_ = L.Cfg(flow_min_score=10.0)
    return L.flow_rank(stx, "NIFTY", "e", ocx, spot_path[-1], cfg_, 50), stx
n_ = 22
oi_ok = [800000 * (1 + 0.012 * i) for i in range(n_)]          # OI hole-hole +25% (khara)
lt_up = [100 * (1 + 0.012 * i) for i in range(n_)]
flat_sp = [25000.0] * n_
r_, st_ = _flow_case(oi_ok, lt_up, flat_sp)
assert r_ and r_[0]["direction"] == "UP" and not st_["__flow_dropped__"], (r_, st_.get("__flow_dropped__"))
oi_jump = [127000] * (n_ - 8) + [1045680] * 8                   # ek snapshot madhe 8x udi (tumchya CSV sarkhe)
r2_, st2_ = _flow_case(oi_jump, lt_up, flat_sp)
assert not r2_ and any("संशयास्पद" in x for x in st2_["__flow_dropped__"]), (r2_, st2_["__flow_dropped__"])
fall_sp = [25000.0 - 4 * i for i in range(n_)]                  # spot ~ -0.34% khali, sanket UP
r3_, st3_ = _flow_case(oi_ok, lt_up, fall_sp)
assert not r3_ and any("उलट दिशेने" in x for x in st3_["__flow_dropped__"]), (r3_, st3_["__flow_dropped__"])
rise_sp = [25000.0 + 4 * i for i in range(n_)]                  # spot var, sanket UP -> thevaycha
r4_, _ = _flow_case(oi_ok, lt_up, rise_sp)
assert r4_ and r4_[0]["direction"] == "UP"
print("23 OK OI sanshayaspad ani spot-ulat sanket baajula")

# 24) Signal tracker
lg = []
assert L.siglog_add(lg, 1000, "NIFTY", "e", "OI FLOW", "UP", 25000, "ce", 25000.0, 80, False)
assert not L.siglog_add(lg, 1300, "NIFTY", "e", "OI FLOW", "UP", 25000, "ce", 25001.0, 80, False)     # 15 min madhe duhera nahi
assert L.siglog_add(lg, 1300, "NIFTY", "e", "OI FLOW", "DOWN", 25000, "pe", 25001.0, 80, False)
assert L.siglog_add(lg, 1300, "NIFTY", "e", "CAS EARLY", "UP", 25000, "pe", 25001.0, 60, True)
assert not L.siglog_add(lg, 1300, "NIFTY", "e", "CHART", "WAIT", None, None, 25000.0, None, False)
L.siglog_update(lg, 1400, "NIFTY", 25050.0)       # +0.2% (UP sathi HIT, DOWN sathi MISS)
res = {(r["engine"], r["dir"]): r["result"] for r in lg}
assert res[("OI FLOW", "UP")] == "HIT" and res[("OI FLOW", "DOWN")] == "MISS" and res[("CAS EARLY", "UP")] == "HIT", res
lg2 = []
L.siglog_add(lg2, 0, "NIFTY", "e", "CHART", "UP", None, None, 25000.0, None, False)
L.siglog_update(lg2, 600, "NIFTY", 25005.0); assert lg2[0]["status"] == "OPEN"
L.siglog_update(lg2, 3700, "NIFTY", 25005.0); assert lg2[0]["result"] == "NO MOVE"
lg3 = []
L.siglog_add(lg3, 0, "NIFTY", "e", "CHART", "UP", None, None, 25000.0, None, False)
L.siglog_update(lg3, 6000, "NIFTY", 25100.0); assert lg3[0]["result"] == "NO DATA"                   # app band hota
sm = L.siglog_summary(lg + lg2 + lg3)
assert all(r["engine"] != "x" for r in sm) and sum(r["sanket"] for r in sm) == 4, sm
oi_ = next(r for r in sm if r["engine"] == "OI FLOW")
assert oi_["HIT"] == 1 and oi_["MISS"] == 1 and oi_["HIT % (HIT+MISS madhe)"] == 50.0, oi_
print("24 OK signal tracker")

# 25) V34 snippet: tumcha kod jasa ahe tasa chalto ka + winner kadhne
import types, pathlib
class _SS(dict):
    __getattr__ = dict.get
    def __setattr__(self, k, v): self[k] = v
class _St:
    def __init__(self): self.session_state = _SS()
    def __getattr__(self, name):
        if name == "empty" or name == "container":
            class _C:
                def __enter__(s): return s
                def __exit__(s, *a): return False
            return lambda *a, **k: _C()
        return lambda *a, **k: None
def _v34_run(ce_path, pe_path, scans=6):
    stt = _St(); src = compile(pathlib.Path("v34_engine.py").read_text(encoding="utf-8"), "v34_engine.py", "exec")
    g = None
    for i in range(scans):
        oc_ = {}
        for k, strike in enumerate((80900, 81000, 81100)):
            cl, pl = ce_path[i] * (1 - 0.05 * (k - 1)), pe_path[i] * (1 + 0.05 * (k - 1))
            oc_[f"{strike}.000000"] = {"ce": {"last_price": cl, "oi": 1_000_000 * (1 + i * 0.8), "previous_oi": 1_000_000, "volume": 900000 + 800000 * i,
                                              "prev_closing_price": 100.0, "greeks": {"delta": 0.5 + i * 0.01}},
                                       "pe": {"last_price": pl, "oi": 400_000, "previous_oi": 400_000, "volume": 100000, "prev_closing_price": 100.0,
                                              "greeks": {"delta": -0.5}}}
        g = {"__name__": "v34_engine", "st": stt, "oc": oc_, "spot": 81000.0, "df_5": None, "main_sym": "SENSEX"}
        exec(src, g)
    return g
up_ce = [100 * (1.06 ** i) for i in range(6)]; flat_pe = [100.0] * 6
g_ = _v34_run(up_ce, flat_pe)
assert g_.get("hero_list"), "V34 hero_list rikami"
w_ = L.v34_winner(g_)
assert w_ is None or (w_["dir"] == "UP" and w_["side"] == "ce"), w_
assert L.v34_winner({"hero_list": [{"option": "SENSEX 81000 PE", "rank_score": 70, "type": "PUT"}], "is_equilibrium_active": False, "market_mode": "🟢 TRENDING"})["dir"] == "DOWN"
assert L.v34_winner({"hero_list": [{"option": "SENSEX 81000 PE", "rank_score": 64, "type": "PUT"}], "is_equilibrium_active": False, "market_mode": "🟢 TRENDING"}) is None
assert L.v34_winner({"hero_list": [{"option": "SENSEX 81000 CE", "rank_score": 90, "type": "CALL"}], "is_equilibrium_active": True, "market_mode": "x"}) is None
print("25 OK V34 snippet chalto; winner:", w_ and (w_["option"], round(w_["score"], 1)))

# 26) commodity: put-call parity varun khara underlying (tumcha NATURALGAS CSV: chain ATM 305, parity 293)
_rows = {265: (28.7, .05), 270: (23, .05), 275: (18.1, .2), 280: (13.6, .45), 285: (9.4, 1.25), 290: (5.85, 2.85), 295: (3.25, 5.25),
         300: (1.6, 8.65), 305: (.75, 12.75), 310: (.3, 17.3), 315: (.15, 22.2), 320: (.1, 26.95)}
_oc = {f"{k}.000000": {"ce": {"last_price": c}, "pe": {"last_price": p}} for k, (c, p) in _rows.items()}
imp = L.implied_spot(_oc)
assert imp is not None and abs(imp - 293.0) < 0.3, imp
assert L.implied_spot({"100.000000": {"ce": {"last_price": 5}, "pe": {"last_price": 5}}}) is None
print("26 OK implied spot:", imp)

# 27) chukleli chaal nondni
hist = [(i * 30.0, 300.0 + i * 0.1) for i in range(60)]              # 30 min madhe +~2%
mm = L.missed_move(hist, [], 1770.0, "NATURALGAS", hist[-1][1], 1.2)
assert mm and mm["dir"] == "UP" and mm["mv"] > 1.5, mm
cov = [{"sym": "NATURALGAS", "dir": "UP", "t": 1000.0}]               # aadhi UP sanket hota -> chukleli nahi
assert L.missed_move(hist, cov, 1770.0, "NATURALGAS", hist[-1][1], 1.2) is None
cov2 = [{"sym": "NATURALGAS", "dir": "DOWN", "t": 1000.0}]            # ulat sanket = chukleli chaal
assert L.missed_move(hist, cov2, 1770.0, "NATURALGAS", hist[-1][1], 1.2)
assert L.missed_move(hist, [], 1770.0, "NATURALGAS", 300.5, 1.2) is None   # chhoti chaal
assert L.missed_move(hist[:3], [], 90.0, "NATURALGAS", 301.0, 1.2) is None  # data kami
print("27 OK chukleli chaal")

# 28) trend continuation / pullback (synthetic: majbut trend + pullback + punha pudhe; sideways = mat nahi)
import numpy as np
def _mk(closes, rng):
    c = np.array(closes, float); o = np.r_[c[0], c[:-1]]
    h = np.maximum(c, o) + abs(rng.normal(0.5, 0.2, len(c))); l = np.minimum(c, o) - abs(rng.normal(0.5, 0.2, len(c)))
    return pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": 1000})
ups = dns = flats = 0
for seed in range(40):
    rng = np.random.default_rng(seed)
    u = list(100 + np.arange(100) * 0.35 + rng.normal(0, 0.9, 100)); b = u[-1]; u += [b - 0.4, b - 1.2, b - 1.9, b - 1.5, b - 0.3, b + 1.2]
    ups += L.continuation_signal(_mk(u, rng), L.Cfg())["direction"] == "UP"
    d_ = list(200 - np.arange(100) * 0.35 + rng.normal(0, 0.9, 100)); b = d_[-1]; d_ += [b + 0.4, b + 1.2, b + 1.9, b + 1.5, b + 0.3, b - 1.2]
    dns += L.continuation_signal(_mk(d_, rng), L.Cfg())["direction"] == "DOWN"
    fl_ = list(100 + rng.normal(0, 0.6, 110))
    flats += L.continuation_signal(_mk(fl_, rng), L.Cfg())["direction"] is not None
assert ups >= 3 and dns >= 3, (ups, dns)           # kahi trend+pullback madhe mat yeta
assert flats <= 3, flats                          # sideways madhe kvachit (<~7%) chukiche mat
assert L.continuation_signal(None, L.Cfg())["direction"] is None
print(f"28 OK trend continuation (synthetic: UP {ups}/40, DOWN {dns}/40, sideways false {flats}/40)")

# 29) Monthly HOLD: indicators, monthly expiry, ₹10 strike, plan
exps = ["2026-11-23", "2026-11-25", "2026-11-30", "2026-12-01", "2026-12-08", "2026-12-29", "2027-01-26"]
assert L.monthly_expiries(exps) == ["2026-11-30", "2026-12-29", "2027-01-26"], L.monthly_expiries(exps)
_rng = np.random.default_rng(5)
_s = pd.Series(100 + np.arange(120) * 0.3 + _rng.normal(0, 0.5, 120))
assert _s.shape == L.hma(_s, 21).shape and L.hma(_s, 21).iloc[-1] > L.hma(_s, 21).iloc[-20]       # HMA vadhto
assert abs(L.alma(_s, 9).iloc[-1] - _s.iloc[-1]) < 3                                              # ALMA kimmat javal
_ml, _ms = L.macd_lines(_s); assert _ml.iloc[-1] > 0
assert L.monthly_hold_signal(None)["direction"] == "WAIT" and L.monthly_hold_signal(None)["note"]
ok_up = ok_dn = fl = 0
for seed in range(40):
    rng = np.random.default_rng(seed)
    base = list(100 + rng.normal(0, 0.6, 90))                                                   # sideways, mag zep
    up = base + list(base[-1] + np.arange(1, 6) * 0.9 + rng.normal(0, 0.25, 5))
    dn = base + list(base[-1] - np.arange(1, 6) * 0.9 + rng.normal(0, 0.25, 5))
    ok_up += L.monthly_hold_signal(_mk(up, rng), 5, 7, 1)["direction"] == "UP"
    ok_dn += L.monthly_hold_signal(_mk(dn, rng), 5, 7, 1)["direction"] == "DOWN"
    fl += L.monthly_hold_signal(_mk(list(100 + rng.normal(0, 0.6, 110)), rng), 5, 8, 2)["direction"] != "WAIT"
assert ok_up >= 10 and ok_dn >= 10, (ok_up, ok_dn)
assert fl <= 4, fl                                                                               # sideways + sakht niyam = kvachit
oc29 = {"23000.000000": {"ce": {"last_price": 10.4, "top_bid_price": 10.0, "top_ask_price": 10.8, "oi": 8000, "greeks": {"delta": 0.05}},
                         "pe": {"last_price": 300.0, "top_bid_price": 299, "top_ask_price": 301, "oi": 9000, "greeks": {"delta": -0.6}}},
        "22000.000000": {"ce": {"last_price": 1100.0, "top_bid_price": 1099, "top_ask_price": 1101, "oi": 9000, "greeks": {"delta": 0.9}},
                         "pe": {"last_price": 9.9, "top_bid_price": 9.5, "top_ask_price": 10.3, "oi": 7000, "greeks": {"delta": -0.04}}}}
o29 = L.pick_by_premium(oc29, "UP", 10.0, 22500.0)
assert o29["strike"] == 23000 and o29["side"] == "ce" and o29["ok"] and o29["warn"], o29      # delta kami = lotery chetavni
o29p = L.pick_by_premium(oc29, "DOWN", 10.0, 22500.0)
assert o29p["strike"] == 22000 and o29p["side"] == "pe"
o29b = L.pick_by_premium(oc29, "UP", 10.0, 22500.0, min_oi=50000)
assert not o29b["ok"] and o29b["issues"]
pl29 = L.month_plan(o29, 65, 100000, 1.0, 50, 100, 200)
assert pl29["ok"] and pl29["sl"] < pl29["entry"] < pl29["t1"] < pl29["t2"] and pl29["max_loss"] <= 1000 + 1e-6, pl29
pl29s = L.month_plan(o29, 65, 10000, 1.0, 50, 100, 200)                                          # 10k bhandval, 1% jokhim = 100 -> 1 lot (~Rs 340 tota) basat nahi
assert not pl29s["ok"] and pl29s["note"]
print(f"29 OK monthly HOLD (UP {ok_up}/40, DOWN {ok_dn}/40, sideways khote {fl}/40)")

# 29b) khidki: ek divas (75 candle) — kahi indicators aadhi, kahi nantar cross zale tari chalel; khup junya trend var nahi
_c = L.monthly_hold_signal
res_ = {}
for n_ in (12, 30, 60, 160):
    hit = 0
    for seed in range(30):
        rng = np.random.default_rng(seed)
        base = list(100 + rng.normal(0, 0.6, 90))
        up = base + list(base[-1] + np.arange(1, n_ + 1) * 0.5 + rng.normal(0, 0.25, n_))
        hit += _c(_mk(up, rng), 75, 8, 8, 20.0)["direction"] == "UP"
    res_[n_] = hit
assert res_[12] >= 5 and res_[30] >= 5, res_            # divsabhar chalel
assert res_[160] <= res_[30], res_                      # junya (2 divsa purvichya) trend var kami
print("29b OK ek divas khidki:", res_)

# 29c) delta var strike
oc29d = {"22000.000000": {"ce": {"last_price": 560.0, "top_bid_price": 559, "top_ask_price": 561, "oi": 9000, "greeks": {"delta": 0.85}}},
         "22400.000000": {"ce": {"last_price": 230.0, "top_bid_price": 229, "top_ask_price": 231, "oi": 9000, "greeks": {"delta": 0.55}}},
         "22500.000000": {"ce": {"last_price": 170.0, "top_bid_price": 169, "top_ask_price": 171, "oi": 9000, "greeks": {"delta": 0.45}}},
         "22700.000000": {"ce": {"last_price": 90.0, "top_bid_price": 89, "top_ask_price": 91, "oi": 9000, "greeks": {"delta": 0.31}}},
         "23500.000000": {"ce": {"last_price": 9.0, "top_bid_price": 8.5, "top_ask_price": 9.5, "oi": 9000, "greeks": {"delta": 0.04}}},
         "22300.000000": {"pe": {"last_price": 80.0, "top_bid_price": 79, "top_ask_price": 81, "oi": 9000, "greeks": {"delta": -0.4}}}}
od = L.pick_by_delta(oc29d, "UP", 0.30, 0.50, 22450.0, 100.0)
assert od["strike"] == 22500 and od["ok"] and not od["warn"], od                    # 0.45 = madhyabindu (0.40) javal
od2 = L.pick_by_delta(oc29d, "UP", 0.60, 0.70, 22450.0, 100.0)                      # pattyat konihi nahi -> javalcha + chetavni
assert od2["strike"] in (22400, 22500) and od2["warn"], od2
oc_ng = {k: {s_: {kk: vv for kk, vv in v_.items() if kk != "greeks"} for s_, v_ in n_.items()} for k, n_ in oc29d.items()}
od3 = L.pick_by_delta(oc_ng, "UP", 0.30, 0.50, 22450.0, 100.0)                       # delta nasel tar andaje, chetavni
assert od3["ok"] and any("अंदाजे" in w for w in od3["warn"]), od3
assert L.pick_by_delta({}, "UP", 0.3, 0.5, 100.0)["ok"] is False
print("29c OK delta var strike:", od["strike"], od["delta"], od["ltp"])

# 29d) move nighun gela + pullback
def _run(n_up, n_pull, retr, pb_al, step=0.5):
    rng = np.random.default_rng(11)
    base = list(100 + rng.normal(0, 0.6, 90))
    up = base + list(base[-1] + np.arange(1, n_up + 1) * 0.7 + rng.normal(0, 0.2, n_up))
    pk = up[-1]
    up += list(pk - np.arange(1, n_pull + 1) * step + rng.normal(0, 0.2, n_pull))
    return L.monthly_pullback(_mk(up, rng), 75, 7, 20.0, retr, pb_al)
pa = _run(30, 0, 0.15, 2)                    # peak var, pullback nahi
pb_ = _run(30, 8, 0.15, 2)                   # 15% pullback pattala, 2+ indicators ajun UP
pc = _run(30, 8, 0.15, 5)                    # pan fakt 2 ajun UP -> 5 mhanje 'ready' nahi
pd_ = _run(30, 4, 0.5, 2)                    # 50% pullback ajun ala nahi
assert pa and pa["direction"] == "UP" and pa["move_atr"] > 1.5 and not pa["ready"], pa
assert pb_ and pb_["ready"] and pb_["level"] < pb_["peak"], pb_
assert pc and not pc["ready"], pc
assert pd_ and not pd_["reached"] and not pd_["ready"], pd_
assert L.monthly_pullback(None) is None
print("29d OK move nighun gela + pullback:", (round(pb_["move_atr"], 1), round(pb_["level"], 1), pb_["ready"]))

# 30) stock_scanner (1 day candle) - khota Dhan (fake raw data)
import stock_scanner as SS
from datetime import datetime as _dt
def _daily(closes, rng):
    d_ = _mk(closes, rng)
    d_["timestamp"] = pd.date_range(end="2026-11-20", periods=len(d_), freq="B")
    return d_
_now = _dt(2026, 11, 23, 18, 0)
rng = np.random.default_rng(3)
_b = list(100 + rng.normal(0, 0.6, 120))
r_flat = SS.scan_one(_daily(_b, rng), _now, 10, 7, 5, 20.0, 1.5, 0.4, 3)
_up = _b + list(_b[-1] + np.arange(1, 7) * 0.9 + rng.normal(0, 0.25, 6))
r_up = SS.scan_one(_daily(_up, rng), _now, 10, 7, 5, 10.0, 1.5, 0.4, 3)      # ADX 10: sakht nasel tar FRESH yete
r_up_adx = SS.scan_one(_daily(_up, rng), _now, 10, 7, 5, 40.0, 1.5, 0.4, 3)  # ADX 40 magitla: WAIT
_long = _b + list(_b[-1] + np.arange(1, 40) * 0.9 + rng.normal(0, 0.25, 39))
r_late = SS.scan_one(_daily(_long, rng), _now, 10, 7, 5, 20.0, 1.5, 0.4, 3)
r_short = SS.scan_one(_daily(_b[:40], rng), _now)
assert r_flat["status"] in ("WAIT", "FRESH", "LATE") and r_short["status"] == "डेटा अपुरा", (r_flat, r_short)
assert r_up["status"] in ("FRESH", "LATE") and r_up["direction"] == "UP", r_up
assert r_up_adx["status"] == "WAIT", r_up_adx
assert r_late["status"] in ("LATE", "PULLBACK READY", "WAIT"), r_late
rows_ = [dict(r_up, symbol="AAA"), dict(r_flat, symbol="BBB", status="WAIT"), dict(r_late, symbol="CCC", status="LATE")]
fr = SS.format_rows(rows_, False)
assert all(x["status"] != "WAIT" for x in fr) and len(SS.format_rows(rows_, True)) == 3
print("30 OK stock scanner:", r_flat["status"], r_up["status"], r_late["status"], r_short["status"])

print("\nSAGLYA CHACHNYA PASS")
