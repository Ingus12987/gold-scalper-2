"""Seven-day historical research for BTC and GOLD. No orders or credentials."""
import argparse
import datetime as dt
import random
import time

import ccxt
from research_strategy import SYMBOLS, simulate

MINUTE_MS = 60_000
PAGE_SIZE = 900
MAX_RETRIES = 5


def load_history(exchange, symbol, days=7, pause=2.0):
    end = (int(time.time() * 1000) // MINUTE_MS) * MINUTE_MS
    start = end - days * 24 * 60 * MINUTE_MS
    cursor = start
    rows = {}
    while cursor < end:
        page = None
        for attempt in range(MAX_RETRIES):
            try:
                page = exchange.fetch_ohlcv(symbol, "1m", since=cursor, limit=PAGE_SIZE)
                break
            except (ccxt.NetworkError, ccxt.ExchangeNotAvailable, ccxt.RateLimitExceeded) as exc:
                delay = min(120, 5 * 2 ** attempt) + random.uniform(0, 1)
                print(f"Temporary API error: {exc}; waiting {delay:.1f}s", flush=True)
                time.sleep(delay)
            except ccxt.ExchangeError as exc:
                if "100410" not in str(exc) and "429" not in str(exc):
                    raise
                delay = min(120, 5 * 2 ** attempt) + random.uniform(0, 1)
                print(f"Exchange busy; waiting {delay:.1f}s", flush=True)
                time.sleep(delay)
        if page is None:
            raise RuntimeError("Historical data unavailable after retries")
        if not page:
            raise RuntimeError("Exchange returned an empty page")
        latest = max(int(c[0]) for c in page)
        for candle in page:
            timestamp = int(candle[0])
            if start <= timestamp < end and timestamp % MINUTE_MS == 0:
                rows[timestamp] = candle
        if latest < cursor:
            raise RuntimeError("Exchange did not advance historical cursor")
        cursor = latest + MINUTE_MS
        print(f"Loaded {len(rows)} / {days * 1440} candles", flush=True)
        if cursor < end:
            time.sleep(pause)
    candles = [rows[t] for t in sorted(rows)]
    expected = days * 1440
    if len(candles) != expected:
        raise RuntimeError(
            f"History incomplete: {len(candles)}/{expected} candles. "
            "Skipping backtest to avoid misleading results."
        )
    return candles


def main():
    parser = argparse.ArgumentParser(description="Research only: no real trades")
    parser.add_argument("--days", type=int, default=7)
    parser.add_argument("--pause", type=float, default=2.0)
    args = parser.parse_args()
    if not 1 <= args.days <= 30 or args.pause < 2:
        parser.error("--days must be 1..30 and --pause must be at least 2")
    exchange = ccxt.bingx({"enableRateLimit": True, "timeout": 30000})
    exchange.load_markets()
    for name, symbol in SYMBOLS.items():
        print(f"\n{name}: requesting {args.days} days of 1m candles", flush=True)
        try:
            candles = load_history(exchange, symbol, args.days, args.pause)
            for label, filtered in (("EMA9/21", False), ("EMA9/21 + EMA50", True)):
                results = simulate(candles, filtered)
                wins = sum(p > 0 for p in results)
                print(f"{name} {label}: trades={len(results)} wins={wins} "
                      f"net={sum(results):+.3f} USDT", flush=True)
        except (ccxt.BaseError, RuntimeError) as exc:
            print(f"{name}: SKIPPED - {exc}", flush=True)


if __name__ == "__main__":
    main()
