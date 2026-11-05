"""BingX gold 5m paper trading. Public REST only. NO LIVE ORDERS."""
import time
import csv
import os
from datetime import datetime, timezone
import ccxt
from research_90day_50x_atr import ema, rsi_series, atr_series

SYMBOL = None
MARGIN = 20.0
LEVERAGE = 200.0
NOTIONAL = MARGIN * LEVERAGE
FEE_RATE = 0.0005
SLIPPAGE = 0.0003
POLL_SECONDS = 5
MAX_HOLD_SECONDS = 3600
MAX_LOSS = MARGIN
STALE_SECONDS = 30
LOG_FILE = "gold_paper_trades_v2.csv"

def main():
    exchange = ccxt.bingx({"enableRateLimit": True, "timeout": 15000})
    markets = exchange.load_markets()
    candidates = [m for m in markets.values() if m.get("swap") and m.get("linear") and m.get("active") is not False and (("XAU" in str(m.get("base","")).upper()) or ("GOLD" in str(m.get("base","")).upper()))]
    if not candidates:
        print("GOLD MARKET UNAVAILABLE on BingX via CCXT. No simulation started.", flush=True)
        return
    candidates.sort(key=lambda m: (not str(m.get("symbol","")).endswith(":USDT"), str(m.get("symbol",""))))
    preferred = next((m for m in candidates if "NCCGOLD2USD" in str(m.get("symbol", ""))), None)
    if preferred is None:
        print("Expected gold contract not found; stopping instead of using another asset.", flush=True)
        return
    symbol = preferred["symbol"]
    print(f"Selected BingX gold market: {symbol}", flush=True)
    print("200x is a PAPER calculation only. Exchange 200x availability is NOT confirmed.", flush=True)
    position = None
    total = 0.0
    if not os.path.exists(LOG_FILE):
        with open(LOG_FILE, "w", newline="") as f:
            csv.writer(f).writerow(["utc", "side", "entry", "exit", "reason", "net_usdt", "session_total_usdt"])
    last_closed_candle = None
    last_entry_candle = None
    candles = None
    next_candle_refresh = 0
    cooldown_until = 0
    errors = 0
    print("PAPER ONLY | GOLD 5m signals | 5s REST checks | 20 USDT margin | 200x", flush=True)
    print("Expanded RSI and ATR filters; trade exits saved to CSV. Paper only.", flush=True)
    print("NO REAL ORDERS. Price-check exits are approximate; gaps, fees and liquidation can differ.", flush=True)
    while True:
        try:
            now = time.time()
            if candles is None or now >= next_candle_refresh:
                candles = exchange.fetch_ohlcv(symbol, "5m", limit=110)
                if len(candles) < 60:
                    raise RuntimeError("Not enough candles")
                next_candle_refresh = now + 60
            ticker = exchange.fetch_ticker(symbol)
            price = ticker.get("last")
            ts = ticker.get("timestamp")
            if price is None or float(price) <= 0:
                raise RuntimeError("Missing price")
            if ts is None or abs(now - ts / 1000) > STALE_SECONDS:
                raise RuntimeError("Missing or stale ticker timestamp")
            price = float(price)
            errors = 0
            if position is not None:
                side, entry, stop, take, opened = position
                stop_hit = price <= stop if side == 1 else price >= stop
                take_hit = price >= take if side == 1 else price <= take
                expired = now - opened >= MAX_HOLD_SECONDS
                if stop_hit or take_hit or expired:
                    exit_price = price * (1 - side * SLIPPAGE)
                    gross = side * (exit_price / entry - 1) * NOTIONAL
                    fees = (1 + exit_price / entry) * NOTIONAL * FEE_RATE
                    net = max(-MAX_LOSS, gross - fees)
                    total += net
                    with open(LOG_FILE, "a", newline="") as f:
                        csv.writer(f).writerow([datetime.now(timezone.utc).isoformat(), "LONG" if side == 1 else "SHORT", round(entry, 4), round(exit_price, 4), "STOP" if stop_hit else "TAKE" if take_hit else "TIME", round(net, 4), round(total, 4)])
                    reason = "STOP" if stop_hit else "TAKE" if take_hit else "TIME"
                    print(f"CLOSE {'LONG' if side == 1 else 'SHORT'} price={exit_price:.2f} {reason} net={net:+.3f} total={total:+.3f} USDT", flush=True)
                    position = None
                    cooldown_until = now + 60
            closed = candles[:-1]
            candle_id = closed[-1][0]
            if candle_id != last_closed_candle:
                last_closed_candle = candle_id
                closes = [float(x[4]) for x in closed]
                fast, slow, trend = ema(closes, 9), ema(closes, 21), ema(closes, 50)
                rsi = rsi_series(closes)
                atr = atr_series(closed)
                j = len(closes) - 1
                side = 0
                if fast[j-1] <= slow[j-1] and fast[j] > slow[j] and closes[j] > trend[j] and trend[j] > trend[j-5] and 50 <= rsi[j] <= 75:
                    side = 1
                elif fast[j-1] >= slow[j-1] and fast[j] < slow[j] and closes[j] < trend[j] and trend[j] < trend[j-5] and 25 <= rsi[j] <= 50:
                    side = -1
                if atr[j] is None or not 0.0008 <= atr[j] / closes[j] <= 0.008:
                    side = 0
                print(f"{datetime.now(timezone.utc).isoformat(timespec='seconds')} 5m signal={'LONG' if side == 1 else 'SHORT' if side == -1 else 'WAIT'} GOLD={price:.2f}", flush=True)
                if side and position is None and now >= cooldown_until and candle_id != last_entry_candle:
                    entry = price * (1 + side * SLIPPAGE)
                    stop = entry - side * 2 * atr[j]
                    take = entry + side * 3 * atr[j]
                    position = (side, entry, stop, take, now)
                    last_entry_candle = candle_id
                    print(f"OPEN PAPER {'LONG' if side == 1 else 'SHORT'} entry={entry:.2f} SL={stop:.2f} TP={take:.2f}", flush=True)
            time.sleep(POLL_SECONDS)
        except (ccxt.BaseError, RuntimeError, ValueError, TypeError) as exc:
            errors += 1
            delay = min(300, 15 * (2 ** min(errors - 1, 4)))
            print(f"DATA ERROR: {exc}; pause {delay}s; no new entry", flush=True)
            candles = None
            time.sleep(delay)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped. Paper positions are not persisted.", flush=True)
