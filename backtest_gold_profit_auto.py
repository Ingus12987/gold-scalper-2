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

def direction(closes, min_ema_gap_pct=0.0):
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
    if abs(e9 - e21) / e21 * 100 < min_ema_gap_pct:
        return None
    if e9 > e21 and 50 < rsi < 70:
        return "LONG"
    if e9 < e21 and 30 < rsi < 50:
        return "SHORT"
    return None

def backtest(bars, take_profit=TAKE_PROFIT_NET, stop_loss=STOP_LOSS_NET, return_trades=False, min_ema_gap_pct=0.0, extra_cost_rate=0.0, allowed_side=None):
    if allowed_side not in (None, 'LONG', 'SHORT'):
        raise ValueError('allowed_side must be LONG, SHORT or None')
    if extra_cost_rate < 0 or extra_cost_rate >= 0.5:
        raise ValueError("extra_cost_rate must be between 0 and 0.5")
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
        if (position is None and pending and (allowed_side is None or pending == allowed_side) and ts - last_exit_time >= 30000
                and loss_by_day.get(day, 0) < DAILY_LOSS_LIMIT):
            steps = max(0, int((balance - START_BALANCE) // GROWTH_STEP))
            notional = min(MAX_NOTIONAL, BASE_NOTIONAL * (1 + 0.1 * steps))
            position = {"side": pending, "entry": o, "qty": notional / o}
        pending = None
        if position is not None:
            sign = 1 if position["side"] == "LONG" else -1
            qty = position["qty"]
            entry = position["entry"]
            # Extra cost models adverse spread/slippage on both sides.
            effective_fee = FEE_RATE + extra_cost_rate
            def pnl(p):
                return (p - entry) * qty * sign - (entry + p) * qty * effective_fee
            # For both TP and SL hit in one candle, prioritize SL.
            stop_hit = pnl(l if sign == 1 else h) <= stop_loss
            target_hit = pnl(h if sign == 1 else l) >= take_profit
            if stop_hit or target_hit:
                threshold = stop_loss if stop_hit else take_profit
                # Solve net PnL = threshold for price.
                exit_price = (threshold + entry * qty * (sign + effective_fee)) / (qty * (sign - effective_fee))
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
            pending = direction([float(x["close"]) for x in bars[max(0, i-99):i+1]], min_ema_gap_pct=min_ema_gap_pct)
    unrealized = 0.0
    if position is not None:
        final_close = float(bars[-1]["close"])
        sign = 1 if position["side"] == "LONG" else -1
        qty = position["qty"]
        entry = position["entry"]
        unrealized = (final_close - entry) * qty * sign - (entry + final_close) * qty * (FEE_RATE + extra_cost_rate)
    winners = sum(t["net"] > 0 for t in trades)
    summary = {"trades": len(trades), "wins": winners,
            "win_rate_pct": round(100 * winners / len(trades), 2) if trades else 0,
            "net_usdt": round(balance - START_BALANCE, 2),
            "ending_balance_usdt": round(balance, 2),
            "max_closed_trade_drawdown_usdt": round(max_drawdown, 2),
            "open_position_at_end": position is not None,
            "unrealized_pnl_usdt": round(unrealized, 2),
            "equity_including_open_usdt": round(balance + unrealized, 2)}
    return (summary, trades) if return_trades else summary

def candle_quality(bars):
    """Report missing/irregular 1m intervals before trusting a backtest."""
    times = [int(b["time"]) for b in bars]
    if any(b <= a for a, b in zip(times, times[1:])):
        raise ValueError("Candles must be strictly chronological")
    gaps = [(b - a) // 60000 - 1 for a, b in zip(times, times[1:]) if b - a > 60000]
    irregular = sum((b - a) % 60000 != 0 for a, b in zip(times, times[1:]))
    return {"candles": len(times), "gaps": len(gaps), "missing_minutes": sum(gaps), "irregular_intervals": irregular}

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("csv_path", help="Historical 1m OHLC CSV")
    parser.add_argument("--compare", action="store_true", help="Compare paper TP/SL variants on identical candles")
    parser.add_argument("--test-side", action="store_true", help="Compare LONG-only, SHORT-only and both on chronological train and holdout")
    parser.add_argument("--cost-stress", action="store_true", help="Stress test C strategy with extra per-side execution costs")
    parser.add_argument("--test-trend-filter", action="store_true", help="Compare fixed EMA gap thresholds on train and untouched holdout")
    parser.add_argument("--diagnose-c", action="store_true", help="Break down C strategy trades by month and side")
    parser.add_argument("--validate-c", action="store_true", help="Evaluate selected C variant on chronological holdout last 30 percent")
    args = parser.parse_args()
    with open(args.csv_path, newline="") as f:
        bars = list(csv.DictReader(f))
    if len(bars) < 31:
        raise SystemExit("At least 31 candles required")
    times = [int(b["time"]) for b in bars]
    try:
        quality = candle_quality(bars)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print("CANDLE QUALITY", quality)
    if quality["gaps"] or quality["irregular_intervals"]:
        print("DATA QUALITY WARNING: missing/irregular minutes; interpret performance cautiously")
    if args.test_side:
        cut = int(len(bars) * 0.70)
        for side in (None, "LONG", "SHORT"):
            train = backtest(bars[:cut], take_profit=2.5, stop_loss=-3.0, allowed_side=side, extra_cost_rate=0.0002)
            holdout = backtest(bars[cut:], take_profit=2.5, stop_loss=-3.0, allowed_side=side, extra_cost_rate=0.0002)
            print("SIDE FILTER", side or "BOTH", "TRAIN", train, "HOLDOUT", holdout)
        print("SIDE FILTER NOTE: choose candidate using TRAIN only; HOLDOUT is confirmation, not optimization")
        return
    if args.cost_stress:
        cut = int(len(bars) * 0.70)
        for extra in (0.0, 0.0002, 0.0005):
            train = backtest(bars[:cut], take_profit=2.5, stop_loss=-3.0, extra_cost_rate=extra)
            holdout = backtest(bars[cut:], take_profit=2.5, stop_loss=-3.0, extra_cost_rate=extra)
            print("EXECUTION COST STRESS", extra, "TRAIN", train, "HOLDOUT", holdout)
        return
    if args.test_trend_filter:
        cut = int(len(bars) * 0.70)
        for threshold in (0.0, 0.02, 0.05, 0.10):
            train = backtest(bars[:cut], take_profit=2.5, stop_loss=-3.0, min_ema_gap_pct=threshold)
            holdout = backtest(bars[cut:], take_profit=2.5, stop_loss=-3.0, min_ema_gap_pct=threshold)
            print("EMA GAP FILTER", threshold, "TRAIN", train, "HOLDOUT", holdout)
        print("FILTER NOTE: thresholds are exploratory; choose using train only, then evaluate holdout")
        return
    if args.diagnose_c:
        summary, trades = backtest(bars, take_profit=2.5, stop_loss=-3.0, return_trades=True)
        print("C FULL PERIOD", summary)
        groups = {}
        for trade in trades:
            month = datetime.fromtimestamp(trade["time"] / 1000, timezone.utc).strftime("%Y-%m")
            key = (month, trade["side"])
            groups.setdefault(key, []).append(trade["net"])
        for key, nets in sorted(groups.items()):
            print("C MONTH SIDE", key[0], key[1], "trades", len(nets), "wins", sum(n > 0 for n in nets), "net_usdt", round(sum(nets), 2))
        return
    if args.validate_c:
        cut = int(len(bars) * 0.70)
        # Separate state and balances: no trades carry over the split.
        train = backtest(bars[:cut], take_profit=2.5, stop_loss=-3.0)
        holdout = backtest(bars[cut:], take_profit=2.5, stop_loss=-3.0)
        print("C TRAIN FIRST 70%", train)
        print("C HOLDOUT LAST 30%", holdout)
        print("HOLDOUT STATUS", "PASS" if holdout["trades"] >= 100 and holdout["net_usdt"] > 0 and holdout["max_closed_trade_drawdown_usdt"] < 30 else "NOT VALIDATED")
        return
    result = backtest(bars)
    print("BASELINE", result)
    if args.compare:
        for tp, sl in ((1.5, -2.0), (2.0, -2.0), (2.5, -3.0), (3.0, -3.0)):
            print(f"VARIANT TP={tp} SL={sl}", backtest(bars, take_profit=tp, stop_loss=sl))
    if result["trades"] < 100:
        print("NOT ENOUGH TRADES: do not infer strategy quality from this sample")

if __name__ == "__main__":
    main()
