import time
import os
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path not in ("/", "/health"):
            self.send_error(404)
            return
        body = b"Gold scalper PAPER DEMO service online\n"
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

def start_health_server():
    port = int(os.environ.get("PORT", "10000"))
    server = ThreadingHTTPServer(("0.0.0.0", port), HealthHandler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    print("HTTP health server listening on port", port, flush=True)

import fcntl
import sys
import requests
import pandas as pd
from datetime import datetime, timezone
from paper_account import load_state, save_state
from demo_config import (
    SYMBOL, MARGIN, LEVERAGE,
    MAX_RISK_USDT, MAX_DAILY_LOSS,
    TAKER_FEE
)

BASE = "https://open-api.bingx.com"
POSITION_VALUE = MARGIN * LEVERAGE
POLL_SECONDS = 10
session = requests.Session()

def api(path, params):
    r = session.get(BASE + path, params=params, timeout=15)
    r.raise_for_status()
    data = r.json()
    if data.get("code") != 0:
        raise RuntimeError(str(data))
    return data["data"]

def get_price():
    data = api(
        "/openApi/swap/v2/quote/price",
        {"symbol": SYMBOL}
    )
    return float(data["price"])

def get_signal():
    data = api(
        "/openApi/swap/v3/quote/klines",
        {"symbol": SYMBOL, "interval": "1m", "limit": 100}
    )
    df = pd.DataFrame(data)
    for col in ("time", "close"):
        df[col] = pd.to_numeric(df[col])
    df = df.sort_values("time")

    minute = int(time.time() // 60) * 60000
    df = df[df["time"] < minute].copy()

    if len(df) < 30:
        return "GAIDIT"

    close = df["close"]
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()

    delta = close.diff()
    gain = delta.clip(lower=0).ewm(
        alpha=1/14, adjust=False
    ).mean()
    loss = (-delta.clip(upper=0)).ewm(
        alpha=1/14, adjust=False
    ).mean()

    if loss.iloc[-1] == 0:
        return "GAIDIT"

    rsi = 100 - 100 / (1 + gain.iloc[-1] / loss.iloc[-1])

    if ema9.iloc[-1] > ema21.iloc[-1] and 50 < rsi < 70:
        return "LONG"
    if ema9.iloc[-1] < ema21.iloc[-1] and 30 < rsi < 50:
        return "SHORT"
    return "GAIDIT"

def open_position(state, side, price):
    if state.get("mode") != "PAPER_DEMO":
        print("Nepareizs konta rezims!")
        return

    if state.get("position") is not None:
        print("Pozicija jau atverta!")
        return

    if state["balance"] < MARGIN + MAX_RISK_USDT:
        print("Nepietiek virtualas bilances!")
        return

    if daily_loss(state):
        print("Dienas zaudejumu limits!")
        return

    if side not in ("LONG", "SHORT") or price <= 0:
        print("Nederigs darijums!")
        return

    fees = POSITION_VALUE * TAKER_FEE * 2
    distance = (MAX_RISK_USDT - fees) / POSITION_VALUE

    if distance <= 0:
        return

    target = (MAX_RISK_USDT * 1.5 + fees) / POSITION_VALUE

    if side == "LONG":
        sl = price * (1 - distance)
        tp = price * (1 + target)
    else:
        sl = price * (1 + distance)
        tp = price * (1 - target)

    state["position"] = {
        "side": side,
        "entry": price,
        "qty": POSITION_VALUE / price,
        "sl": sl,
        "tp": tp,
        "opened": time.time()
    }
    save_state(state)
    print("ATVERTS", side, "Cena:", price,
          "SL:", round(sl, 2), "TP:", round(tp, 2))

def close_position(state, price, reason):
    pos = state["position"]
    direction = 1 if pos["side"] == "LONG" else -1
    gross = (price - pos["entry"]) * pos["qty"] * direction
    fees = (pos["entry"] + price) * pos["qty"] * TAKER_FEE
    net = gross - fees

    state["balance"] += net
    state["trades"].append({
        "side": pos["side"],
        "entry": pos["entry"],
        "exit": price,
        "net": round(net, 4),
        "reason": reason,
        "date": datetime.now(timezone.utc).isoformat()
    })
    state["position"] = None
    save_state(state)

    print("AIZVERTS:", reason,
          "Rezultats:", round(net, 4),
          "Bilance:", round(state["balance"], 4))

def daily_loss(state):
    today = datetime.now(timezone.utc).date().isoformat()
    losses = sum(
        -float(t["net"])
        for t in state["trades"]
        if t.get("date", "").startswith(today)
        and float(t["net"]) < 0
    )
    return losses >= MAX_DAILY_LOSS

def main():
    print("GOLD SCALPER 1.0 | PAPER DEMO")
    print("Starts ar realam BingX cenam")
    state = load_state()
    last_signal_minute = state.get("last_signal_minute")

    while True:
        try:
            state = load_state()
            price = get_price()
            pos = state["position"]

            if pos:
                if pos["side"] == "LONG":
                    if price <= pos["sl"]:
                        close_position(state, price, "SL")
                    elif price >= pos["tp"]:
                        close_position(state, price, "TP")
                else:
                    if price >= pos["sl"]:
                        close_position(state, price, "SL")
                    elif price <= pos["tp"]:
                        close_position(state, price, "TP")
            elif not daily_loss(state):
                minute = int(time.time() // 60)
                if minute != last_signal_minute:
                    signal = get_signal()
                    last_signal_minute = minute
                    state["last_signal_minute"] = minute
                    save_state(state)
                    print("Cena:", price, "Signals:", signal)
                    if signal in ("LONG", "SHORT"):
                        open_position(state, signal, price)
            else:
                print("DIENAS ZAUDĒJUMU LIMITS")

        except Exception as e:
            print("KLUDA:", e)

        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    lock_file = open("gold_bot.lock", "w")
    try:
        fcntl.flock(
            lock_file.fileno(),
            fcntl.LOCK_EX | fcntl.LOCK_NB
        )
    except BlockingIOError:
        sys.exit("Bots jau darbojas!")

    start_health_server()
    try:
        main()
    except KeyboardInterrupt:
        print("\nBots apturets.")
