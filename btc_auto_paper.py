"""BTC/USDT perpetual PAPER bot. No keys, no orders, simulated fills only."""
import time, csv, os
from collections import deque
import ccxt

SYMBOL = "BTC/USDT:USDT"
MARGIN, LEVERAGE = 20.0, 50.0
NOTIONAL = MARGIN * LEVERAGE
FEE_RATE = 0.0005  # illustrative taker fee per side, verify actual exchange fees
SLIPPAGE = 0.0002  # illustrative adverse slippage per fill
STOP_PCT = 0.0015
TRAIL_PCT = 0.0010
ARM_PCT = 0.0016
MAX_LOSS_USDT = 3.0
COOLDOWN = 30
LOG = "paper_trades.csv"

def ema(values, period):
    k = 2 / (period + 1)
    result = values[0]
    for x in values[1:]:
        result += k * (x - result)
    return result

def fill(price, side, opening):
    # adverse simulated fill: buy higher, sell lower
    buy = (side == "LONG") == opening
    return price * (1 + SLIPPAGE if buy else 1 - SLIPPAGE)

def pnl(position, exit_price):
    direction = 1 if position["side"] == "LONG" else -1
    gross = direction * (exit_price - position["entry"]) * position["qty"]
    fees = FEE_RATE * (position["entry"] + exit_price) * position["qty"]
    return gross - fees

def record(position, price, reason, balance):
    exit_price = fill(price, position["side"], False)
    result = pnl(position, exit_price)
    balance += result
    print(f'CLOSE {position["side"]} {reason} | net {result:+.2f} USDT | balance {balance:.2f}', flush=True)
    exists = os.path.isfile(LOG)
    with open(LOG, "a", newline="") as f:
        w = csv.writer(f)
        if not exists:
            w.writerow(["open_time","close_time","side","entry","exit","net_pnl","balance","reason"])
        w.writerow([position["opened"], int(time.time()), position["side"], position["entry"], exit_price, round(result,4), round(balance,4), reason])
    return balance

def main():
    exchange = ccxt.bingx({"enableRateLimit": True, "timeout": 15000})
    exchange.load_markets()
    balance, position, last_signal, last_exit = 100.0, None, None, 0
    recent = deque(maxlen=5)
    print("PAPER ONLY | BTC/USDT | tick exits | 1m trend data | 50x simulated | NO LIVE ORDERS", flush=True)
    while True:
        try:
            ticker = exchange.fetch_ticker(SYMBOL)
            price = float(ticker["last"])
            now = time.time()
            if position:
                direction = 1 if position["side"] == "LONG" else -1
                move = direction * (price / position["entry"] - 1)
                position["peak"] = max(position["peak"], move)
                reason = None
                if pnl(position, fill(price, position["side"], False)) <= -MAX_LOSS_USDT:
                    reason = "MAX_LOSS"
                elif move <= -STOP_PCT:
                    reason = "STOP"
                elif position["peak"] >= ARM_PCT and move <= position["peak"] - TRAIL_PCT:
                    reason = "TRAIL"
                if reason:
                    balance = record(position, price, reason, balance)
                    position, last_exit = None, now
            candles = exchange.fetch_ohlcv(SYMBOL, "1m", limit=55)
            closed = candles[:-1]
            if len(closed) < 50:
                raise RuntimeError("Not enough closed candles")
            candle_id = closed[-1][0]
            closes = [float(c[4]) for c in closed]
            fast, slow, trend = ema(closes, 9), ema(closes, 21), ema(closes, 50)
            momentum = closes[-1] / closes[-4] - 1
            signal = "LONG" if fast > slow > trend and momentum > 0.0005 else "SHORT" if fast < slow < trend and momentum < -0.0005 else None
            if position and signal and signal != position["side"]:
                balance = record(position, price, "REVERSAL", balance)
                position, last_exit = None, now
            if not position and signal and now-last_exit >= COOLDOWN and balance >= MARGIN and candle_id != last_signal:
                entry = fill(price, signal, True)
                position = {"side":signal, "entry":entry, "qty":NOTIONAL/entry, "peak":0.0, "opened":int(now)}
                last_signal = candle_id
                print(f'OPEN {signal} @ {entry:.2f} | margin {MARGIN:.2f} | simulated leverage {LEVERAGE}x', flush=True)
            elif not position and candle_id != last_signal:
                last_signal = candle_id
                print(f'WAIT | BTC {price:.2f} | signal {signal or "NONE"} | balance {balance:.2f}', flush=True)
        except (ccxt.BaseError, RuntimeError, TypeError, ValueError, KeyError) as exc:
            print(f"DATA ERROR (no new trade): {exc}", flush=True)
        time.sleep(1)

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped. Open paper position is not persisted; restart resets balance.")
