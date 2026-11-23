"""Regression tests for optional LONG entry filter; no live orders."""
import random
import unittest
from backtest_gold_profit_auto import backtest

def candles(n=1200):
    rng=random.Random(7)
    price=4000.0
    result=[]
    for i in range(n):
        o=price
        c=o*(1+rng.gauss(0,0.001))
        result.append(dict(time=1700000000000+i*60000,open=o,high=max(o,c)*1.0004,low=min(o,c)*0.9996,close=c))
        price=c
    return result

class LongFilterRegression(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bars=candles()

    def run_bt(self, **kwargs):
        return backtest(self.bars, return_trades=True, **kwargs)

    def test_accept_all_is_noop(self):
        self.assertEqual(self.run_bt(), self.run_bt(long_filter=lambda bars,i:True))

    def test_short_only_invariance(self):
        base=self.run_bt(allowed_side="SHORT")
        for fn in (lambda b,i:False,lambda b,i:True,lambda b,i:i%2==0):
            self.assertEqual(base,self.run_bt(allowed_side="SHORT",long_filter=fn))

    def test_reject_all_removes_long(self):
        _,trades=self.run_bt(long_filter=lambda b,i:False)
        self.assertTrue(all(t["side"]=="SHORT" for t in trades))

    def test_no_overlap(self):
        _,trades=self.run_bt(long_filter=lambda b,i:i%2==0)
        for a,b in zip(trades,trades[1:]):
            self.assertLessEqual(a["time"],b["entry_time"])

    def test_closed_trade_accounting(self):
        summary,trades=self.run_bt()
        self.assertAlmostEqual(summary["net_usdt"],sum(t["net"] for t in trades),delta=.02*max(1,len(trades)))
        for t in trades:
            self.assertAlmostEqual(t["net"],t["gross_usdt"]-t["cost_usdt"],delta=.0002)

if __name__=="__main__":
    unittest.main()
