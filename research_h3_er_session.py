"""Isolated H3 DEMO research. Never places orders; no private API keys.
Signal gate uses Kaufman ER(30) and predeclared UTC 07:00-17:59 session.
Same completed candle used for signal; execution remains next candle open.
This is exploratory train/validation ONLY: old final third is contaminated.
"""
import argparse
import csv
import json
from datetime import datetime, timezone
import backtest_gold_profit_auto as engine

def er30(closes):
    if len(closes) < 31:
        return 0.0
    last = closes[-31:]
    movement = abs(last[-1] - last[0])
    noise = sum(abs(b-a) for a,b in zip(last,last[1:]))
    return movement/noise if noise else 0.0

def evaluate(bars, cost, gate):
    original = engine.direction
    index = -1
    def gated(closes, **kwargs):
        nonlocal index
        index += 1
        signal = original(closes, **kwargs)
        if not gate or not signal:
            return signal
        utc_hour = datetime.fromtimestamp(int(bars[index]["time"])/1000,timezone.utc).hour
        if not (7 <= utc_hour < 18):
            return None
        if er30(closes) < 0.25:
            return None
        return signal
    engine.direction = gated
    try:
        return engine.backtest(bars,take_profit=2.5,stop_loss=-3.0,
            extra_cost_rate=cost,fresh_signal_only=True,return_trades=True)
    finally:
        engine.direction = original

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("csv_path")
    args=ap.parse_args()
    with open(args.csv_path,newline="") as f:
        bars=list(csv.DictReader(f))
    assert len(bars)>3000
    quality=engine.candle_quality(bars)
    print("DATA_QUALITY",json.dumps(quality),flush=True)
    if quality["gaps"] or quality["irregular_intervals"]:
        raise SystemExit("Irregular candles: stop")
    # Two thirds only: no evaluation of previously inspected final third.
    n=len(bars); train=bars[:n//3]; validation=bars[n//3+100:2*n//3]
    for name, segment in (("train",train),("validation",validation)):
        for cost in (0.0002,0.0005):
            for gated in (False,True):
                summary,trades=evaluate(segment,cost,gated)
                result={"segment":name,"strategy":"H3_ER30_0.25_UTC07_18" if gated else "fresh_baseline",
                    "extra_cost_per_side":cost,**summary,
                    "gross_usdt":round(sum(t["gross_usdt"] for t in trades),2),
                    "cost_usdt":round(sum(t["cost_usdt"] for t in trades),2),
                    "net_including_open_usdt":round(summary["equity_including_open_usdt"]-engine.START_BALANCE,2)}
                # Diagnostic only: identify which side and exit type create losses.
                # No parameter selection is made from these diagnostics.
                for side in ("LONG", "SHORT"):
                    side_trades = [t for t in trades if t["side"] == side]
                    audit = {"segment": name, "strategy": result["strategy"],
                        "extra_cost_per_side": cost, "side": side,
                        "trades": len(side_trades),
                        "gross_usdt": round(sum(t["gross_usdt"] for t in side_trades), 2),
                        "cost_usdt": round(sum(t["cost_usdt"] for t in side_trades), 2),
                        "net_closed_usdt": round(sum(t["net"] for t in side_trades), 2),
                        "stops": sum(t["reason"] == "SL" for t in side_trades),
                        "targets": sum(t["reason"] == "TP" for t in side_trades)}
                    print("H3_SIDE_AUDIT", json.dumps(audit, sort_keys=True), flush=True)
                print("H3_RESULT",json.dumps(result,sort_keys=True),flush=True)
    print("NO_HOLDOUT_TEST: last third deliberately excluded",flush=True)
if __name__=="__main__":
    main()
