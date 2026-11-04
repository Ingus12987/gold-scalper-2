"""Two independent PAPER trading strategies using public BingX market data.
No exchange orders are ever submitted.
"""
import argparse
import csv
import json
import math
import os
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

import ccxt

DEFAULTS = {"BTC": "BTC/USDT:USDT", "GOLD": "XAUT/USDT:USDT"}
MARGIN = 20.0
LEVERAGE = 20.0
STOP_PCT = 0.003
TAKE_PCT = 0.006
FEE_RATE = 0.0005  # illustrative taker fee per side; verify current fees
COOLDOWN_BARS = 3


class StateError(RuntimeError):
    """Raised when saved paper-trading state cannot be used safely."""


def ema_series(values, period):
    if not values:
        raise ValueError("No prices")
    alpha = 2 / (period + 1)
    result = [float(values[0])]
    for value in values[1:]:
        result.append(alpha * float(value) + (1 - alpha) * result[-1])
    return result


class PaperBot:
    def __init__(self, name, symbol, folder):
        self.name, self.symbol = name, symbol
        self.folder = Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.position = None
        self.last_exit_bar = -100
        self.bar = 0
        self.pnl = 0.0
        self.last_stamp = None
        self.closed_trades = []
        self.path = self.folder / (name.lower() + "_trades.csv")
        self.state_path = self.folder / (name.lower() + "_state.json")
        self._restore_state()

    @staticmethod
    def _number(value, label):
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise StateError(f"{label} must be a finite number")
        value = float(value)
        if not math.isfinite(value):
            raise StateError(f"{label} must be a finite number")
        return value

    def _validate_trade(self, trade, index):
        label = f"closed_trades[{index}]"
        if not isinstance(trade, dict):
            raise StateError(f"{label} must be an object")
        for key in ("utc", "bot", "symbol", "side", "reason"):
            if not isinstance(trade.get(key), str) or not trade[key]:
                raise StateError(f"{label}.{key} must be a non-empty string")
        if trade["bot"] != self.name:
            raise StateError(f"{label}.bot does not match {self.name}")
        if trade["side"] not in ("LONG", "SHORT") or trade["reason"] not in ("SL", "TP"):
            raise StateError(f"{label} has an invalid side or exit reason")
        entry = self._number(trade.get("entry"), f"{label}.entry")
        exit_price = self._number(trade.get("exit"), f"{label}.exit")
        net_pnl = self._number(trade.get("net_pnl_usdt"), f"{label}.net_pnl_usdt")
        if entry <= 0 or exit_price <= 0:
            raise StateError(f"{label} prices must be positive")
        return {
            "utc": trade["utc"], "bot": trade["bot"], "symbol": trade["symbol"],
            "side": trade["side"], "entry": entry, "exit": exit_price,
            "reason": trade["reason"], "net_pnl_usdt": net_pnl,
        }

    def _validate_state(self, state):
        if not isinstance(state, dict):
            raise StateError("state root must be an object")
        version = state.get("version")
        if isinstance(version, bool) or not isinstance(version, int) or version != 1:
            raise StateError(f"unsupported state version: {version!r}")
        if state.get("name") != self.name or state.get("symbol") != self.symbol:
            raise StateError(
                f"saved state is for {state.get('name')!r}/{state.get('symbol')!r}, "
                f"not {self.name!r}/{self.symbol!r}"
            )

        bar = state.get("bar")
        last_exit_bar = state.get("last_exit_bar")
        last_stamp = state.get("last_stamp")
        if isinstance(bar, bool) or not isinstance(bar, int) or bar < 0:
            raise StateError("bar must be a non-negative integer")
        if isinstance(last_exit_bar, bool) or not isinstance(last_exit_bar, int):
            raise StateError("last_exit_bar must be an integer")
        if last_exit_bar > bar:
            raise StateError("last_exit_bar cannot be greater than bar")
        if last_stamp is not None and (
                isinstance(last_stamp, bool) or not isinstance(last_stamp, int) or last_stamp < 0):
            raise StateError("last_stamp must be null or a non-negative integer")

        raw_position = state.get("position")
        position = None
        if raw_position is not None:
            if not isinstance(raw_position, dict):
                raise StateError("position must be null or an object")
            side = raw_position.get("side")
            entry = self._number(raw_position.get("entry"), "position.entry")
            stop = self._number(raw_position.get("stop"), "position.stop")
            take = self._number(raw_position.get("take"), "position.take")
            if entry <= 0 or side not in ("LONG", "SHORT"):
                raise StateError("position has an invalid side or entry price")
            if (side == "LONG" and not stop < entry < take) or (
                    side == "SHORT" and not take < entry < stop):
                raise StateError("position stop and take do not match its side")
            position = {"side": side, "entry": entry, "stop": stop, "take": take}
        if position is not None and last_stamp is None:
            raise StateError("an open position must have a last processed candle")

        raw_trades = state.get("closed_trades")
        if not isinstance(raw_trades, list):
            raise StateError("closed_trades must be a list")
        trades = [self._validate_trade(trade, index) for index, trade in enumerate(raw_trades)]
        pnl = self._number(state.get("pnl"), "pnl")
        trade_total = sum(trade["net_pnl_usdt"] for trade in trades)
        if not math.isclose(pnl, trade_total, rel_tol=1e-9, abs_tol=1e-6):
            raise StateError("pnl does not match the saved closed trades")
        self.position = position
        self.last_exit_bar = last_exit_bar
        self.bar = bar
        self.pnl = pnl
        self.last_stamp = last_stamp
        self.closed_trades = trades

    def _atomic_write(self, path, write_contents, newline=None):
        descriptor, temporary = tempfile.mkstemp(
            prefix=f".{path.name}.", suffix=".tmp", dir=self.folder
        )
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8", newline=newline) as file:
                write_contents(file)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
            try:
                directory_fd = os.open(self.folder, os.O_RDONLY)
                try:
                    os.fsync(directory_fd)
                finally:
                    os.close(directory_fd)
            except OSError:
                pass
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)

    def _save_state(self):
        state = {
            "version": 1,
            "name": self.name,
            "symbol": self.symbol,
            "position": self.position,
            "last_exit_bar": self.last_exit_bar,
            "bar": self.bar,
            "pnl": self.pnl,
            "last_stamp": self.last_stamp,
            "closed_trades": self.closed_trades,
        }
        self._atomic_write(
            self.state_path,
            lambda file: json.dump(state, file, indent=2, allow_nan=False),
        )

    def _write_trade_csv(self):
        def write(file):
            writer = csv.writer(file)
            writer.writerow(["utc", "bot", "symbol", "side", "entry", "exit", "reason", "net_pnl_usdt"])
            for trade in self.closed_trades:
                writer.writerow([
                    trade["utc"], trade["bot"], trade["symbol"], trade["side"],
                    trade["entry"], trade["exit"], trade["reason"],
                    round(trade["net_pnl_usdt"], 4),
                ])

        self._atomic_write(self.path, write, newline="")

    def _import_legacy_csv(self):
        if not self.path.exists() or self.path.stat().st_size == 0:
            return []
        try:
            with self.path.open(newline="", encoding="utf-8") as file:
                reader = csv.DictReader(file)
                required = {"utc", "bot", "symbol", "side", "entry", "exit", "reason", "net_pnl_usdt"}
                if not required.issubset(reader.fieldnames or []):
                    raise StateError(f"legacy trade log has an unexpected header: {self.path}")
                trades = []
                for index, row in enumerate(reader):
                    try:
                        row["entry"] = float(row["entry"])
                        row["exit"] = float(row["exit"])
                        row["net_pnl_usdt"] = float(row["net_pnl_usdt"])
                    except (TypeError, ValueError) as error:
                        raise StateError(f"invalid legacy trade row {index + 2} in {self.path}") from error
                    trades.append(self._validate_trade(row, index))
                return trades
        except OSError as error:
            raise StateError(f"cannot read legacy trade log {self.path}: {error}") from error

    def _restore_state(self):
        if self.state_path.exists():
            try:
                with self.state_path.open(encoding="utf-8") as file:
                    state = json.load(file)
                self._validate_state(state)
            except StateError as error:
                raise StateError(f"Invalid paper state {self.state_path}: {error}") from error
            except (OSError, json.JSONDecodeError) as error:
                raise StateError(f"Cannot read paper state {self.state_path}: {error}") from error
            if self.closed_trades:
                self._write_trade_csv()
            return

        # Preserve trade history written by earlier versions before state files existed.
        self.closed_trades = self._import_legacy_csv()
        self.pnl = sum(trade["net_pnl_usdt"] for trade in self.closed_trades)
        self._save_state()

    def _persist(self, write_trades=False):
        self._save_state()
        if write_trades:
            self._write_trade_csv()

    def update(self, candles):
        # Exclude still-forming candle; signals use completed candles only.
        closed = candles[:-1]
        if not closed:
            return

        if self.last_stamp is None:
            # Keep first-run behavior: start from the latest closed candle only.
            if len(closed) < 35:
                return
            indexes = [len(closed) - 1]
        else:
            if self.position and closed[0][0] > self.last_stamp + 60_000:
                raise StateError(
                    f"saved open position is older than BingX's returned candle history "
                    f"(last saved={self.last_stamp}, earliest returned={closed[0][0]}); "
                    "cannot safely replay its stop or take"
                )
            # Replay every candle missed while stopped, so a saved position can
            # still hit its simulated stop or take profit during the outage.
            indexes = [i for i, candle in enumerate(closed) if candle[0] > self.last_stamp]
        if not indexes:
            return
        for index in indexes:
            if self.position is None and index < 34:
                self.last_stamp = closed[index][0]
                self.bar += 1
                self._persist()
                continue

            candle = closed[index]
            self.last_stamp = candle[0]
            self.bar += 1
            closes = [float(c[4]) for c in closed[:index + 1]]
            price = closes[-1]

            if self.position:
                p = self.position
                # Conservative intrabar assumption: stop wins when both touched.
                high, low = float(candle[2]), float(candle[3])
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
                    self.closed_trades.append({
                        "utc": datetime.now(timezone.utc).isoformat(),
                        "bot": self.name,
                        "symbol": self.symbol,
                        "side": p["side"],
                        "entry": p["entry"],
                        "exit": out,
                        "reason": "SL" if stop_hit else "TP",
                        "net_pnl_usdt": net,
                    })
                    print(f"{self.name}: CLOSE {p['side']} net={net:+.3f} USDT total={self.pnl:+.3f}", flush=True)
                    self.position = None
                    self.last_exit_bar = self.bar
                    self._persist(write_trades=True)
                    continue
                self._persist()
                continue

            if self.bar - self.last_exit_bar >= COOLDOWN_BARS:
                fast, slow = ema_series(closes, 9), ema_series(closes, 21)
                side = "LONG" if fast[-2] <= slow[-2] and fast[-1] > slow[-1] else (
                    "SHORT" if fast[-2] >= slow[-2] and fast[-1] < slow[-1] else None)
                if side:
                    self.position = {
                        "side": side, "entry": price,
                        "stop": price * (1 - STOP_PCT if side == "LONG" else 1 + STOP_PCT),
                        "take": price * (1 + TAKE_PCT if side == "LONG" else 1 - TAKE_PCT)
                    }
                    print(f"{self.name}: PAPER OPEN {side} at {price:.5f}", flush=True)
            self._persist()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--btc-symbol", default=os.getenv("BTC_SYMBOL", DEFAULTS["BTC"]))
    parser.add_argument("--gold-symbol", default=os.getenv("GOLD_SYMBOL", DEFAULTS["GOLD"]))
    parser.add_argument("--poll", type=int, default=15)
    parser.add_argument("--once", action="store_true", help="Check market data once, then exit")
    args = parser.parse_args()
    if args.poll < 5:
        parser.error("--poll must be at least 5 seconds")
    exchange = ccxt.bingx({"enableRateLimit": True, "timeout": 15000, "options": {"defaultType": "swap"}})
    while True:
        try:
            markets = exchange.load_markets()
            break
        except ccxt.BaseError as error:
            if args.once:
                raise SystemExit(f"Market loading failed: {error}")
            print(f"Market loading failed: {error}; retrying in 30 seconds", flush=True)
            time.sleep(30)
    symbols = {"BTC": args.btc_symbol, "GOLD": args.gold_symbol}
    folder = Path("logs")
    folder.mkdir(parents=True, exist_ok=True)
    bots = []
    for name, symbol in symbols.items():
        if symbol not in markets:
            print(f"{name}: unavailable symbol {symbol}; other bot can still run", flush=True)
            continue
        bots.append(PaperBot(name, symbol, folder))
        print(f"{name}: READY {symbol}", flush=True)
    if not bots:
        raise SystemExit("Neither symbol is available on BingX")
    print("PAPER ONLY: BTC and GOLD. No API keys, no real orders. Ctrl+C stops.", flush=True)
    disabled_bots = set()
    while True:
        failures = []
        for bot in bots:
            if bot.name in disabled_bots:
                continue
            try:
                candles = exchange.fetch_ohlcv(bot.symbol, "1m", limit=80)
                bot.update(candles)
                if args.once:
                    print(f"{bot.name}: data OK, {len(candles)} candles", flush=True)
            except ccxt.BaseError as error:
                print(f"{bot.name}: market data error: {error}", flush=True)
                failures.append(f"{bot.name}: {error}")
            except StateError as error:
                message = f"{bot.name}: paper state recovery stopped: {error}"
                print(message, flush=True)
                failures.append(message)
                disabled_bots.add(bot.name)
        if args.once:
            if failures:
                raise SystemExit("Market data check failed: " + "; ".join(failures))
            break
        time.sleep(args.poll)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped. No exchange orders were placed.")
