"""Gold Profit Auto: XAUT-USDT PAPER simulator. No MT5 connection, no live orders.
Run: python gold_profit_auto.py
Uses public BingX prices; results denominated in USDT, not EUR.
"""
import json
import time
from datetime import datetime, timezone
from pathlib import Path
import requests
import pandas as pd

SYMBOL = "XAUT-USDT"
BASE_URL = "https://open-api.bingx.com"
STATE = Path("gold_profit_auto_state.json")
START_BALANCE = 3000.0  # USDT, independent of MT5 demo balance
TAKE_PROFIT_NET = 2.0
STOP_LOSS_NET = -3.0
COOLDOWN_SECONDS = 30
DAILY_LOSS_LIMIT = 30.0
FEE_RATE = 0.0005
BASE_NOTIONAL = 200.0
MAX_NOTIONAL = 600.0
GROWTH_STEP = 300.0
POLL_SECONDS = 10

def initial_state():
    return {"mode": "PAPER_DEMO", "balance": START_BALANCE,
            "position": None, "trades": [], "last_exit": 0,
            "last_signal_minute": -1}

def load():
    if not STATE.exists():
        return initial_state()
    state = json.loads(STATE.read_text())
    if state.get("mode") != "PAPER_DEMO":
        raise ValueError("Only PAPER_DEMO is supported")
    return state

def save(state):
    tmp = STATE.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(STATE)

def api(path, params):
    response = requests.get(BASE_URL + path, params=params, timeout=15)
    response.raise_for_status()
    data = response.json()
    if data.get("code") != 0:
        raise RuntimeError(str(data))
    return data["data"]

def price():
    return float(api("/openApi/swap/v2/quote/price", {"symbol": SYMBOL})["price"])

def signal():
    candles = api("/openApi/swap/v3/quote/klines",
                  {"symbol": SYMBOL, "interval": "1m", "limit": 100})
    df = pd.DataFrame(candles)
    for col in ("time", "close"):
        df[col] = pd.to_numeric(df[col])
    df = df.sort_values("time")
    df = df[df["time"] < int(time.time() // 60) * 60000]
    if len(df) < 30:
        return "WAIT"
    close = df["close"]
    ema9 = close.ewm(span=9, adjust=False).mean()
    ema21 = close.ewm(span=21, adjust=False).mean()
    delta = close.diff()
    gains = delta.clip(lower=0).ewm(alpha=1/14, adjust=False).mean()
    losses = (-delta.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean()
    if losses.iloc[-1] <= 0:
        return "WAIT"
    rsi = 100 - 100 / (1 + gains.iloc[-1] / losses.iloc[-1])
    if ema9.iloc[-1] > ema21.iloc[-1] and 50 < rsi < 70:
        return "LONG"
    if ema9.iloc[-1] < ema21.iloc[-1] and 30 < rsi < 50:
        return "SHORT"
    return "WAIT"

def notional(balance):
    steps = max(0, int((balance - START_BALANCE) // GROWTH_STEP))
    return min(MAX_NOTIONAL, BASE_NOTIONAL * (1 + 0.1 * steps))

def net_pnl(position, mark):
    direction = 1 if position["side"] == "LONG" else -1
    gross = (mark - position["entry"]) * position["qty"] * direction
    fees = (position["entry"] + mark) * position["qty"] * FEE_RATE
    return gross - fees

def daily_loss(state):
    today = datetime.now(timezone.utc).date().isoformat()
    return sum(-t["net"] for t in state["trades"]
               if t["date"].startswith(today) and t["net"] < 0)

def step(state, mark, now, trade_signal="WAIT"):
    position = state["position"]
    if position:
        net = net_pnl(position, mark)
        if net >= TAKE_PROFIT_NET or net <= STOP_LOSS_NET:
            state["balance"] += net
            state["trades"].append({
                "date": datetime.now(timezone.utc).isoformat(),
                "side": position["side"], "entry": position["entry"],
                "exit": mark, "net": round(net, 4),
                "reason": "TP" if net >= TAKE_PROFIT_NET else "SL"})
            state["position"] = None
            state["last_exit"] = now
    elif (now - state["last_exit"] >= COOLDOWN_SECONDS
          and daily_loss(state) < DAILY_LOSS_LIMIT
          and trade_signal in ("LONG", "SHORT")):
        value = notional(state["balance"])
        # Fee-inclusive stops are evaluated at the next poll, not guaranteed fills.
        state["position"] = {"side": trade_signal, "entry": mark,
                             "qty": value / mark, "opened": now}
    return state

def main():
    print("GOLD PROFIT AUTO | XAUT-USDT | PAPER ONLY | USDT")
    state = load()
    while True:
        try:
            mark = price()
            now = time.time()
            trade_signal = "WAIT"
            minute = int(now // 60)
            if state["position"] is None and minute != state["last_signal_minute"]:
                trade_signal = signal()
                state["last_signal_minute"] = minute
            step(state, mark, now, trade_signal)
            save(state)
            print("Price", mark, "Signal", trade_signal,
                  "Balance", round(state["balance"], 2),
                  "Position", state["position"], "Trades", len(state["trades"]),
                  flush=True)
        except Exception as exc:
            print("ERROR:", exc, flush=True)
        time.sleep(POLL_SECONDS)

if __name__ == "__main__":
    main()
