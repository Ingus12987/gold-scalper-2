"""Isolated LONG-only 15m completed EMA50 trend hypothesis; TRAIN only.
No orders, secrets, validation/old holdout or parameter tuning.
"""
import csv, hashlib, json, sys
import pandas as pd
import backtest_gold_profit_auto as engine

def long_gate(bars):
    # Current 15m bucket is incomplete; only use the previous completed bucket.
    frame = pd.DataFrame({"bucket":[int(b["time"])//900000 for b in bars],
                          "close":[float(b["close"]) for b in bars]})
    completed = frame.groupby("bucket",sort=True)["close"].last()
    ema = completed.ewm(span=50,adjust=False,min_periods=50).mean()
    allowed = dict(zip(completed.index,completed >= ema))
    return [bool(allowed.get(int(b["time"])//900000-1,False)) for b in bars]

def run(bars,cost,filter_long):
    allowed=long_gate(bars) if filter_long else None
    original=engine.direction
    index=-1
    def direction(closes,**kw):
        nonlocal index
        index+=1
        side=original(closes,**kw)
        if filter_long and side=="LONG" and not allowed[index]:
            return None
        return side
    engine.direction=direction
    try:
        summary,trades=engine.backtest(bars,take_profit=2.5,stop_loss=-3.0,
              extra_cost_rate=cost,fresh_signal_only=True,return_trades=True)
        return summary,trades
    finally:
        engine.direction=original

def short_ids(trades):
    return [(t["entry_time"],t["time"],t["entry"],t["exit"],t["reason"],t["net"]) for t in trades if t["side"]=="SHORT"]

def counterfactual_baseline_ledger(bars, baseline_trades):
    """Attribution only: retain baseline SHORT fills exactly, remove disallowed LONG fills.
    This is NOT a feasible execution backtest: freed occupancy is deliberately not reused.
    Closed PnL only; never confuse this with equity including an open position.
    """
    allowed = long_gate(bars)
    time_to_gate = {int(b["time"]): flag for b, flag in zip(bars, allowed)}
    retained = [t for t in baseline_trades
                if t["side"] == "SHORT" or time_to_gate.get(int(t["entry_time"]), False)]
    assert short_ids(retained) == short_ids(baseline_trades), "SHORT ledger changed"
    removed = [t for t in baseline_trades
               if t["side"] == "LONG" and not time_to_gate.get(int(t["entry_time"]), False)]
    def metrics(trades):
        net = sum(float(t["net"]) for t in trades)
        cumulative = peak = max_dd = 0.0
        for t in trades:
            cumulative += float(t["net"])
            peak = max(peak, cumulative)
            max_dd = max(max_dd, peak - cumulative)
        return {"closed_net": round(net, 2), "closed_trade_max_dd": round(max_dd, 2)}
    return {"baseline": metrics(baseline_trades),
            "retained": metrics(retained),
            "removed_long": metrics(removed),
            "removed_long_count": len(removed),
            "baseline_long_count": sum(t["side"] == "LONG" for t in baseline_trades),
            "short_count": len(short_ids(baseline_trades)),
            "short_ledger_identical": True,
            "interpretation": "non-executable baseline-fill attribution, closed trades only"}

def main(path):
    with open(path,newline="") as f: bars=list(csv.DictReader(f))
    with open(path,"rb") as raw_file:
        source_sha256=hashlib.sha256(raw_file.read()).hexdigest()
    print("LONG_INPUT_AUDIT",json.dumps({"sha256":source_sha256,"candles":len(bars),
        "first_time":bars[0]["time"] if bars else None,
        "last_time":bars[-1]["time"] if bars else None,
        "note":"fresh public fetch; comparison across runs requires identical sha256"},sort_keys=True),flush=True)
    q=engine.candle_quality(bars)
    if q["gaps"] or q["irregular_intervals"]: raise ValueError(q)
    train=bars[:len(bars)//3]
    # Predeclared chronological train-only subperiods; 100-bar embargo.
    mid=len(train)//2
    for label,segment in (("train_a",train[:mid]),("train_b",train[mid+100:])):
        for cost in (0.0002,0.0005):
            base,bt=run(segment,cost,False)
            filt,ft=run(segment,cost,True)
            same=short_ids(bt)==short_ids(ft)
            cf=counterfactual_baseline_ledger(segment,bt)
            print("LONG_BASELINE_COUNTERFACTUAL",json.dumps({"segment":label,"cost_per_side":cost,**cf},sort_keys=True),flush=True)
            for name,s,t in (("baseline",base,bt),("long_15m_ema50",filt,ft)):
                print("LONG_TRAIN_RESULT",json.dumps({"segment":label,"cost_per_side":cost,
                    "strategy":name,"long_trades":sum(x["side"]=="LONG" for x in t),
                    "short_trades":sum(x["side"]=="SHORT" for x in t),
                    "net_including_open":round(s["equity_including_open_usdt"]-engine.START_BALANCE,2),
                    "drawdown":s["max_closed_trade_drawdown_usdt"],"win_rate":s["win_rate_pct"]},
                    sort_keys=True),flush=True)
            print("SHORT_REGRESSION",json.dumps({"segment":label,"cost_per_side":cost,
                "identical":same,"baseline_short_count":len(short_ids(bt)),
                "filtered_short_count":len(short_ids(ft))}),flush=True)
            if not same:
                print("SHORT_REGRESSION_BLOCK: shared position occupancy changes SHORT executions; candidate cannot be promoted",flush=True)
    print("NO_VALIDATION_OR_HOLDOUT: TRAIN-only diagnostic, no promotion",flush=True)
if __name__=="__main__": main(sys.argv[1])
