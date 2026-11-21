"""Offline XAUT-USDT 1-minute CSV backtest, NO orders or API calls.

CSV must contain time (Unix ms), open, high, low, close.
Run: python backtest_gold_profit_auto.py candles.csv
A bar-close signal is executed at the NEXT bar's open (no lookahead).
Intrabar stop/target are conservatively assessed using the OHLC range.
This is an approximation: spread, slippage and gaps may worsen live fills.
"""
import argparse
import csv
from datetime import datetime, timezone
from gold_profit_auto import (
    START_BALANCE, BASE_NOTIONAL, MAX_NOTIONAL, GROWTH_STEP,
    TAKE_PROFIT_NET, STOP_LOSS_NET, DAILY_LOSS_LIMIT, FEE_RATE,
)
import pandas as pd

def direction(closes):
    s = pd.Series(closes, dtype=float)
    if len(s) < 30:
        return None
    e9 = s.ewm(span=9, adjust=False).mean().iloc[-1]
    e21 = s.ewm(span=21, adjust=False).mean().iloc[-1]
    d = s.diff()
    gain = d.clip(lower=0).ewm(alpha=1/14, adjust=False).mean().iloc[-1]
    loss = (-d.clip(upper=0)).ewm(alpha=1/14, adjust=False).mean().iloc[-1]
    if loss <= 0:
        return None
    rsi = 100 - 100 / (1 + gain / loss)
    if e9 > e21 and 50 < rsi < 70:
        return "LONG"
    if e9 < e21 and 30 < rsi < 50:
        return "SHORT"
    return None

def backtest(bars):
    balance = START_BALANCE
    position = None
    trades = []
    pending = None
    last_exit_time = -10**15
    loss_by_day = {}
    peak = balance
    max_drawdown = 0.0
    for i, bar in enumerate(bars):
        ts = int(bar["time"])
        day = datetime.fromtimestamp(ts / 1000, timezone.utc).date().isoformat()
        o, h, l, c = (float(bar[k]) for k in ("open", "high", "low", "close"))
        if min(o, h, l, c) <= 0 or l > min(o, c) or h < max(o, c):
            raise ValueError(f"Invalid OHLC bar at index {i}")
        # Pending signal from the PREVIOUS closed candle.
        if (position is None and pending and ts - last_exit_time >= 30000
                and loss_by_day.get(day, 0) < DAILY_LOSS_LIMIT):
            steps = max(0, int((balance - START_BALANCE) // GROWTH_STEP))
            notional = min(MAX_NOTIONAL, BASE_NOTIONAL * (1 + 0.1 * steps))
            position = {"side": pending, "entry": o, "qty": notional / o}
        pending = None
        if position is not None:
            sign = 1 if position["side"] == "LONG" else -1
            qty = position["qty"]
            entry = position["entry"]
            # Approximate net PnL after both entry and exit taker fees.
            def pnl(p):
                return (p - entry) * qty * sign - (entry + p) * qty * FEE_RATE
            # For both TP and SL hit in one candle, prioritize SL.
            stop_hit = pnl(l if sign == 1 else h) <= STOP_LOSS_NET
            target_hit = pnl(h if sign == 1 else l) >= TAKE_PROFIT_NET
            if stop_hit or target_hit:
                threshold = STOP_LOSS_NET if stop_hit else TAKE_PROFIT_NET
                # Solve net PnL = threshold for price.
                exit_price = (threshold + entry * qty * (sign + FEE_RATE)) / (qty * (sign - FEE_RATE))
                # Gap at open is filled at open, not the more favorable threshold.
                if stop_hit and pnl(o) < threshold:
                    exit_price = o
                net = pnl(exit_price)
                balance += net
                trades.append({"time": ts, "side": position["side"],
                               "entry": entry, "exit": exit_price,
                               "net": round(net, 4),
                               "reason": "SL" if stop_hit else "TP"})
                if net < 0:
                    loss_by_day[day] = loss_by_day.get(day, 0) - net
                position = None
                last_exit_time = ts
                peak = max(peak, balance)
                max_drawdown = max(max_drawdown, peak - balance)
        # Only closes through the current completed bar inform next bar.
        if position is None:
            pending = direction([float(x["close"]) for x in bars[max(0, i-99):i+1]])
    unrealized = 0.0
    if position is not None:
        final_close = float(bars[-1]["close"])
        sign = 1 if position["side"] == "LONG" else -1
        qty = position["qty"]
        entry = position["entry"]
        unrealized = (final_close - entry) * qty * sign - (entry + final_close) * qty * FEE_RATE
    winners = sum(t["net"] > 0 for t in trades)
    return {"trades": len(trades), "wins": winners,
            "win_rate_pct": round(100 * winners / len(trades), 2) if trades else 0,
            "net_usdt": round(balance - START_BALANCE, 2),
            "ending_balance_usdt": round(balance, 2),
            "max_closed_trade_drawdown_usdt": round(max_drawdown, 2),
            "open_position_at_end": position is not None,
            "unrealized_pnl_usdt": round(unrealized, 2),
            "equity_including_open_usdt": round(balance + unrealized, 2)}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", help="Historical 1m OHLC CSV")
    args = parser.parse_args()
    with open(args.csv_path, newline="") as f:
        bars = list(csv.DictReader(f))
    if len(bars) < 31:
        raise SystemExit("At least 31 candles required")
    times = [int(b["time"]) for b in bars]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise SystemExit("Candles must be strictly chronological")
    result = backtest(bars)
    print(result)
    if result["trades"] < 100:
        print("NOT ENOUGH TRADES: do not infer strategy quality from this sample")

if __name__ == "__main__":
    main()
