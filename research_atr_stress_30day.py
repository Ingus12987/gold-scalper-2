"""BTC 5m ATR exit stress test. Paper research only, no real orders."""
import ccxt
from research_90day_50x_atr import fetch_history, ema, rsi_series, atr_series
MARGIN=20.0
LEVERAGE=50.0
NOTIONAL=MARGIN*LEVERAGE
FEE=0.0005
SLIPPAGE=0.0003
MAINTENANCE=0.005
FUNDING_PER_8H=0.0001

def run(bars):
    close=[float(c[4]) for c in bars]
    fast,slow,trend=ema(close,9),ema(close,21),ema(close,50)
    rsi,atr=rsi_series(close),atr_series(bars)
    results=[]
    pos=None
    cooldown=0
    for i in range(55,len(bars)):
        o,h,l=map(float,(bars[i][1],bars[i][2],bars[i][3]))
        if pos is None and i>=cooldown:
            j=i-1
            side=1 if fast[j-1]<=slow[j-1] and fast[j]>slow[j] else (-1 if fast[j-1]>=slow[j-1] and fast[j]<slow[j] else 0)
            if side==1 and not (close[j]>trend[j] and trend[j]>trend[j-5] and 55<=rsi[j]<=70):side=0
            if side==-1 and not (close[j]<trend[j] and trend[j]<trend[j-5] and 30<=rsi[j]<=45):side=0
            if side and not (0.0015<=atr[j]/close[j]<=0.008):side=0
            if side:
                entry=o*(1+side*SLIPPAGE)
                stop=entry-side*2*atr[j]
                take=entry+side*3*atr[j]
                # Approximate liquidation threshold; not exchange liquidation formula.
                liq=entry*(1-side*(1/LEVERAGE-MAINTENANCE))
                pos=(side,entry,stop,take,liq,i)
        if pos:
            side,entry,stop,take,liq,opened=pos
            liq_hit=l<=liq if side==1 else h>=liq
            stop_hit=l<=stop if side==1 else h>=stop
            take_hit=h>=take if side==1 else l<=take
            if liq_hit or stop_hit or take_hit:
                # Conservative intrabar assumption: liquidation before stop before take.
                raw=liq if liq_hit else stop if stop_hit else take
                exit_price=raw*(1-side*SLIPPAGE)
                gross=side*(exit_price/entry-1)*NOTIONAL
                fees=(1+exit_price/entry)*NOTIONAL*FEE
                funding=(i-opened)*5/(8*60)*NOTIONAL*FUNDING_PER_8H
                net=max(-MARGIN,gross-fees-funding) if liq_hit else gross-fees-funding
                results.append((i,net,"LIQ" if liq_hit else "SL" if stop_hit else "TP"))
                pos=None
                cooldown=i+3
    return results

def report(label, rows):
    print(f"{label}: trades={len(rows)} wins={sum(p>0 for _,p,_ in rows)} liquidations={sum(r=='LIQ' for _,_,r in rows)} net={sum(p for _,p,_ in rows):+.3f} USDT",flush=True)

def main():
    print("PAPER ONLY | BTC 5m | margin 20 USDT | 50x | ATR stop 2 / take 3")
    print("Assumptions: taker fee 0.05% each side; adverse slippage 0.03% each side; maintenance 0.5%; illustrative funding 0.01% / 8h.")
    ex=ccxt.bingx({"enableRateLimit":True,"timeout":20000})
    ex.load_markets()
    try:
        bars=fetch_history(ex,"BTC/USDT:USDT",30)
    except (ccxt.BaseError,RuntimeError) as e:
        print(f"DATA UNAVAILABLE: {e}")
        return
    trades=run(bars)
    report("First 23 days",[t for t in trades if t[0]<23*288])
    report("Last 7 days",[t for t in trades if t[0]>=23*288])
    report("Full 30 days",trades)
    print("NOTE: Same 30-day sample as earlier selection, NOT out-of-sample validation.")
    print("Candle data cannot resolve true intrabar ordering, gaps, or exchange liquidation rules. No live orders.")
if __name__=="__main__":
    main()
