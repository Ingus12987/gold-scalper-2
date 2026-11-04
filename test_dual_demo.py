import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
import ccxt
from dual_demo import PaperBot, StateError, ema_series, main


def candles(prices):
    return [[i * 60000, p, p * 1.001, p * .999, p, 100] for i, p in enumerate(prices)]


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
                exchange.fetch_ohlcv.return_value = candles([100.] * 80)
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
                    return candles([100.] * 80)

                exchange.fetch_ohlcv.side_effect = fetch
                with self.assertRaisesRegex(SystemExit, 'Market data check failed: BTC:'):
                    main()
                self.assertEqual(exchange.fetch_ohlcv.call_count, 2)
                self.assertEqual(exchange.fetch_ohlcv.call_args_list[0].args[0], 'BTC/USDT:USDT')
                self.assertEqual(exchange.fetch_ohlcv.call_args_list[1].args[0], 'XAUT/USDT:USDT')


if __name__ == '__main__':
    unittest.main()
