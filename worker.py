"""
worker.py: UI shivay chalnara Telegram alert robot (samadhan_terminal.py chach logic vaprto).
Order aapoaap jat nahi. Fakt Telegram var ENTER / T1 / SL / T2 sandesh.

Env variables (Secrets):
  DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN, TELEGRAM_TOKEN, TELEGRAM_CHAT_ID   (garjechhe)
  INDEX=NIFTY|BANKNIFTY|SENSEX (default NIFTY)  CAPITAL=100000  RISK_PCT=1  LOT_SIZE=<broker war tapasa>
  REFRESH=30 (sec)  EXPIRY=YYYY-MM-DD (nasel tar javalcha apoaap)  STATE_FILE=worker_state.json

Chalva:  python worker.py            (satat loop, VPS / swatachya PC var)
         python worker.py --once     (ekdach tapasa: cron / GitHub Actions; ya mode madhe OI-flow nasto,
                                      karan tyala satat chalnari memory lagte)
"""
import json
import os
import sys
import time
from datetime import datetime, timedelta, timezone, time as dtime
from pathlib import Path

import requests

import samadhan_terminal as T

IST = timezone(timedelta(hours=5, minutes=30))
STATE = Path(os.environ.get("STATE_FILE", "worker_state.json"))


def tg(text):
    tok, chat = os.environ.get("TELEGRAM_TOKEN"), os.environ.get("TELEGRAM_CHAT_ID")
    if not tok or not chat:
        print("[telegram nahi]", text)
        return False
    try:
        r = requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": chat, "text": text}, timeout=8)
        return r.status_code == 200
    except Exception as e:
        print("telegram error:", e)
        return False


def load_state():
    try:
        return json.loads(STATE.read_text())
    except Exception:
        return {"journal": [], "day": ""}


def save_state(s):
    try:
        STATE.write_text(json.dumps(s))
    except Exception as e:
        print("state save error:", e)


def fetch_chain(d_api, sec_id, expiry):
    resp = d_api.option_chain(under_security_id=sec_id, under_exchange_segment="IDX_I", expiry=expiry)
    data = (resp or {}).get("data", {}).get("data") if isinstance(resp, dict) else None
    if not data or "oc" not in data:
        raise RuntimeError(f"option chain milali nahi: {str(resp)[:200]}")
    return {"spot": float(data.get("last_price", 0)), "oc": data["oc"]}


def step(state, flow, flow_top, tsl, d_api, sym, cfg, capital, risk_pct, lot_size, expiry_env, use_flow, now=None, now_ts=None):
    """Ek fer. state = journal dict (disk war). flow/flow_top = RAM madhe OI-flow memory."""
    now = now or datetime.now(IST).replace(tzinfo=None)
    now_ts = now_ts or time.time()
    meta = T.INDEX_MAP[sym]
    today = now.strftime("%Y-%m-%d")
    if state.get("day") != today:
        state["day"] = today
        flow.clear(); flow_top.clear()
    expiry = expiry_env
    if not expiry:
        dates = [e for e in T.extract_dates(d_api.expiry_list(under_security_id=meta["id"], under_exchange_segment="IDX_I")) if e >= today]
        if not dates:
            raise RuntimeError("expiry yadi milali nahi; EXPIRY env set kara")
        expiry = dates[0]
    chain = fetch_chain(d_api, meta["id"], expiry)
    spot, oc = chain["spot"], chain["oc"]
    J = state["journal"]

    # chalu trades che status (ani Telegram)
    eod = now.time() >= dtime(15, 20)
    for t in J:
        if t["sym"] == sym and t["expiry"] == expiry and t["status"] in T.OPEN_STATES:
            before = t["status"]
            T.update_trade(t, T.chain_ltp(oc, t["strike"], t["side"]), now_ts, eod, cfg.max_trade_minutes)
            if t["status"] != before:
                tg(f"📌 {sym} {t['strike']:.0f} {t['side'].upper()}: {before} → {t['status']} | entry {t['entry']} | LTP {t['last']:.2f}"
                   + (" | SL aata entry var" if t["status"] == "T1 HIT" else ""))

    if not T.in_trade_window(now, cfg.window_start, cfg.window_end):
        return "vel baher"

    flow_dir = None
    if use_flow:
        T.flow_ingest(flow, sym, expiry, oc, spot, cfg, meta["step"], now_ts)
        sent_cas = state.setdefault("cas_sent", {})
        for c in T.cas_detect(flow, sym, expiry, oc, spot, cfg, meta["step"])[:3]:
            if c["stage"] == "LATE":
                continue
            k = f"{c['strike']:.0f}{c['side']}"
            prev = sent_cas.get(k, {"t": 0, "stage": ""})
            upgraded = c["stage"] == "STRONG" and prev["stage"] != "STRONG"
            if now_ts - prev["t"] > 1800 or upgraded:
                sp = T.cas_spread(c, oc, meta["step"], lot_size, capital, risk_pct, cfg)
                if sp["ok"]:
                    sent_cas[k] = {"t": now_ts, "stage": c["stage"]}
                    tg(f"🟡 {sym} CAS {c['stage']} {c['bias']} (score {c['score']:.0f}): {c['strike']:.0f} {c['side'].upper()}\n"
                       + " | ".join(c["reasons"]) + "\n"
                       f"{sp['name']}: SELL {sp['sell']:.0f} @ {sp['sell_px']} / BUY {sp['hedge']:.0f} @ {sp['hedge_px']}\n"
                       f"Credit ₹{sp['credit']} | rr {sp['rr']} | {sp['lots']} lot | kamal tota ₹{sp['max_loss']:.0f} | Breakeven {sp['breakeven']:.0f}\n"
                       "⚠ Margin tapasa. Move yeilach asa nahi. Suchana, salla nahi.")
        ranked = T.flow_rank(flow, sym, expiry, oc, spot, cfg, meta["step"])
        if ranked:
            key = (ranked[0]["strike"], ranked[0]["side"], ranked[0]["direction"])
            flow_top.append((now_ts, key))
            if T.flow_confirmed(flow_top, key, now_ts, cfg.flow_confirm_min):
                flow_dir = ranked[0]["direction"]
        flow_top[:] = [x for x in flow_top if now_ts - x[0] < 600]

    raw = tsl.get_historical_data(sym, "INDEX", "5")
    df, _ = T.drop_forming_candle(raw, 5, now)
    sig = T.evaluate_signal(df, cfg)
    if sig["close"] and spot and abs(spot / sig["close"] - 1) > 0.015:
        raise RuntimeError(f"spot ({spot:.0f}) ani chart ({sig['close']:.0f}) judat nahit; security id tapasa")
    d = sig["direction"]
    if d == "WAIT":
        return "WAIT"
    opt = T.pick_option(oc, spot, d, meta["step"], cfg)
    if not opt["ok"]:
        return "option nakarla: " + "; ".join(opt["issues"])
    plan = T.trade_plan(opt, sig["atr"], cfg, lot_size, capital, risk_pct)
    if not plan["ok"]:
        return "plan nahi: " + (plan["note"] or "")
    if not T.cooldown_ok(J, sym, d, now_ts, cfg.cooldown_min, sig["candle"]):
        return "cooldown"
    rel = T.flow_relation(flow_dir, d)
    J.append(T.new_trade(sym, d, expiry, opt, plan, now_ts, sig["candle"], rel))
    tg(T.fmt_alert(sym, d, expiry, opt, plan, sig, {"agree": "✅ julto", "oppose": "⚠ virodhat", "none": ""}[rel]))
    return "ENTER"


def main():
    once = "--once" in sys.argv
    cid, tok = os.environ.get("DHAN_CLIENT_ID"), os.environ.get("DHAN_ACCESS_TOKEN")
    if not cid or not tok:
        sys.exit("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN set kara")
    from dhanhq import DhanContext, dhanhq
    from Dhan_Tradehull import Tradehull
    tsl, d_api = Tradehull(cid, tok, mode="access_token"), dhanhq(DhanContext(cid, tok))
    sym = os.environ.get("INDEX", "NIFTY").upper()
    cfg = T.Cfg()
    capital = float(os.environ.get("CAPITAL", 100000))
    risk_pct = float(os.environ.get("RISK_PCT", 1))
    lot = int(os.environ.get("LOT_SIZE", T.INDEX_MAP[sym]["lot"]))
    refresh = int(os.environ.get("REFRESH", 30))
    expiry = os.environ.get("EXPIRY", "")
    state, flow, flow_top = load_state(), {}, []
    if not once:                                  # --once madhe pratyek veli sandesh nako
        tg(f"✅ Worker suru: {sym} | refresh {refresh}s | satat")
    last_err = ""
    while True:
        try:
            r = step(state, flow, flow_top, tsl, d_api, sym, cfg, capital, risk_pct, lot, expiry, use_flow=not once)
            print(datetime.now(IST).strftime("%H:%M:%S"), r)
            last_err = ""
        except Exception as e:
            msg = str(e)[:300]
            print("ERROR:", msg)
            if msg != last_err:                       # same chuk var var Telegram var nahi
                tg(f"⚠ Worker chuk: {msg}\n(token 24 tasanchach asto; expire zala asel tar navin Secrets takaa)")
                last_err = msg
        save_state(state)
        if once:
            break
        time.sleep(refresh)


if __name__ == "__main__":
    main()
