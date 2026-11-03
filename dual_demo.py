"""Two independent PAPER trading strategies using public BingX market data.
No exchange orders are ever submitted.
"""
import argparse
import csv
import os
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt

DEFAULTS = {"BTC": "BTC/USDT:USDT", "GOLD": "XAU/USDT:USDT"}
MARGIN = 20.0
LEVERAGE = 20.0
STOP_PCT = 0.003
TAKE_PCT = 0.006
FEE_RATE = 0.0005  # illustrative taker fee per side; verify current fees
COOLDOWN_BARS = 3


def ema(values, period):
    alpha = 2 / (period + 1)
    result = values[0]
    for value in values[1:]:
        result = alpha * value + (1 - alpha) * result
    return result


class PaperBot:
    def __init__(self, name, symbol, folder):
        self.name, self.symbol = name, symbol
        self.position = None
        self.last_exit_bar = -100
        self.bar = 0
        self.pnl = 0.0
        self.path = folder / (name.lower() + "_trades.csv")

    def log(self, side, entry, exit_price, reason, pnl):
        new = not self.path.exists()
        with self.path.open("a", newline="", encoding="utf-8") as file:
            writer = csv.writer(file)
            if new:
                writer.writerow(["utc", "bot", "symbol", "side", "entry", "exit", "reason", "net_pnl_usdt"])
            writer.writerow([datetime.now(timezone.utc).isoformat(), self.name, self.symbol,
                             side, entry, exit_price, reason, round(pnl, 4)])

    def update(self, candles):
        # Exclude still-forming candle; signals use completed candles only.
        closed = candles[:-1]
        if len(closed) < 35:
            return
        stamp = closed[-1][0]
        if getattr(self, "last_stamp", None) == stamp:
            return
        self.last_stamp = stamp
        self.bar += 1
        closes = [float(c[4]) for c in closed]
        price = closes[-1]
        fast, slow = ema(closes[-30:], 9), ema(closes[-30:], 21)
        prev_fast, prev_slow = ema(closes[-31:-1], 9), ema(closes[-31:-1], 21)

        if self.position:
            p = self.position
            # Conservative intrabar assumption: stop wins when both touched.
            high, low = float(closed[-1][2]), float(closed[-1][3])
            if p["side"] == "LONG":
                stop_hit, take_hit = low <= p["stop"], high >= p["take"]
            else:
                stop_hit, take_hit = high >= p["stop"], low <= p["take"]
            if stop_hit or take_hit:
                out = p["stop"] if stop_hit else p["take"]
                gross = (out - p["entry"]) / p["entry"] * MARGIN * LEVERAGE
                if p["side"] == "SHORT":
                    gross = -gross
                fees = (p["entry"] + out) / p["entry"] * MARGIN * LEVERAGE * FEE_RATE
                net = gross - fees
                self.pnl += net
                self.log(p["side"], p["entry"], out, "SL" if stop_hit else "TP", net)
                print(f"{self.name}: CLOSE {p['side']} net={net:+.3f} USDT total={self.pnl:+.3f}", flush=True)
                self.position = None
                self.last_exit_bar = self.bar
            return

        if self.bar - self.last_exit_bar < COOLDOWN_BARS:
            return
        side = "LONG" if prev_fast <= prev_slow and fast > slow else (
            "SHORT" if prev_fast >= prev_slow and fast < slow else None)
        if not side:
            return
        self.position = {
            "side": side, "entry": price,
            "stop": price * (1 - STOP_PCT if side == "LONG" else 1 + STOP_PCT),
            "take": price * (1 + TAKE_PCT if side == "LONG" else 1 - TAKE_PCT)
        }
        print(f"{self.name}: PAPER OPEN {side} at {price:.5f}", flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--btc-symbol", default=os.getenv("BTC_SYMBOL", DEFAULTS["BTC"]))
    parser.add_argument("--gold-symbol", default=os.getenv("GOLD_SYMBOL", DEFAULTS["GOLD"]))
    parser.add_argument("--poll", type=int, default=15)
    args = parser.parse_args()
    if args.poll < 5:
        parser.error("--poll must be at least 5 seconds")
    exchange = ccxt.bingx({"enableRateLimit": True, "options": {"defaultType": "swap"}})
    markets = exchange.load_markets()
    symbols = {"BTC": args.btc_symbol, "GOLD": args.gold_symbol}
    missing = [f"{name} ({symbol})" for name, symbol in symbols.items() if symbol not in markets]
    if missing:
        raise SystemExit("Unsupported BingX symbol(s): " + ", ".join(missing) +
                         ". Check BingX/ccxt market names; no trades placed.")
    folder = Path("logs")
    folder.mkdir(exist_ok=True)
    bots = [PaperBot(name, symbol, folder) for name, symbol in symbols.items()]
    print("PAPER ONLY: BTC and GOLD. No API keys, no real orders. Ctrl+C stops.", flush=True)
    while True:
        for bot in bots:
            try:
                candles = exchange.fetch_ohlcv(bot.symbol, "1m", limit=80)
                bot.update(candles)
            except (ccxt.NetworkError, ccxt.ExchangeError) as error:
                print(f"{bot.name}: market data error: {error}", flush=True)
        time.sleep(args.poll)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped. No exchange orders were placed.")
