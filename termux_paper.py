"""BingX public API paper bots. Standard library only. NO REAL ORDERS."""
import csv
import json
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

BASE = "https://open-api.bingx.com"
SYMBOLS = {"BTC": "BTC-USDT", "GOLD": "XAUT-USDT"}
MARGIN = 20.0
LEVERAGE = 20
STOP = 0.003
TAKE = 0.006
FEE = 0.0005


def api(path, params):
    url = BASE + path + "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"User-Agent": "Termux-PaperBot/1.0"})
    with urllib.request.urlopen(request, timeout=15) as response:
        result = json.load(response)
    if result.get("code") != 0:
        raise RuntimeError(str(result))
    return result["data"]


def ema(values, period):
    result = [float(values[0])]
    alpha = 2 / (period + 1)
    for v in values[1:]:
        result.append(alpha * float(v) + (1 - alpha) * result[-1])
    return result


def candles(symbol):
    data = api("/openApi/swap/v3/quote/klines", {"symbol": symbol, "interval": "1m", "limit": 100})
    if not isinstance(data, list):
        raise ValueError("Unexpected candles response: " + str(data)[:200])
    rows = []
    for c in data:
        if isinstance(c, dict):
            rows.append([int(c["time"]), float(c["open"]), float(c["high"]), float(c["low"]), float(c["close"])])
        else:
            rows.append([int(c[0]), float(c[1]), float(c[2]), float(c[3]), float(c[4])])
    rows.sort(key=lambda x: x[0])
    # Exclude candle currently being formed, using exchange candle timestamp.
    now_ms = int(time.time() * 1000)
    return [c for c in rows if c[0] + 60000 <= now_ms]


class Bot:
    def __init__(self, name, symbol):
        self.name, self.symbol = name, symbol
        self.last_stamp = None
        self.position = None
        self.pnl = 0.
        self.cooldown_until = 0
        Path("logs").mkdir(exist_ok=True)
        self.logfile = Path("logs") / (name.lower() + "_paper.csv")

    def save(self, side, entry, out, reason, pnl):
        new = not self.logfile.exists() or self.logfile.stat().st_size == 0
        with self.logfile.open("a", newline="") as f:
            w = csv.writer(f)
            if new:
                w.writerow(["utc", "instrument", "side", "entry", "exit", "reason", "net_usdt"])
            w.writerow([datetime.now(timezone.utc).isoformat(), self.symbol, side, entry, out, reason, round(pnl, 4)])

    def update(self, rows):
        if len(rows) < 35:
            print(f"{self.name}: waiting for 35 closed candles", flush=True)
            return
        stamp = rows[-1][0]
        if self.last_stamp is not None and stamp <= self.last_stamp:
            return
        self.last_stamp = stamp
        prices = [r[4] for r in rows]
        price = prices[-1]
        fast, slow = ema(prices, 9), ema(prices, 21)
        if self.position:
            p = self.position
            hi, lo = rows[-1][2:4]
            stop_hit = lo <= p["stop"] if p["side"] == "LONG" else hi >= p["stop"]
            take_hit = hi >= p["take"] if p["side"] == "LONG" else lo <= p["take"]
            if stop_hit or take_hit:
                out = p["stop"] if stop_hit else p["take"]
                move = (out / p["entry"] - 1) * (1 if p["side"] == "LONG" else -1)
                fee = MARGIN * LEVERAGE * FEE * (1 + out / p["entry"])
                pnl = MARGIN * LEVERAGE * move - fee
                self.pnl += pnl
                self.save(p["side"], p["entry"], out, "SL" if stop_hit else "TP", pnl)
                print(f"{self.name}: CLOSE {p['side']} {pnl:+.3f} USDT, total {self.pnl:+.3f}", flush=True)
                self.position = None
                self.cooldown_until = stamp + 3 * 60000
            return
        if stamp < self.cooldown_until:
            return
        side = ("LONG" if fast[-2] <= slow[-2] and fast[-1] > slow[-1] else
                "SHORT" if fast[-2] >= slow[-2] and fast[-1] < slow[-1] else None)
        if side:
            self.position = {"side": side, "entry": price,
                             "stop": price * (1 - STOP if side == "LONG" else 1 + STOP),
                             "take": price * (1 + TAKE if side == "LONG" else 1 - TAKE)}
            print(f"{self.name}: PAPER {side} at {price}", flush=True)
        else:
            print(f"{self.name}: {price} | waiting for signal", flush=True)


def main():
    bots = [Bot(name, symbol) for name, symbol in SYMBOLS.items()]
    print("BingX BTC + XAUT | 1m | PAPER ONLY | 20 USDT x20 each | Ctrl+C to stop", flush=True)
    while True:
        for bot in bots:
            try:
                bot.update(candles(bot.symbol))
            except Exception as error:
                print(f"{bot.name}: data error: {error}", flush=True)
        time.sleep(15)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped. No real orders placed.")
