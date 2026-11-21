import unittest
from backtest_gold_profit_auto import backtest, direction
from gold_profit_auto import START_BALANCE

class BacktestTests(unittest.TestCase):
    def test_flat_market_no_trade(self):
        bars = [{"time": 1700000000000 + i*60000,
                 "open": 100, "high": 100, "low": 100, "close": 100}
                for i in range(120)]
        result = backtest(bars)
        self.assertEqual(result["trades"], 0)
        self.assertEqual(result["ending_balance_usdt"], START_BALANCE)

    def test_not_enough_candles_no_signal(self):
        self.assertIsNone(direction([100.0] * 29))

    def test_invalid_bar_rejected(self):
        with self.assertRaises(ValueError):
            backtest([{"time": 1700000000000, "open": 100,
                       "high": 90, "low": 80, "close": 100}])

    def test_flat_market_equity_unchanged(self):
        bars = [{"time": 1700000000000 + i * 60000,
                 "open": 100, "high": 100, "low": 100, "close": 100}
                for i in range(150)]
        result = backtest(bars)
        self.assertFalse(result["open_position_at_end"])
        self.assertEqual(result["unrealized_pnl_usdt"], 0)
        self.assertEqual(result["equity_including_open_usdt"], START_BALANCE)

    def test_negative_price_rejected(self):
        with self.assertRaises(ValueError):
            backtest([{"time": 1700000000000, "open": -1,
                       "high": 2, "low": -2, "close": 1}])

if __name__ == "__main__":
    unittest.main()
