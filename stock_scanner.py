"""
stock_scanner.py: F&O STOCKS cha 1-DAY (daily) candle var Monthly HOLD formula (KST, DMI, MOM, MACD, ALMA, HMA, VI, EMA9).
samadhan_terminal.py madhla ekach logic vaprto (monthly_hold_signal + monthly_pullback). Order aapoaap jat nahi.

Kay dakhavto (pratyek stock):  dishā (UP/DOWN), kiti indicators ekach bajula, ADX, move kiti ATR,
sthiti: FRESH (nava signal) / LATE (move nighun gela, pullback chi pattali dakhavto) / PULLBACK READY / WAIT.
Strike ithe nahi: stock madhe candidate sapadla ki option strike terminal madhe baghava (tya madhe stock cha security id lagto; ajun tapasle nahi).

Env (Secrets): DHAN_CLIENT_ID, DHAN_ACCESS_TOKEN   (Telegram sathi: TELEGRAM_TOKEN, TELEGRAM_CHAT_ID)

Chalva:
  python stock_scanner.py                          # default 45 stocks, 1 day candle, fakt kaam che (FRESH/LATE/PULLBACK) dakhav
  python stock_scanner.py --all                    # sarva stocks (WAIT sahit) dakhav
  python stock_scanner.py --file mystocks.txt      # tumchi list (pratyek oleet ek symbol)
  python stock_scanner.py --symbols RELIANCE,TCS   # thodya stocks
  python stock_scanner.py --window 10 --min-aligned 7 --min-fresh 5 --telegram

TAPASLE NAHI: Dhan_Tradehull cha 'DAY' timeframe naav ani stock symbols che naav (kahi naave badalleli astat) tumchya account var.
Jar 'timeframe' chi tarkar aali tar --tf badla (udaharan "D" kimva "1D"). Ek stock fail zala tari baki chalatat; shevti 'tutlele' yadi dakhavto.
Backtest nahi. Arthik salla nahi.
"""
import argparse
import os
import sys
import time
from datetime import datetime, timedelta, timezone

import pandas as pd
import requests

import samadhan_terminal as T

IST = timezone(timedelta(hours=5, minutes=30))

DEFAULT_STOCKS = [
    "RELIANCE", "TCS", "HDFCBANK", "INFY", "ICICIBANK", "SBIN", "AXISBANK", "KOTAKBANK", "LT", "ITC",
    "BHARTIARTL", "HINDUNILVR", "BAJFINANCE", "MARUTI", "SUNPHARMA", "TATASTEEL", "WIPRO", "HCLTECH",
    "ADANIENT", "ADANIPORTS", "ONGC", "NTPC", "POWERGRID", "COALINDIA", "JSWSTEEL", "ULTRACEMCO",
    "TITAN", "ASIANPAINT", "NESTLEIND", "M&M", "BAJAJFINSV", "TECHM", "INDUSINDBK", "HINDALCO",
    "GRASIM", "DRREDDY", "CIPLA", "EICHERMOT", "BPCL", "TATACONSUM", "APOLLOHOSP", "DIVISLAB",
    "HEROMOTOCO", "BRITANNIA", "SBILIFE",
]

PRIORITY = {"PULLBACK READY": 0, "FRESH": 1, "LATE": 2, "WAIT": 3}


def scan_one(raw, now, window=10, min_aligned=7, min_fresh=5, adx_min=20.0, late_atr=1.5, retrace=0.4, pb_aligned=3):
    """raw = candle DataFrame (daily). Ek stock cha nikal dict."""
    df, _ = T.drop_forming_candle(raw, 1440, now)          # chalu (apurna) divsachi candle vagale jate
    if df is None or len(df) < 70:
        return {"status": "डेटा अपुरा", "direction": "-", "aligned": 0, "adx": 0.0, "move_atr": 0.0,
                "level": None, "close": None, "note": f"candle {0 if df is None else len(df)} (किमान 70)"}
    sig = T.monthly_hold_signal(df, window, min_aligned, min_fresh, adx_min)
    pb = T.monthly_pullback(df, window, min_aligned, adx_min, retrace, pb_aligned)
    adx_now = float(T.dmi(df)[2].iloc[-1])
    close = float(df["close"].iloc[-1])
    late = bool(pb and pb["move_atr"] > late_atr)
    if late and pb["ready"]:
        status, direction = "PULLBACK READY", pb["direction"]
    elif late:
        status, direction = "LATE", pb["direction"]
    elif sig["direction"] in ("UP", "DOWN"):
        status, direction = "FRESH", sig["direction"]
    else:
        status, direction = "WAIT", sig.get("leaning", "-")
    return {"status": status, "direction": direction, "aligned": int(sig.get("aligned", 0)), "adx": adx_now,
            "move_atr": float(pb["move_atr"]) if pb else 0.0, "level": float(pb["level"]) if pb else None,
            "close": close, "note": ""}


def fetch_daily(tsl, sym, exch, tf):
    return tsl.get_historical_data(sym, exch, tf)


def send_telegram(text):
    tok, chat = os.environ.get("TELEGRAM_TOKEN", ""), os.environ.get("TELEGRAM_CHAT_ID", "")
    if not tok or not chat:
        return False
    try:
        return requests.post(f"https://api.telegram.org/bot{tok}/sendMessage", json={"chat_id": chat, "text": text}, timeout=8).status_code == 200
    except Exception:
        return False


def format_rows(rows, show_all):
    rows = [r for r in rows if show_all or r["status"] != "WAIT"]
    rows.sort(key=lambda r: (PRIORITY.get(r["status"], 9), -r["aligned"], -r["adx"]))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="")
    ap.add_argument("--file", default="")
    ap.add_argument("--exch", default="NSE")
    ap.add_argument("--tf", default="DAY")
    ap.add_argument("--window", type=int, default=10, help="cross chi khidki: gelya kiti divsat (default 10)")
    ap.add_argument("--min-aligned", type=int, default=7)
    ap.add_argument("--min-fresh", type=int, default=5)
    ap.add_argument("--adx", type=float, default=20.0)
    ap.add_argument("--late-atr", type=float, default=1.5)
    ap.add_argument("--retrace", type=float, default=0.4)
    ap.add_argument("--pb-aligned", type=int, default=3)
    ap.add_argument("--delay", type=float, default=1.0, help="don mangnyanmadhe sekand (Dhan limit sathi; limit mahit nahi)")
    ap.add_argument("--all", action="store_true")
    ap.add_argument("--telegram", action="store_true")
    ap.add_argument("--out", default="stock_scan.csv")
    a = ap.parse_args()

    cid, tok = os.environ.get("DHAN_CLIENT_ID", ""), os.environ.get("DHAN_ACCESS_TOKEN", "")
    if not cid or not tok:
        sys.exit("DHAN_CLIENT_ID / DHAN_ACCESS_TOKEN env madhe thevā (code madhe lihu naka).")
    from Dhan_Tradehull import Tradehull
    tsl = Tradehull(cid, tok, mode="access_token")

    if a.file:
        syms = [x.strip().upper() for x in open(a.file, encoding="utf-8") if x.strip() and not x.startswith("#")]
    elif a.symbols:
        syms = [x.strip().upper() for x in a.symbols.split(",") if x.strip()]
    else:
        syms = DEFAULT_STOCKS
    now = datetime.now(IST).replace(tzinfo=None)

    rows, failed = [], []
    for i, sym in enumerate(syms, 1):
        try:
            raw = fetch_daily(tsl, sym, a.exch, a.tf)
            r = scan_one(raw, now, a.window, a.min_aligned, a.min_fresh, a.adx, a.late_atr, a.retrace, a.pb_aligned)
            r["symbol"] = sym
            rows.append(r)
        except Exception as e:
            failed.append((sym, str(e)[:120]))
        print(f"[{i}/{len(syms)}] {sym}", end="\r", flush=True)
        time.sleep(a.delay)
    print()

    show = format_rows(rows, a.all)
    cols = ["symbol", "status", "direction", "aligned", "adx", "move_atr", "level", "close", "note"]
    df = pd.DataFrame(show, columns=cols) if show else pd.DataFrame(columns=cols)
    df.to_csv(a.out, index=False, encoding="utf-8-sig")
    print(df.round(2).to_string(index=False) if len(df) else "आज एकही stock अटीत बसत नाही.")
    if failed:
        print("\nतुटलेले:", "; ".join(f"{s}: {m}" for s, m in failed))
    print(f"\n{len(rows)} पैकी {len(show)} दाखवले · CSV: {a.out} · backtest नाही, आर्थिक सल्ला नाही.")

    if a.telegram and len(df):
        lines = [f"{r.symbol} {r.direction} · {r.status} · {r.aligned}/8 · ADX {r.adx:.0f}" + (f" · pullback {r.level:,.1f}" if r.status == "LATE" and r.level else "")
                 for r in df.head(15).itertuples()]
        send_telegram("📅 DAILY स्कॅन (Monthly HOLD)\n" + "\n".join(lines) + "\nफक्त माहिती; सल्ला नाही.")


if __name__ == "__main__":
    main()
