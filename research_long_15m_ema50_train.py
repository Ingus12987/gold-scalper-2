"""Isolated LONG-only 15m completed EMA50 trend hypothesis; TRAIN only.
No orders, secrets, validation/old holdout or parameter tuning.
"""
import csv, hashlib, json, sys
from collections import defaultdict
from datetime import datetime, timezone
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

def run(bars,cost,filter_long,allowed_side=None):
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
              extra_cost_rate=cost,fresh_signal_only=True,return_trades=True,allowed_side=allowed_side)
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

def baseline_train_diagnostics(trades, segment, cost, bars):
    """Descriptive TRAIN-only closed fills; no strategy selection or look-ahead."""
    groups = defaultdict(list)
    # Past-only volatility/regime tags; descriptive TRAIN diagnostics, not a gate.
    close = pd.Series([float(b["close"]) for b in bars])
    high = pd.Series([float(b["high"]) for b in bars])
    low = pd.Series([float(b["low"]) for b in bars])
    tr = pd.concat([high-low, (high-close.shift()).abs(),
                    (low-close.shift()).abs()], axis=1).max(axis=1)
    atr = tr.rolling(14, min_periods=14).mean().shift(1)
    ema = close.ewm(span=50, adjust=False, min_periods=50).mean().shift(1)
    time_idx = {int(b["time"]): i for i, b in enumerate(bars)}
    # Conservative excursion bounds: exclude exit candle extremes because
    # their ordering relative to the actual fill is unknown. Include fill price.
    for t in trades:
        start = time_idx.get(int(t["entry_time"]))
        end = time_idx.get(int(t["time"]))
        if start is None or end is None or end < start:
            raise ValueError("Trade timestamps not aligned with candles")
        entry, exit_price = float(t["entry"]), float(t["exit"])
        highs = [entry, exit_price]
        lows = [entry, exit_price]
        if end > start:
            highs.append(float(high.iloc[start:end].max()))
            lows.append(float(low.iloc[start:end].min()))
        sign = 1 if t["side"] == "LONG" else -1
        favorable = max(sign*(p-entry)/entry*100 for p in (max(highs), min(lows)))
        adverse = min(sign*(p-entry)/entry*100 for p in (max(highs), min(lows)))
        print("TRAIN_EXCURSION", json.dumps({
            "segment": segment, "cost_per_side": cost, "side": t["side"],
            "entry_time": t["entry_time"], "exit_time": t["time"],
            "reason": t["reason"], "net_usdt": t["net"],
            "mfe_pct_lower_bound": round(favorable, 6),
            "mae_pct_magnitude_lower_bound": round(-adverse, 6),
            "exit_bar_extremes_excluded": True
        }, sort_keys=True), flush=True)
        hour = datetime.fromtimestamp(int(t["entry_time"])/1000, timezone.utc).hour
        idx = time_idx.get(int(t["entry_time"]))
        regime = "unknown"
        volatility = "unknown"
        if idx is not None and pd.notna(ema.iloc[idx]):
            regime = "above_ema50" if float(close.iloc[idx-1]) >= float(ema.iloc[idx]) else "below_ema50" if idx > 0 else "unknown"
        if idx is not None and pd.notna(atr.iloc[idx]) and idx > 0:
            # ATR as percentage of prior close; fixed ex-ante 0.1% boundary.
            volatility = "atr14_ge_0.1pct" if float(atr.iloc[idx])/float(close.iloc[idx-1]) >= 0.001 else "atr14_lt_0.1pct"
        for dimension, value in (("side", t["side"]), ("utc_entry_hour", f"{hour:02d}"),
                                 ("exit_reason", str(t["reason"])),
                                 ("past_only_ema50_regime", regime),
                                 ("past_only_atr14_volatility", volatility)):
            groups[(dimension, value, t["side"])].append(float(t["net"]))
    for (dimension, value, side), pnl in sorted(groups.items()):
        print("LONG_TRAIN_DIAGNOSTIC", json.dumps({
            "segment": segment, "cost_per_side": cost, "dimension": dimension,
            "value": value, "side": side, "trades": len(pnl),
            "closed_net_usdt": round(sum(pnl), 2),
            "losing_trades": sum(x < 0 for x in pnl),
            "note": "TRAIN closed fills only; no MAE/MFE or open equity in this table"
        }, sort_keys=True), flush=True)

def isolated_sleeves(segment, label, cost):
    """Separate virtual accounts, not a deployable shared-capital portfolio.

    Compare LONG-only baseline vs gated LONG-only; independently rerun
    SHORT-only with the gate enabled to verify exact fill invariance.
    Each sleeve has its own original starting capital and risk limits.
    Never add these PnLs as if they came from one original account.
    """
    for side in ("SHORT", "LONG"):
        baseline, bt = run(segment, cost, False, allowed_side=side)
        candidate, ct = run(segment, cost, True, allowed_side=side)
        if side == "SHORT":
            # A rejected LONG becomes None, which must not affect SHORT
            # transitions or executions in a dedicated SHORT account.
            assert bt == ct and baseline == candidate, "Isolated SHORT regression failed"
        print("ISOLATED_SLEEVE_RESULT", json.dumps({
            "segment": label, "cost_per_side": cost, "side": side,
            "baseline": baseline, "candidate": candidate,
            "baseline_cost": round(sum(t["cost_usdt"] for t in bt), 4),
            "candidate_cost": round(sum(t["cost_usdt"] for t in ct), 4),
            "identical_full_ledger": bt == ct,
            "capital_model": "separate full-size virtual accounts; no portfolio aggregation"
        }, sort_keys=True), flush=True)

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
            baseline_train_diagnostics(bt,label,cost,segment)
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
            isolated_sleeves(segment, label, cost)
    print("NO_VALIDATION_OR_HOLDOUT: TRAIN-only diagnostic, no promotion",flush=True)
if __name__=="__main__": main(sys.argv[1])
