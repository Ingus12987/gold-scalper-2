import unittest
from unittest.mock import patch
from backtest_gold_profit_auto import backtest, direction, candle_quality
from gold_profit_auto import START_BALANCE

class BacktestTests(unittest.TestCase):
    def test_trade_cost_accounting_reconciles(self):
        # Synthetic rising market produces entries and exits; verify every trade.
        bars = []
        for i in range(180):
            price = 100 + i * 0.3
            bars.append({"time": 1700000000000 + i * 60000,
                         "open": price, "high": price + 3,
                         "low": price - 0.1, "close": price + 0.2})
        with patch("backtest_gold_profit_auto.direction", return_value="LONG"):
            summary, trades = backtest(bars, return_trades=True, extra_cost_rate=0.0002)
        self.assertGreater(len(trades), 0)
        for trade in trades:
            self.assertAlmostEqual(trade["gross_usdt"] - trade["cost_usdt"], trade["net"], places=3)
            self.assertGreaterEqual(trade["holding_minutes"], 0)
        self.assertAlmostEqual(sum(x["net"] for x in trades), summary["net_usdt"], places=2)

    def test_max_hold_validation(self):
        with self.assertRaises(ValueError):
            backtest([], max_hold_minutes=0)

    def test_time_exit_occurs_without_tp_or_sl(self):
        bars = [{"time": 1700000000000 + i * 60000, "open": 100, "high": 100.1, "low": 99.9, "close": 100} for i in range(40)]
        with patch("backtest_gold_profit_auto.direction", return_value="LONG"):
            _, trades = backtest(bars, return_trades=True, max_hold_minutes=3, take_profit=50, stop_loss=-50)
        self.assertTrue(any(t["reason"] == "TIME" for t in trades))
        self.assertTrue(all(t["holding_minutes"] >= 3 for t in trades if t["reason"] == "TIME"))

    def test_negative_cooldown_rejected(self):
        with self.assertRaises(ValueError):
            backtest([], cooldown_minutes=-1)

    def test_invalid_rsi_band_rejected(self):
        with self.assertRaises(ValueError):
            backtest([], rsi_band=0)
        with self.assertRaises(ValueError):
            backtest([], rsi_band=51)

    def test_negative_volatility_threshold_rejected(self):
        with self.assertRaises(ValueError):
            backtest([], min_range_pct=-0.1)

    def test_side_filter_invalid_rejected(self):
        with self.assertRaises(ValueError):
            backtest([], allowed_side='BOTH')

    def test_side_filter_flat_market(self):
        bars = [{'time': 1700000000000 + i*60000, 'open': 100, 'high': 100, 'low': 100, 'close': 100} for i in range(120)]
        for side in ('LONG', 'SHORT'):
            with self.subTest(side=side):
                self.assertEqual(backtest(bars, allowed_side=side)['trades'], 0)

    def test_missing_candle_quality(self):
        bars = [{"time": 0}, {"time": 60000}, {"time": 180000}]
        self.assertEqual(candle_quality(bars)["missing_minutes"], 1)
        self.assertEqual(candle_quality(bars)["gaps"], 1)

    def test_duplicate_candle_quality_rejected(self):
        with self.assertRaises(ValueError):
            candle_quality([{"time": 0}, {"time": 0}])

    def test_extra_cost_validation(self):
        with self.assertRaises(ValueError):
            backtest([], extra_cost_rate=-0.01)

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

    def test_parameterized_targets_preserve_flat_market(self):
        bars = [{"time": 1700000000000 + i * 60000,
                 "open": 100, "high": 100, "low": 100, "close": 100}
                for i in range(120)]
        for tp, sl in ((1.5, -2.0), (2.0, -2.0), (2.5, -3.0), (3.0, -3.0)):
            with self.subTest(tp=tp, sl=sl):
                result = backtest(bars, take_profit=tp, stop_loss=sl)
                self.assertEqual(result["trades"], 0)
                self.assertEqual(result["ending_balance_usdt"], START_BALANCE)

if __name__ == "__main__":
    unittest.main()
