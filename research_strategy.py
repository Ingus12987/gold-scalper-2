"""Compare EMA crossover filters on CLOSED BingX 1m candles. Research only; no orders."""
import argparse
import math
import ccxt

SYMBOLS = {"BTC": "BTC/USDT:USDT", "GOLD": "XAUT/USDT:USDT"}
NOTIONAL = 400.0
FEE_RATE = 0.0005
STOP = 0.003
TAKE = 0.006

def ema(prices, period):
    alpha = 2 / (period + 1)
    out = [float(prices[0])]
    for p in prices[1:]:
        out.append(alpha * p + (1 - alpha) * out[-1])
    return out

def simulate(candles, filtered):
    closes = [float(c[4]) for c in candles]
    fast, slow, trend = ema(closes, 9), ema(closes, 21), ema(closes, 50)
    trades = []
    position = None
    cooldown_until = 0
    for i in range(55, len(candles)):
        high, low = float(candles[i][2]), float(candles[i][3])
        if position:
            side, entry, stop, take = position
            stop_hit = low <= stop if side == 1 else high >= stop
            take_hit = high >= take if side == 1 else low <= take
            if stop_hit or take_hit:
                exit_price = stop if stop_hit else take
                gross = side * (exit_price / entry - 1) * NOTIONAL
                fees = (1 + exit_price / entry) * NOTIONAL * FEE_RATE
                trades.append(gross - fees)
                position = None
                cooldown_until = i + 3
            continue
        if i < cooldown_until:
            continue
        # Signal formed at candle i-1 close; entry at candle i open.
        j = i - 1
        side = 1 if fast[j-1] <= slow[j-1] and fast[j] > slow[j] else (
            -1 if fast[j-1] >= slow[j-1] and fast[j] < slow[j] else 0)
        if filtered and side:
            # EMA50 direction and price alignment, using closed candles only.
            if side == 1 and not (closes[j] > trend[j] and trend[j] > trend[j-5]):
                side = 0
            if side == -1 and not (closes[j] < trend[j] and trend[j] < trend[j-5]):
                side = 0
        if side:
            entry = float(candles[i][1])
            if not math.isfinite(entry) or entry <= 0:
                continue
            position = (side, entry, entry * (1 - side * STOP), entry * (1 + side * TAKE))
            # Check stop/take on the entry candle as well.
            stop_hit = low <= position[2] if side == 1 else high >= position[2]
            take_hit = high >= position[3] if side == 1 else low <= position[3]
            if stop_hit or take_hit:
                exit_price = position[2] if stop_hit else position[3]
                trades.append(side * (exit_price / entry - 1) * NOTIONAL
                              - (1 + exit_price / entry) * NOTIONAL * FEE_RATE)
                position = None
                cooldown_until = i + 3
    return trades

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=1000)
    args = parser.parse_args()
    if not 100 <= args.limit <= 1000:
        parser.error("--limit must be 100..1000")
    exchange = ccxt.bingx({"enableRateLimit": True, "timeout": 20000})
    exchange.load_markets()
    for name, symbol in SYMBOLS.items():
        try:
            candles = exchange.fetch_ohlcv(symbol, "1m", limit=args.limit)
            if len(candles) < 100:
                print(f"{name}: insufficient candles")
                continue
            # Exclude current unfinished candle.
            import time
            now_ms = int(time.time() * 1000)
            candles = [c for c in candles if c[0] + 60000 <= now_ms]
            print(f"\\n{name}: {len(candles)} completed 1m candles")
            for label, filtered in (("EMA9/21 baseline", False), ("EMA9/21 + EMA50 filter", True)):
                results = simulate(candles, filtered)
                wins = sum(x > 0 for x in results)
                print(f"{label}: trades={len(results)} wins={wins} net={sum(results):+.3f} USDT")
        except ccxt.BaseError as error:
            print(f"{name}: market data unavailable: {error}")

if __name__ == "__main__":
    main()
