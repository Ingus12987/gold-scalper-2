"""Compare fixed and ATR-based exits on BTC 5m; paper-only research."""
import ccxt
from research_90day_50x_atr import fetch_history, ema, rsi_series, atr_series, MARGIN, NOTIONAL, FEE_RATE, LEVERAGE

def simulate(bars, stop_mult, take_mult):
    closes=[float(c[4]) for c in bars]
    fast,slow,trend=ema(closes,9),ema(closes,21),ema(closes,50)
    rsi,atr=rsi_series(closes),atr_series(bars)
    trades=[]; position=None; cooldown=0
    for i in range(55,len(bars)):
        o,h,l=map(float,(bars[i][1],bars[i][2],bars[i][3]))
        if position is None and i>=cooldown:
            j=i-1
            side=1 if fast[j-1]<=slow[j-1] and fast[j]>slow[j] else (-1 if fast[j-1]>=slow[j-1] and fast[j]<slow[j] else 0)
            if side==1 and not (closes[j]>trend[j] and trend[j]>trend[j-5] and 55<=rsi[j]<=70):side=0
            if side==-1 and not (closes[j]<trend[j] and trend[j]<trend[j-5] and 30<=rsi[j]<=45):side=0
            if side and not (0.0015<=atr[j]/closes[j]<=0.008):side=0
            if side:
                # ATR from previous fully closed candle; no future leakage.
                stop_dist=stop_mult*atr[j]
                take_dist=take_mult*atr[j]
                position=(side,o,o-side*stop_dist,o+side*take_dist)
        if position:
            side,entry,stop,take=position
            stop_hit=l<=stop if side==1 else h>=stop
            take_hit=h>=take if side==1 else l<=take
            if stop_hit or take_hit:
                # Conservative assumption: stop wins when both touch in same bar.
                exit_price=stop if stop_hit else take
                gross=side*(exit_price/entry-1)*NOTIONAL
                fees=(1+exit_price/entry)*NOTIONAL*FEE_RATE
                trades.append((i,gross-fees))
                position=None;cooldown=i+3
    return trades

def show(name,trades,split):
    for label,subset in (("first 23d",[p for i,p in trades if i<split]),("last 7d",[p for i,p in trades if i>=split]),("full 30d",[p for _,p in trades])):
        print(f"{name} {label}: trades={len(subset)} wins={sum(p>0 for p in subset)} net={sum(subset):+.3f} USDT",flush=True)

def main():
    ex=ccxt.bingx({"enableRateLimit":True,"timeout":20000})
    ex.load_markets()
    print("PAPER ONLY: BTC 5m, margin 20 USDT, leverage 50x; 30 days")
    bars=fetch_history(ex,"BTC/USDT:USDT",30)
    split=23*288
    for stop,take in ((1.0,1.5),(1.5,2.0),(2.0,3.0)):
        trades=simulate(bars,stop,take)
        show(f"ATR SL={stop} TP={take}",trades,split)
    print("CAUTION: simplified candle backtest, ignores slippage, funding, liquidation, gaps and maintenance margin.")
    print("Results are NOT reliable forecasts. No live orders.")
if __name__=="__main__":
    main()
