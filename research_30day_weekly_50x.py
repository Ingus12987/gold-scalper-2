"""Independent 7-day windows across the last 30 days. PAPER ONLY."""
import ccxt
from research_90day_50x_atr import fetch_history, simulate, MARGIN, LEVERAGE

def main():
    ex = ccxt.bingx({"enableRateLimit": True, "timeout": 20000})
    ex.load_markets()
    print(f"PAPER ONLY | BTC 5m | margin {MARGIN:g} USDT | leverage {LEVERAGE:g}x")
    print("Fetching 30 days of BingX candles. No real orders.")
    bars = fetch_history(ex, "BTC/USDT:USDT", 30)
    # Indicators are warmed up on prior bars, rather than resetting each week.
    # Each window is attributed by the bar where the simulated trade EXITED.
    trades = simulate(bars, use_rsi=True, use_atr=True)
    week_lengths = [7, 7, 7, 7, 2]
    start = 0
    for n, days in enumerate(week_lengths, 1):
        end = start + days * 288
        subset = [(i, pnl, reason) for i, pnl, reason in trades if start <= i < end]
        wins = sum(pnl > 0 for _, pnl, _ in subset)
        net = sum(pnl for _, pnl, _ in subset)
        print(f"Period {n} ({days}d): trades={len(subset)} wins={wins} net={net:+.3f} USDT")
        start = end
    print(f"TOTAL 30d: trades={len(trades)} wins={sum(p>0 for _,p,_ in trades)} net={sum(p for _,p,_ in trades):+.3f} USDT")
    print("CAUTION: This is a simplified backtest. No funding, slippage, maintenance margin or gap modeling.")
    print("Do not treat results as a live trading profit forecast.")

if __name__ == "__main__":
    main()
