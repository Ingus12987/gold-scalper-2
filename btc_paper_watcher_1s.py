"""Read-only BTC price watcher. Five-minute signals, one-second polling. No orders."""
import time
import ccxt
from research_90day_50x_atr import ema, rsi_series, atr_series

def main():
    exchange = ccxt.bingx({"enableRateLimit": True, "timeout": 10000})
    exchange.load_markets()
    previous = None
    print("DEMO WATCHER | BTC/USDT | 5m signals | 1s price checks | 50x reference only")
    while True:
        try:
            candles = exchange.fetch_ohlcv("BTC/USDT:USDT", "5m", limit=100)
            if len(candles) < 60:
                raise RuntimeError("Insufficient history")
            closed = candles[:-1]
            prices = [float(x[4]) for x in closed]
            fast, slow, trend = ema(prices, 9), ema(prices, 21), ema(prices, 50)
            rsi = rsi_series(prices)
            atr = atr_series(closed)
            ticker = exchange.fetch_ticker("BTC/USDT:USDT")
            price = ticker.get("last")
            if price is None:
                raise RuntimeError("Missing price")
            candle_id = closed[-1][0]
            if candle_id != previous:
                previous = candle_id
                j = len(prices)-1
                signal = "WAIT"
                if fast[j-1] <= slow[j-1] and fast[j] > slow[j] and prices[j] > trend[j] and trend[j] > trend[j-5] and 55 <= rsi[j] <= 70:
                    signal = "LONG"
                elif fast[j-1] >= slow[j-1] and fast[j] < slow[j] and prices[j] < trend[j] and trend[j] < trend[j-5] and 30 <= rsi[j] <= 45:
                    signal = "SHORT"
                if atr[j] is None or not 0.0015 <= atr[j]/prices[j] <= 0.008:
                    signal = "WAIT"
                print(f"Closed 5m candle {candle_id}: {signal}; BTC price {price}", flush=True)
        except (ccxt.BaseError, RuntimeError, TypeError, ValueError) as exc:
            print(f"Price fetch error: {exc}", flush=True)
        time.sleep(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Watcher stopped")
