"""DEMO-only chronological walk-forward audit. Never places orders.

Usage: python demo_walkforward.py candles.csv
CSV: time,open,high,low,close in chronological order.
The last third is deliberately excluded: no holdout peeking.
"""
import argparse
import csv
import json
from backtest_gold_profit_auto import backtest, candle_quality

CANDIDATES = {
    "baseline": {},
    "short_only": {"allowed_side": "SHORT"},
    "long_only": {"allowed_side": "LONG"},
    "fresh_short": {"allowed_side": "SHORT", "fresh_signal_only": True},
}

def run(bars):
    if len(bars) < 300:
        raise ValueError("Need at least 300 candles")
    quality = candle_quality(bars)
    if quality["gaps"] or quality["irregular_intervals"]:
        raise ValueError("Missing or irregular candles: " + str(quality))
    # Use two disjoint training blocks; reserve final third untouched.
    cut1, cut2 = len(bars) // 3, 2 * len(bars) // 3
    output = {"candles": len(bars), "train_a": cut1,
              "train_b": cut2-cut1, "holdout_reserved": len(bars)-cut2,
              "candidates": {}}
    for name, kw in CANDIDATES.items():
        results = []
        for segment in (bars[:cut1], bars[cut1:cut2]):
            stress = []
            for cost in (0.0002, 0.0005):
                s = backtest(segment, take_profit=2.5, stop_loss=-3.0,
                             extra_cost_rate=cost, **kw)
                stress.append({"extra_cost_per_side": cost,
                               "trades": s["trades"],
                               "win_rate_pct": s["win_rate_pct"],
                               "net_including_open_usdt": round(
                                   s["net_usdt"]+s["unrealized_pnl_usdt"], 2),
                               "drawdown_usdt": s["max_closed_trade_drawdown_usdt"]})
            results.append(stress)
        output["candidates"][name] = results
    output["positive_all_train_stress"] = [
        name for name, blocks in output["candidates"].items()
        if all(test["trades"] >= 10 and test["net_including_open_usdt"] > 0
               for block in blocks for test in block)]
    return output

def main():
    p = argparse.ArgumentParser()
    p.add_argument("csv_path")
    args = p.parse_args()
    with open(args.csv_path, newline="") as f:
        bars = list(csv.DictReader(f))
    print(json.dumps(run(bars), indent=2))

if __name__ == "__main__":
    main()
