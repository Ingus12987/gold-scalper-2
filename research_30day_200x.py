"""30-day 5-minute 200x historical stress test; no live orders."""
import argparse
import time
import ccxt
from research_strategy import ema
SYMBOLS = {"BTC": "BTC/USDT:USDT"}

BAR = 300_000
MARGIN = 20.0
LEVERAGE = 200.0
NOTIONAL = MARGIN * LEVERAGE
FEE_RATE = 0.0005
STOP = 0.003
TAKE = 0.006
# Simplified liquidation boundary: maintenance margin excluded, so real liquidation can occur earlier.
LIQ_MOVE = 1 / LEVERAGE

def fetch_history(exchange, symbol, days):
    end = int(time.time() * 1000) // BAR * BAR
    start = end - days * 288 * BAR
    since = start
    bars = {}
    while since < end:
        limit = min(900, (end - since) // BAR)
        batch = exchange.fetch_ohlcv(symbol, "5m", since=since, limit=limit)
        if not batch:
            raise RuntimeError("Empty candle page")
        for c in batch:
            t = int(c[0])
            if start <= t < end:
                bars[t] = c
        new_since = max(int(c[0]) for c in batch) + BAR
        if new_since <= since:
            raise RuntimeError("Pagination did not advance")
        since = new_since
        if len(bars) % 3600 < 900:
            print(f"  {len(bars)}/{days*288} bars", flush=True)
    ordered = [bars[t] for t in range(start, end, BAR) if t in bars]
    if len(ordered) != days * 288:
        raise RuntimeError(f"Incomplete history: {len(ordered)}/{days*288} bars")
    return ordered

def simulate(bars):
    close = [float(c[4]) for c in bars]
    fast, slow, trend = ema(close,9), ema(close,21), ema(close,50)
    trades = []
    position = None
    cooldown = 0
    for i in range(55, len(bars)):
        high, low = float(bars[i][2]), float(bars[i][3])
        if position:
            side, entry, stop, take, liq = position
            liq_hit = low <= liq if side == 1 else high >= liq
            stop_hit = low <= stop if side == 1 else high >= stop
            take_hit = high >= take if side == 1 else low <= take
            if liq_hit or stop_hit or take_hit:
                # Conservative same-candle ordering: liquidation first, then stop, then target.
                price = liq if liq_hit else stop if stop_hit else take
                gross = side * (price/entry-1)*NOTIONAL
                fees = (1+price/entry)*NOTIONAL*FEE_RATE
                net = max(-MARGIN, gross-fees) if liq_hit else gross-fees
                trades.append((i, net, "LIQ" if liq_hit else "SL" if stop_hit else "TP"))
                position = None
                cooldown = i+3
            continue
        if i < cooldown:
            continue
        j=i-1
        side=1 if fast[j-1]<=slow[j-1] and fast[j]>slow[j] else (-1 if fast[j-1]>=slow[j-1] and fast[j]<slow[j] else 0)
        if side == 1 and not(close[j]>trend[j] and trend[j]>trend[j-5]): side=0
        if side == -1 and not(close[j]<trend[j] and trend[j]<trend[j-5]): side=0
        if side:
            entry=float(bars[i][1])
            if entry<=0: continue
            position=(side,entry,entry*(1-side*STOP),entry*(1+side*TAKE),entry*(1-side*LIQ_MOVE))
            # Evaluate entry bar too; liquidation takes precedence.
            side, entry, stop, take, liq = position
            liq_hit=low<=liq if side==1 else high>=liq
            stop_hit=low<=stop if side==1 else high>=stop
            take_hit=high>=take if side==1 else low<=take
            if liq_hit or stop_hit or take_hit:
                price=liq if liq_hit else stop if stop_hit else take
                gross=side*(price/entry-1)*NOTIONAL
                fees=(1+price/entry)*NOTIONAL*FEE_RATE
                net=max(-MARGIN,gross-fees) if liq_hit else gross-fees
                trades.append((i,net,"LIQ" if liq_hit else "SL" if stop_hit else "TP"))
                position=None
                cooldown=i+3
    return trades

def report(label, trades):
    print(f"{label}: trades={len(trades)} wins={sum(p>0 for _,p,_ in trades)} "
          f"liquidations={sum(reason=='LIQ' for _,_,reason in trades)} "
          f"net={sum(p for _,p,_ in trades):+.3f} USDT", flush=True)

def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--days",type=int,default=30,choices=[30])
    args=parser.parse_args()
    ex=ccxt.bingx({"enableRateLimit":True,"timeout":20000})
    ex.load_markets()
    print("PAPER ONLY: 5m, margin 20 USDT, leverage 200x, notional 4000 USDT")
    print("Liquidation approximation excludes maintenance margin, funding, slippage and gaps.")
    for name,symbol in SYMBOLS.items():
        try:
            print(f"\n{name}: fetching {args.days} days",flush=True)
            bars=fetch_history(ex,symbol,args.days)
            split=23*288
            trades = simulate(bars)
            first = [t for t in trades if t[0] < split]
            last = [t for t in trades if t[0] >= split]
            report(name+" first 23 days (exit date)", first)
            report(name+" last 7 days (exit date)", last)
            report(name+" full 30 days", trades)
            assert len(first) + len(last) == len(trades)
            assert abs(sum(t[1] for t in first) + sum(t[1] for t in last) - sum(t[1] for t in trades)) < 1e-8
            print("CHECK OK: period totals reconcile")
        except (ccxt.BaseError,RuntimeError) as e:
            print(f"{name}: FAILED {e}",flush=True)

if __name__=="__main__":
    main()
