"""Offline tests: no exchange access and no real orders."""
import unittest
from gold_profit_auto import (
    initial_state, step, net_pnl, notional, daily_loss,
    START_BALANCE, COOLDOWN_SECONDS, DAILY_LOSS_LIMIT,
)

class GoldProfitAutoTests(unittest.TestCase):
    def test_initial_demo_mode(self):
        state = initial_state()
        self.assertEqual(state["mode"], "PAPER_DEMO")
        self.assertEqual(state["balance"], START_BALANCE)
        self.assertIsNone(state["position"])

    def test_open_and_take_profit(self):
        state = initial_state()
        step(state, 100, 100, "LONG")
        self.assertIsNotNone(state["position"])
        step(state, 102, 101)
        self.assertIsNone(state["position"])
        self.assertEqual(state["trades"][0]["reason"], "TP")
        self.assertGreater(state["balance"], START_BALANCE)

    def test_stop_loss(self):
        state = initial_state()
        step(state, 100, 100, "SHORT")
        step(state, 102, 101)
        self.assertIsNone(state["position"])
        self.assertEqual(state["trades"][0]["reason"], "SL")
        self.assertLess(state["balance"], START_BALANCE)

    def test_no_second_position(self):
        state = initial_state()
        step(state, 100, 100, "LONG")
        step(state, 100, 101, "SHORT")
        self.assertEqual(state["position"]["side"], "LONG")

    def test_cooldown(self):
        state = initial_state()
        step(state, 100, 100, "LONG")
        step(state, 102, 101)
        step(state, 100, 101 + COOLDOWN_SECONDS - 1, "SHORT")
        self.assertIsNone(state["position"])
        step(state, 100, 101 + COOLDOWN_SECONDS, "SHORT")
        self.assertEqual(state["position"]["side"], "SHORT")

    def test_size_only_increases_with_equity(self):
        self.assertEqual(notional(START_BALANCE), notional(START_BALANCE - 500))
        self.assertGreater(notional(START_BALANCE + 300), notional(START_BALANCE))
        self.assertLessEqual(notional(START_BALANCE + 100000), 600)

    def test_daily_loss_block(self):
        from datetime import datetime, timezone
        state = initial_state()
        state["trades"] = [{
            "date": datetime.now(timezone.utc).isoformat(),
            "net": -DAILY_LOSS_LIMIT,
        }]
        self.assertGreaterEqual(daily_loss(state), DAILY_LOSS_LIMIT)
        step(state, 100, 100, "LONG")
        self.assertIsNone(state["position"])

    def test_fees_included(self):
        position = {"side": "LONG", "entry": 100, "qty": 2}
        self.assertLess(net_pnl(position, 100), 0)

if __name__ == "__main__":
    unittest.main()
