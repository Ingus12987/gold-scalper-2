"""30-day historical paper research. No orders or API credentials."""
import argparse
import time
from datetime import datetime, timezone
import ccxt
from research_strategy import SYMBOLS, simulate

MINUTE = 60_000

def fetch_history(exchange, days):
    end = (int(time.time() * 1000) // MINUTE) * MINUTE
    start = end - days * 1440 * MINUTE
    since = start
    candles = {}
    while since < end:
        batch = exchange.fetch_ohlcv(
            exchange.current_symbol, "1m", since=since,
            limit=min(900, (end - since) // MINUTE))
        if not batch:
            raise RuntimeError("Missing candles: exchange returned an empty page")
        for candle in batch:
            stamp = int(candle[0])
            if start <= stamp < end and stamp + MINUTE <= end:
                candles[stamp] = candle
        next_since = max(int(c[0]) for c in batch) + MINUTE
        if next_since <= since:
            raise RuntimeError("Pagination stopped advancing")
        since = next_since
        print(f"  Loaded {len(candles)}/{days * 1440}", flush=True)
    ordered = [candles[t] for t in range(start, end, MINUTE) if t in candles]
    if len(ordered) != days * 1440:
        raise RuntimeError(f"Missing {days * 1440 - len(ordered)} candles; refusing partial backtest")
    return ordered

def report(label, candles):
    results = simulate(candles, True)
    wins = sum(p > 0 for p in results)
    print(f"{label}: trades={len(results)} wins={wins} net={sum(results):+.3f} USDT")

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--days", type=int, default=30, choices=[30])
    args = parser.parse_args()
    exchange = ccxt.bingx({"enableRateLimit": True, "timeout": 20000})
    exchange.load_markets()
    for name, symbol in SYMBOLS.items():
        try:
            exchange.current_symbol = symbol
            print(f"\n{name}: downloading {args.days} days", flush=True)
            candles = fetch_history(exchange, args.days)
            split = 23 * 1440
            # 23 days for development, 7 later days held out.
            report(f"{name} first 23 days", candles[:split])
            # Warm-up EMA on 55 earlier candles; trades only after that.
            report(f"{name} last 7 days (held-out)", candles[split-55:])
            report(f"{name} full 30 days", candles)
        except (ccxt.BaseError, RuntimeError) as error:
            print(f"{name}: FAILED: {error}", flush=True)

if __name__ == "__main__":
    main()
