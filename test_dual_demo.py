import csv
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dual_demo import PaperBot, ema_series, main


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

    def test_short_stop_wins_when_both_levels_touched(self):
        with tempfile.TemporaryDirectory() as tmp:
            bot = PaperBot('GOLD', 'XAUT/USDT:USDT', Path(tmp))
            bot.position = {'side': 'SHORT', 'entry': 100., 'stop': 100.3, 'take': 99.4}
            data = candles([100.] * 40)
            data[-2][2], data[-2][3] = 101., 99.
            bot.update(data)
            self.assertIsNone(bot.position)
            self.assertLess(bot.pnl, 0)


if __name__ == '__main__':
    unittest.main()
