"""Two independent PAPER trading strategies using public BingX market data.
No exchange orders are ever submitted.
"""
import argparse
import csv
import json
import math
import os
import tempfile
import threading
import time
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

import ccxt

DEFAULTS = {"BTC": "BTC/USDT:USDT", "GOLD": "XAUT/USDT:USDT"}
MARGIN = 20.0
LEVERAGE = 20.0
STOP_PCT = 0.003
TAKE_PCT = 0.006
FEE_RATE = 0.0005  # illustrative taker fee per side; verify current fees
COOLDOWN_BARS = 3
MARKET_DATA_MAX_AGE_MS = 180_000
MAX_BACKOFF_SECONDS = 300.0
DEFAULT_POLL_SECONDS = 60
DASHBOARD_PORT = int(os.getenv("PORT", "5000"))


class StateError(RuntimeError):
    """Raised when saved paper-trading state cannot be used safely."""


class MarketDataError(ccxt.NetworkError):
    """Raised when BingX returns missing, malformed, or stale candles."""


class ExponentialBackoff:
    def __init__(self, initial_delay, max_delay=MAX_BACKOFF_SECONDS):
        self.max_delay = float(max_delay)
        self.initial_delay = min(float(initial_delay), self.max_delay)
        self.current_delay = self.initial_delay
        self.retry_at = 0.0

    def is_waiting(self, now):
        return now < self.retry_at

    def fail(self, now):
        delay = self.current_delay
        self.retry_at = now + delay
        self.current_delay = min(self.current_delay * 2, self.max_delay)
        return delay

    def reset(self):
        self.current_delay = self.initial_delay
        self.retry_at = 0.0


def is_retryable_market_error(error):
    if isinstance(error, (
            ccxt.NetworkError, ccxt.RateLimitExceeded, ccxt.DDoSProtection)):
        return True
    message = str(error).lower()
    return any(marker in message for marker in (
        "100410", "429", "rate limit", "too many request",
        "try again later", "temporarily unavailable",
    ))


def latest_closed_candle(candles, now_ms=None):
    """Return the newest fresh, completed 1-minute candle or raise for retry."""
    if not isinstance(candles, (list, tuple)) or len(candles) < 2:
        raise MarketDataError("BingX returned fewer than two candles")

    timestamps = []
    for index, candle in enumerate(candles):
        if not isinstance(candle, (list, tuple)) or len(candle) < 5:
            raise MarketDataError(f"BingX returned a malformed candle at index {index}")
        try:
            stamp = int(candle[0])
        except (TypeError, ValueError, OverflowError) as error:
            raise MarketDataError(f"BingX returned an invalid candle timestamp at index {index}") from error
        if stamp % 60_000:
            raise MarketDataError(f"BingX returned a non-minute candle timestamp: {stamp}")
        try:
            opened, high, low, close = (float(candle[index]) for index in (1, 2, 3, 4))
        except (TypeError, ValueError, OverflowError) as error:
            raise MarketDataError(f"BingX returned invalid OHLC data at index {index}") from error
        if (not all(math.isfinite(value) and value > 0 for value in (opened, high, low, close))
                or high < max(opened, close) or low > min(opened, close) or high < low):
            raise MarketDataError(f"BingX returned inconsistent OHLC data at index {index}")
        timestamps.append(stamp)
    if any(current <= previous for previous, current in zip(timestamps, timestamps[1:])):
        raise MarketDataError("BingX candles are not in strictly increasing time order")

    now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
    completed = [
        candle for candle, stamp in zip(candles, timestamps)
        if stamp + 60_000 <= now_ms
    ]
    if not completed:
        raise MarketDataError("BingX returned no completed 1-minute candle")

    newest = completed[-1]
    age_ms = now_ms - (int(newest[0]) + 60_000)
    if age_ms > MARKET_DATA_MAX_AGE_MS:
        raise MarketDataError(
            f"newest completed 1-minute candle is stale by {age_ms // 1000}s"
        )
    return newest


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
        self.last_price = None
        self.last_mark_stamp = None
        self.closed_trades = []
        self.state_lock = threading.RLock()
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
        last_price = state.get("last_price")
        last_mark_stamp = state.get("last_mark_stamp")
        if last_price is None and last_mark_stamp is None:
            pass
        elif last_price is None or last_mark_stamp is None:
            raise StateError("last_price and last_mark_stamp must both be present or null")
        else:
            last_price = self._number(last_price, "last_price")
            if last_price <= 0:
                raise StateError("last_price must be positive")
            if (isinstance(last_mark_stamp, bool) or not isinstance(last_mark_stamp, int)
                    or last_mark_stamp < 0):
                raise StateError("last_mark_stamp must be a non-negative integer")

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
        self.last_price = last_price
        self.last_mark_stamp = last_mark_stamp
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
            "last_price": self.last_price,
            "last_mark_stamp": self.last_mark_stamp,
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

    def update(self, candles, newest_closed=None):
        with self.state_lock:
            return self._update(candles, newest_closed)

    def _update(self, candles, newest_closed=None):
        # Main passes the newest completed candle explicitly, so also support
        # exchange responses that omit the still-forming candle.
        if newest_closed is None:
            closed = candles[:-1]
        else:
            closed = [candle for candle in candles if candle[0] <= newest_closed[0]]
        if not closed:
            return
        if newest_closed is None:
            newest_closed = closed[-1]

        if self.last_stamp is None:
            # Keep first-run behavior: start from the latest closed candle only.
            if len(closed) < 35:
                self._set_mark(newest_closed)
                self._persist()
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
            if self._set_mark(newest_closed):
                self._persist()
            return
        for index in indexes:
            candle = closed[index]
            self._set_mark(candle)
            if self.position is None and index < 34:
                self.last_stamp = candle[0]
                self.bar += 1
                self._persist()
                continue

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

    def _set_mark(self, candle):
        price = float(candle[4])
        if not math.isfinite(price) or price <= 0:
            raise MarketDataError("candle close must be a finite positive price")
        changed = self.last_price != price or self.last_mark_stamp != int(candle[0])
        self.last_price = price
        self.last_mark_stamp = int(candle[0])
        return changed

    def dashboard_snapshot(self, now_ms=None):
        with self.state_lock:
            now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
            position = None
            unrealized = 0.0
            mark_fresh = (
                self.last_price is not None
                and self.last_mark_stamp is not None
                and 0 <= now_ms - (self.last_mark_stamp + 60_000) <= MARKET_DATA_MAX_AGE_MS
            )
            if self.position:
                pos = self.position
                mark = self.last_price if mark_fresh else None
                if mark is None:
                    unrealized = None
                else:
                    direction = 1 if pos["side"] == "LONG" else -1
                    notional = MARGIN * LEVERAGE
                    gross = direction * (mark - pos["entry"]) / pos["entry"] * notional
                    estimated_fees = (
                        (pos["entry"] + mark) / pos["entry"] * notional * FEE_RATE
                    )
                    unrealized = gross - estimated_fees
                position = {
                    "side": pos["side"],
                    "entry_price": pos["entry"],
                    "stop_price": pos["stop"],
                    "take_price": pos["take"],
                    "mark_price": self.last_price if mark_fresh else None,
                    "unrealized_pnl_usdt": unrealized,
                }

            cumulative = self.pnl + unrealized if unrealized is not None else None
            return {
                "name": self.name,
                "symbol": self.symbol,
                "open_position": position,
                "closed_trades": [
                    {
                        "closed_at": trade["utc"],
                        "side": trade["side"],
                        "entry_price": trade["entry"],
                        "exit_price": trade["exit"],
                        "reason": trade["reason"],
                        "net_pnl_usdt": trade["net_pnl_usdt"],
                    }
                    for trade in reversed(self.closed_trades[-50:])
                ],
                "realized_pnl_usdt": self.pnl,
                "unrealized_pnl_usdt": unrealized,
                "cumulative_pnl_usdt": cumulative,
                "mark_price": self.last_price if mark_fresh else None,
                "mark_at": self._iso_candle_end(self.last_mark_stamp),
                "last_processed_candle_at": self._iso_candle_end(self.last_stamp),
            }

    @staticmethod
    def _iso_candle_end(stamp):
        if stamp is None:
            return None
        return datetime.fromtimestamp((stamp + 60_000) / 1000, tz=timezone.utc).isoformat()


class DashboardState:
    """Thread-safe, read-only view over persisted paper bot state and feed status."""

    def __init__(self, bots):
        self.bots = {bot.name: bot for bot in bots}
        self.lock = threading.RLock()
        self.statuses = {
            bot.name: {
                "status": "starting",
                "status_message": None,
                "last_market_data_at": None,
                "latest_candle_at": None,
                "retry_at": None,
            }
            for bot in bots
        }

    def update_status(
        self, name, status, message=None, last_market_data_at=None,
        latest_candle_at=None, retry_at=None,
    ):
        with self.lock:
            previous = self.statuses.get(name, {})
            self.statuses[name] = {
                "status": status,
                "status_message": message,
                "last_market_data_at": last_market_data_at or previous.get("last_market_data_at"),
                "latest_candle_at": latest_candle_at or previous.get("latest_candle_at"),
                "retry_at": retry_at,
            }

    def snapshot(self, now_ms=None):
        now_ms = int(time.time() * 1000) if now_ms is None else int(now_ms)
        with self.lock:
            statuses = {name: dict(value) for name, value in self.statuses.items()}

        bots = []
        for name, bot in self.bots.items():
            item = bot.dashboard_snapshot(now_ms)
            status = statuses[name]
            resolved_status = status["status"]
            mark_at = item["mark_at"]
            if mark_at is not None:
                mark_ms = int(datetime.fromisoformat(mark_at).timestamp() * 1000)
                if now_ms - mark_ms > MARKET_DATA_MAX_AGE_MS:
                    resolved_status = "stale"
                    if not status["status_message"]:
                        status["status_message"] = "Last market candle is outside the freshness window."
            elif resolved_status == "fresh":
                resolved_status = "stale"
                status["status_message"] = "No persisted candle price is available."

            bots.append({
                **item,
                "status": resolved_status,
                "status_message": status["status_message"],
                "last_market_data_at": status["last_market_data_at"],
                "latest_candle_at": status["latest_candle_at"] or mark_at,
                "retry_at": status["retry_at"],
            })
        return {
            "mode": "paper-only",
            "updated_at": datetime.fromtimestamp(now_ms / 1000, tz=timezone.utc).isoformat(),
            "bots": bots,
        }


def start_dashboard(dashboard_state, port=DASHBOARD_PORT):
    html_path = Path(__file__).with_name("dashboard-ui") / "index.html"
    if not html_path.is_file():
        raise RuntimeError(f"Dashboard UI file is missing: {html_path}")

    class Handler(BaseHTTPRequestHandler):
        def _respond(self, status, content_type, body):
            self.send_response(status)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.end_headers()
            self.wfile.write(body)

        def do_GET(self):
            route = urlsplit(self.path).path
            if route in ("/", "/dashboard"):
                self._respond(200, "text/html; charset=utf-8", html_path.read_bytes())
            elif route == "/api/state":
                body = json.dumps(
                    dashboard_state.snapshot(), allow_nan=False, separators=(",", ":")
                ).encode("utf-8")
                self._respond(200, "application/json; charset=utf-8", body)
            else:
                self._respond(404, "text/plain; charset=utf-8", b"Not found")

        def log_message(self, format_string, *args):
            # Dashboard refreshes are local reads, not market-data activity.
            return

    server = ThreadingHTTPServer(("0.0.0.0", int(port)), Handler)
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="paper-dashboard", daemon=True)
    thread.start()
    print(f"Read-only paper dashboard listening on port {port}", flush=True)
    return server


def iso_timestamp(timestamp):
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, tz=timezone.utc).isoformat()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--btc-symbol", default=os.getenv("BTC_SYMBOL", DEFAULTS["BTC"]))
    parser.add_argument("--gold-symbol", default=os.getenv("GOLD_SYMBOL", DEFAULTS["GOLD"]))
    parser.add_argument(
        "--poll", type=int, default=DEFAULT_POLL_SECONDS,
        help="minimum normal polling interval in seconds (at least one minute)",
    )
    parser.add_argument("--once", action="store_true", help="Check market data once, then exit")
    args = parser.parse_args()
    if args.poll < 60:
        parser.error("--poll must be at least 60 seconds to avoid polling faster than candles form")
    exchange = ccxt.bingx({"enableRateLimit": True, "timeout": 15000, "options": {"defaultType": "swap"}})
    initial_backoff = min(float(args.poll), MAX_BACKOFF_SECONDS)
    market_list_backoff = ExponentialBackoff(initial_backoff)
    while True:
        try:
            markets = exchange.load_markets()
            break
        except ccxt.BaseError as error:
            if args.once or not is_retryable_market_error(error):
                raise SystemExit(f"Market loading failed: {error}")
            delay = market_list_backoff.fail(time.monotonic())
            print(
                f"Market loading temporarily failed: {error}; "
                f"retrying in {delay:g}s (exponential backoff)",
                flush=True,
            )
            time.sleep(delay)
    symbols = {"BTC": args.btc_symbol, "GOLD": args.gold_symbol}
    folder = Path("logs")
    folder.mkdir(parents=True, exist_ok=True)
    bots = []
    active_bots = []
    for name, symbol in symbols.items():
        bot = PaperBot(name, symbol, folder)
        bots.append(bot)
        if symbol not in markets:
            print(f"{name}: unavailable symbol {symbol}; other bot can still run", flush=True)
            continue
        active_bots.append(bot)
        print(f"{name}: READY {symbol}", flush=True)
    if not active_bots:
        raise SystemExit("Neither symbol is available on BingX")

    dashboard_state = DashboardState(bots)
    for bot in bots:
        if bot not in active_bots:
            dashboard_state.update_status(
                bot.name, "disabled", f"Unavailable BingX symbol: {bot.symbol}"
            )
    print("PAPER ONLY: BTC and GOLD. No API keys, no real orders. Ctrl+C stops.", flush=True)
    disabled_bots = set()
    verified_bots = set()
    backoffs = {
        bot.name: ExponentialBackoff(initial_backoff)
        for bot in active_bots
    }
    next_poll_at = {bot.name: 0.0 for bot in active_bots}
    dashboard_server = None
    if not args.once:
        dashboard_server = start_dashboard(dashboard_state)
    try:
        while True:
            failures = []
            for bot in active_bots:
                if bot.name in disabled_bots:
                    continue
                now_mono = time.monotonic()
                if now_mono < next_poll_at[bot.name]:
                    continue
                backoff = backoffs[bot.name]
                if backoff.is_waiting(now_mono):
                    next_poll_at[bot.name] = backoff.retry_at
                    continue
                try:
                    # Request enough history to replay a persisted paper position.
                    # A short 80-candle window loses stop/take events after downtime.
                    # If the exchange cannot provide the entire gap, keep the
                    # existing safe halt rather than inventing an exit price.
                    since = (
                        max(0, bot.last_stamp - 60_000)
                        if bot.position is not None and bot.last_stamp is not None
                        else None
                    )
                    candles = exchange.fetch_ohlcv(
                        bot.symbol, "1m", since=since,
                        limit=1000 if since is not None else 80,
                    )
                    latest_closed = latest_closed_candle(candles)
                    bot.update(candles, newest_closed=latest_closed)
                    backoff.reset()
                    now_wall = time.time()
                    candle_end = datetime.fromtimestamp(
                        (int(latest_closed[0]) + 60_000) / 1000,
                        tz=timezone.utc,
                    ).isoformat()
                    dashboard_state.update_status(
                        bot.name,
                        "fresh",
                        last_market_data_at=iso_timestamp(now_wall),
                        latest_candle_at=candle_end,
                    )
                    next_poll_at[bot.name] = time.monotonic() + args.poll
                    if bot.name not in verified_bots:
                        print(f"{bot.name}: fresh 1m candles through {candle_end}", flush=True)
                        verified_bots.add(bot.name)
                    if args.once:
                        print(
                            f"{bot.name}: data OK, {len(candles)} candles; "
                            f"latest closed candle {candle_end}",
                            flush=True,
                        )
                except ccxt.BaseError as error:
                    failures.append(f"{bot.name}: {error}")
                    if args.once:
                        print(f"{bot.name}: market data error: {error}", flush=True)
                    elif is_retryable_market_error(error):
                        delay = backoff.fail(time.monotonic())
                        next_poll_at[bot.name] = backoff.retry_at
                        retry_at = iso_timestamp(time.time() + delay)
                        dashboard_state.update_status(
                            bot.name, "retrying", str(error), retry_at=retry_at
                        )
                        print(
                            f"{bot.name}: transient/rate-limit market data error: {error}; "
                            f"retrying in {delay:g}s (independent exponential backoff)",
                            flush=True,
                        )
                    else:
                        message = f"{bot.name}: non-retryable market data error: {error}; disabling this bot"
                        dashboard_state.update_status(bot.name, "disabled", str(error))
                        print(message, flush=True)
                        disabled_bots.add(bot.name)
                except StateError as error:
                    message = f"{bot.name}: paper state recovery stopped: {error}"
                    dashboard_state.update_status(bot.name, "disabled", str(error))
                    print(message, flush=True)
                    failures.append(message)
                    disabled_bots.add(bot.name)

            if args.once:
                if failures:
                    raise SystemExit("Market data check failed: " + "; ".join(failures))
                break

            waiting = [
                max(0.0, next_poll_at[bot.name] - time.monotonic())
                for bot in active_bots
                if bot.name not in disabled_bots
            ]
            time.sleep(min(waiting) if waiting else args.poll)
    finally:
        if dashboard_server is not None:
            dashboard_server.shutdown()
            dashboard_server.server_close()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("Stopped. No exchange orders were placed.")
