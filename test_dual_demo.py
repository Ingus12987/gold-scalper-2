import csv
import json
import tempfile
import time
import urllib.request
import unittest
from pathlib import Path
from unittest.mock import patch
import ccxt
from dual_demo import (
    DashboardState,
    ExponentialBackoff,
    MarketDataError,
    PaperBot,
    StateError,
    ema_series,
    is_retryable_market_error,
    latest_closed_candle,
    main,
    start_dashboard,
)


def candles(prices):
    return [[i * 60000, p, p * 1.001, p * .999, p, 100] for i, p in enumerate(prices)]


def fresh_candles(count=80):
    now_ms = int(time.time() * 1000)
    current_minute = now_ms // 60000 * 60000
    first = current_minute - (count - 1) * 60000
    return [
        [first + i * 60000, 100., 100.1, 99.9, 100., 100.]
        for i in range(count)
    ]


class PaperBotTests(unittest.TestCase):
    def test_ema(self):
        self.assertEqual(ema_series([10, 10, 10], 9), [10., 10., 10.])

    def test_startup_creates_log_folder_and_checks_available_market(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / "logs"
            with patch("sys.argv", ["dual_demo.py", "--once"]), \
                    patch("dual_demo.Path", return_value=folder), \
                    patch("dual_demo.ccxt.bingx") as exchange_factory:
                exchange = exchange_factory.return_value
                exchange.load_markets.return_value = {
                    "BTC/USDT:USDT": {}, "XAUT/USDT:USDT": {}}
                exchange.fetch_ohlcv.return_value = fresh_candles()
                main()
                self.assertTrue(folder.is_dir())
                self.assertEqual(exchange.fetch_ohlcv.call_args_list, [
                    unittest.mock.call("BTC/USDT:USDT", "1m", limit=80),
                    unittest.mock.call("XAUT/USDT:USDT", "1m", limit=80),
                ])

    def test_two_independent_bots_and_take_profit(self):
        with tempfile.TemporaryDirectory() as tmp:
            btc = PaperBot('BTC', 'BTC/USDT:USDT', Path(tmp))
            gold = PaperBot('GOLD', 'XAUT/USDT:USDT', Path(tmp))
            series = [100.] * 40 + [110., 110.]
            btc.update(candles(series))
            gold.update(candles(series))
            self.assertEqual(btc.position['side'], 'LONG')
            self.assertEqual(gold.position['side'], 'LONG')
            btc.update(candles(series))
            self.assertEqual(btc.bar, 1)
            btc.update(candles(series[:-1] + [111., 111.]))
            self.assertIsNone(btc.position)
            self.assertIsNotNone(gold.position)
            with (Path(tmp) / 'btc_trades.csv').open() as file:
                self.assertEqual(list(csv.DictReader(file))[0]['reason'], 'TP')
            restored_btc = PaperBot('BTC', 'BTC/USDT:USDT', Path(tmp))
            self.assertEqual(restored_btc.pnl, btc.pnl)
            self.assertEqual(len(restored_btc.closed_trades), 1)
            self.assertIsNone(restored_btc.position)

    def test_short_stop_wins_when_both_levels_touched(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = PaperBot('GOLD', 'XAUT/USDT:USDT', Path(tmp))
            bot.position = {'side': 'SHORT', 'entry': 100., 'stop': 100.3, 'take': 99.4}
            data = candles([100.] * 40)
            data[-2][2], data[-2][3] = 101., 99.
            bot.update(data)
            self.assertIsNone(bot.position)
            self.assertLess(bot.pnl, 0)

    def test_restart_restores_positions_trades_pnl_and_last_candle(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            bot = PaperBot('BTC', 'BTC/USDT:USDT', folder)
            history = candles([100.] * 42)
            bot.position = {'side': 'LONG', 'entry': 100., 'stop': 99.7, 'take': 100.6}
            bot.last_exit_bar = 3
            bot.bar = 5
            bot.pnl = 0.75
            bot.last_stamp = history[-2][0]
            bot.closed_trades = [{
                'utc': '2026-11-04T10:00:00+00:00',
                'bot': 'BTC',
                'symbol': 'BTC/USDT:USDT',
                'side': 'SHORT',
                'entry': 101.,
                'exit': 100.,
                'reason': 'TP',
                'net_pnl_usdt': 0.75,
            }]
            bot._persist(write_trades=True)

            restored = PaperBot('BTC', 'BTC/USDT:USDT', folder)
            self.assertEqual(restored.position, bot.position)
            self.assertEqual(restored.closed_trades, bot.closed_trades)
            self.assertEqual(restored.pnl, 0.75)
            self.assertEqual(restored.last_stamp, history[-2][0])
            self.assertEqual(restored.bar, 5)

            restored.update(history)
            self.assertEqual(restored.bar, 5)
            self.assertEqual(restored.position, bot.position)
            with (folder / 'btc_trades.csv').open() as file:
                self.assertEqual(len(list(csv.DictReader(file))), 1)

    def test_restart_replays_unprocessed_candles_for_an_open_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            bot = PaperBot('GOLD', 'XAUT/USDT:USDT', folder)
            history = candles([100.] * 45)
            closed = history[:-1]
            bot.position = {'side': 'LONG', 'entry': 100., 'stop': 99.7, 'take': 100.6}
            bot.bar = 10
            bot.last_exit_bar = 2
            bot.last_stamp = closed[39][0]
            closed[40][2] = 101.
            bot._persist()

            restored = PaperBot('GOLD', 'XAUT/USDT:USDT', folder)
            restored.update(history)
            self.assertIsNone(restored.position)
            self.assertEqual(len(restored.closed_trades), 1)
            self.assertEqual(restored.closed_trades[0]['reason'], 'TP')
            self.assertGreater(restored.pnl, 0)
            self.assertEqual(restored.last_stamp, closed[-1][0])

    def test_restart_refuses_to_guess_when_open_position_is_outside_candle_history(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            bot = PaperBot('GOLD', 'XAUT/USDT:USDT', folder)
            bot.position = {'side': 'LONG', 'entry': 100., 'stop': 99.7, 'take': 100.6}
            bot.bar = 4
            bot.last_stamp = 60_000
            bot._persist()

            restored = PaperBot('GOLD', 'XAUT/USDT:USDT', folder)
            history = candles([100.] * 80)
            for candle in history:
                candle[0] += 10 * 60_000
            with self.assertRaisesRegex(StateError, 'cannot safely replay its stop or take'):
                restored.update(history)
            self.assertEqual(restored.last_stamp, 60_000)
            self.assertIsNotNone(restored.position)

    def test_corrupt_state_fails_closed_without_overwriting_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            PaperBot('GOLD', 'XAUT/USDT:USDT', folder)
            state_path = folder / 'gold_state.json'
            state_path.write_text('{"version":', encoding='utf-8')
            with self.assertRaisesRegex(StateError, 'Cannot read paper state'):
                PaperBot('GOLD', 'XAUT/USDT:USDT', folder)
            self.assertEqual(state_path.read_text(encoding='utf-8'), '{"version":')

    def test_existing_csv_trade_history_is_imported_on_first_state_creation(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            log_path = folder / 'gold_trades.csv'
            with log_path.open('w', newline='', encoding='utf-8') as file:
                writer = csv.writer(file)
                writer.writerow(['utc', 'bot', 'symbol', 'side', 'entry', 'exit', 'reason', 'net_pnl_usdt'])
                writer.writerow(['2026-11-04T10:00:00+00:00', 'GOLD', 'XAUT/USDT:USDT',
                                 'LONG', 100, 101, 'TP', 0.42])
            bot = PaperBot('GOLD', 'XAUT/USDT:USDT', folder)
            self.assertEqual(bot.pnl, 0.42)
            self.assertEqual(len(bot.closed_trades), 1)
            self.assertTrue((folder / 'gold_state.json').exists())

    def test_once_reports_market_loading_api_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'logs'
            with patch('sys.argv', ['dual_demo.py', '--once']), \
                    patch('dual_demo.Path', return_value=folder), \
                    patch('dual_demo.ccxt.bingx') as exchange_factory:
                exchange_factory.return_value.load_markets.side_effect = ccxt.OperationFailed(
                    'temporary BingX error')
                with self.assertRaisesRegex(SystemExit, 'Market loading failed: temporary BingX error'):
                    main()

    def test_market_loading_100410_retries_with_backoff_then_starts_both_bots(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'logs'
            clock = [0.]
            waits = []
            markets = {'BTC/USDT:USDT': {}, 'XAUT/USDT:USDT': {}}

            def sleep(seconds):
                waits.append(seconds)
                clock[0] += seconds
                if len(waits) >= 2:
                    raise KeyboardInterrupt

            with patch('sys.argv', ['dual_demo.py', '--poll', '60']), \
                    patch('dual_demo.Path', return_value=folder), \
                    patch('dual_demo.ccxt.bingx') as exchange_factory, \
                    patch('dual_demo.time.monotonic', side_effect=lambda: clock[0]), \
                    patch('dual_demo.time.sleep', side_effect=sleep), \
                    patch('dual_demo.start_dashboard'):
                exchange = exchange_factory.return_value
                exchange.load_markets.side_effect = [
                    ccxt.OperationFailed('BingX code 100410: try again later'),
                    markets,
                ]
                exchange.fetch_ohlcv.return_value = fresh_candles()
                with self.assertRaises(KeyboardInterrupt):
                    main()
                self.assertEqual(exchange.load_markets.call_count, 2)
                self.assertEqual(exchange.fetch_ohlcv.call_count, 2)
            self.assertEqual(waits, [60., 60.])

    def test_once_attempts_both_symbols_and_reports_individual_candle_failure(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'logs'
            with patch('sys.argv', ['dual_demo.py', '--once']), \
                    patch('dual_demo.Path', return_value=folder), \
                    patch('dual_demo.ccxt.bingx') as exchange_factory:
                exchange = exchange_factory.return_value
                exchange.load_markets.return_value = {
                    'BTC/USDT:USDT': {}, 'XAUT/USDT:USDT': {}}

                def fetch(symbol, timeframe, limit):
                    if symbol == 'BTC/USDT:USDT':
                        raise ccxt.OperationFailed('BingX temporarily unavailable')
                    return fresh_candles()

                exchange.fetch_ohlcv.side_effect = fetch
                with self.assertRaisesRegex(SystemExit, 'Market data check failed: BTC:'):
                    main()
                self.assertEqual(exchange.fetch_ohlcv.call_count, 2)
                self.assertEqual(exchange.fetch_ohlcv.call_args_list[0].args[0], 'BTC/USDT:USDT')
                self.assertEqual(exchange.fetch_ohlcv.call_args_list[1].args[0], 'XAUT/USDT:USDT')

    def test_backoff_doubles_caps_and_resets(self):
        backoff = ExponentialBackoff(initial_delay=5, max_delay=20)
        self.assertEqual(backoff.fail(100), 5)
        self.assertTrue(backoff.is_waiting(104.9))
        self.assertFalse(backoff.is_waiting(105))
        self.assertEqual(backoff.fail(105), 10)
        self.assertEqual(backoff.fail(115), 20)
        self.assertEqual(backoff.fail(135), 20)
        backoff.reset()
        self.assertFalse(backoff.is_waiting(135))
        self.assertEqual(backoff.fail(135), 5)

    def test_bingx_100410_and_rate_limit_errors_are_retryable(self):
        self.assertTrue(is_retryable_market_error(
            ccxt.OperationFailed('bingx {"code":100410,"msg":"try again later"}')))
        self.assertTrue(is_retryable_market_error(ccxt.RateLimitExceeded('HTTP 429')))
        self.assertFalse(is_retryable_market_error(ccxt.BadSymbol('invalid symbol')))

    def test_latest_closed_candle_must_be_fresh_and_one_minute_aligned(self):
        rows = [
            [420_000, 1., 1., 1., 1., 1.],
            [480_000, 1., 1., 1., 1., 1.],
            [540_000, 1., 1., 1., 1., 1.],
            [600_000, 1., 1., 1., 1., 1.],
        ]
        self.assertEqual(latest_closed_candle(rows, now_ms=600_000), rows[2])
        with self.assertRaisesRegex(MarketDataError, 'stale'):
            latest_closed_candle(rows[:2], now_ms=1_000_000)

    def test_xaut_backoff_does_not_pause_btc_and_recovers(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp) / 'logs'
            clock = [0.]
            btc_attempts = []
            xaut_attempts = []

            def fetch(symbol, timeframe, limit):
                if symbol == 'BTC/USDT:USDT':
                    btc_attempts.append(clock[0])
                    return fresh_candles()
                xaut_attempts.append(clock[0])
                if len(xaut_attempts) <= 2:
                    raise ccxt.OperationFailed(
                        'bingx {"code":100410,"msg":"please try again later"}')
                return fresh_candles()

            def sleep(seconds):
                clock[0] += seconds
                if clock[0] >= 30:
                    raise KeyboardInterrupt

            with patch('sys.argv', ['dual_demo.py', '--poll', '60']), \
                    patch('dual_demo.Path', return_value=folder), \
                    patch('dual_demo.ccxt.bingx') as exchange_factory, \
                    patch('dual_demo.time.monotonic', side_effect=lambda: clock[0]), \
                    patch('dual_demo.time.sleep', side_effect=sleep), \
                    patch('dual_demo.start_dashboard'):
                exchange = exchange_factory.return_value
                exchange.load_markets.return_value = {
                    'BTC/USDT:USDT': {}, 'XAUT/USDT:USDT': {}}
                exchange.fetch_ohlcv.side_effect = fetch
                with self.assertRaises(KeyboardInterrupt):
                    main()

            self.assertGreaterEqual(len(btc_attempts), 4)
            self.assertEqual(len(xaut_attempts), 3)
            self.assertEqual(xaut_attempts, [0., 60., 180.])

    def test_dashboard_state_shows_pnl_only_with_a_fresh_mark(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = PaperBot('BTC', 'BTC/USDT:USDT', Path(tmp))
            now_ms = int(time.time() * 1000)
            bot.position = {
                'side': 'LONG', 'entry': 100., 'stop': 99.7, 'take': 100.6,
            }
            bot.last_price = 101.
            bot.last_mark_stamp = (now_ms // 60_000) * 60_000 - 60_000
            bot.pnl = 0.25
            bot._persist()
            state = DashboardState([bot])
            state.update_status(
                'BTC', 'fresh',
                last_market_data_at='2026-11-04T10:00:00+00:00',
            )
            snapshot = state.snapshot(now_ms)['bots'][0]
            self.assertEqual(snapshot['status'], 'fresh')
            self.assertGreater(snapshot['unrealized_pnl_usdt'], 0)
            self.assertAlmostEqual(
                snapshot['cumulative_pnl_usdt'],
                snapshot['realized_pnl_usdt'] + snapshot['unrealized_pnl_usdt'],
            )
            stale = state.snapshot(now_ms + 240_000)['bots'][0]
            self.assertEqual(stale['status'], 'stale')
            self.assertIsNone(stale['mark_price'])
            self.assertIsNone(stale['unrealized_pnl_usdt'])
            self.assertIsNone(stale['cumulative_pnl_usdt'])

    def test_dashboard_http_api_is_read_only_and_returns_saved_state(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = PaperBot('GOLD', 'XAUT/USDT:USDT', Path(tmp))
            bot.position = {
                'side': 'SHORT', 'entry': 100., 'stop': 100.3, 'take': 99.4,
            }
            bot.last_price = 100.
            bot.last_mark_stamp = (int(time.time() * 1000) // 60_000) * 60_000 - 60_000
            bot._persist()
            state = DashboardState([bot])
            server = start_dashboard(state, port=0)
            try:
                base = f'http://127.0.0.1:{server.server_port}'
                with urllib.request.urlopen(base + '/api/state', timeout=2) as response:
                    payload = json.loads(response.read())
                    self.assertEqual(payload['mode'], 'paper-only')
                    self.assertEqual(payload['bots'][0]['symbol'], 'XAUT/USDT:USDT')
                    self.assertEqual(payload['bots'][0]['open_position']['side'], 'SHORT')
                with urllib.request.urlopen(base + '/', timeout=2) as response:
                    self.assertEqual(response.status, 200)
                    self.assertIn(b'<html', response.read().lower())
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
