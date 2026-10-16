"""Focused integration and presentation tests for compact, USDT-first LBank alerts.

Never talks to exchange or Telegram. Does not place orders.
"""
import unittest
from datetime import datetime, timezone
from lbank_compact_alert import format_compact_entry_alert
import lbank_futures_monitor as monitor


class CompactAlertTests(unittest.TestCase):
    def setUp(self):
        self.plan = {
            "side": "롱", "direction": 1, "strategy": "trend_retest",
            "price": 82112.9, "stop": 81946.8,
            "target1": 82653.7, "target2": 82943.0,
            "margin": 69.55, "notional": 2086.50,
            "net_loss": 9.35, "net_gain1": 10.85, "net_gain2": 18.22,
        }
        self.args = {"reference_balance": 1871.9017, "leverage": 30,
                     "max_margin_fraction": .15}

    def test_kst_conversion_short_order_and_usdt_are_visible(self):
        note = format_compact_entry_alert(
            self.plan, "2026-10-16T04:25:01.934562+00:00",
            reference_number=5, **self.args)
        self.assertIn("10/16 13:25 KST", note)
        self.assertIn("신호번호: #5", note)
        self.assertIn("69.55 USDT", note)
        self.assertIn("레버리지 30배 | 포지션 2,086.50 USDT", note)
        self.assertIn("예상 총손실 -9.35 USDT", note)
        self.assertIn("예상 순이익 +10.85 USDT", note)
        self.assertIn("예상 순이익 +18.22 USDT", note)
        self.assertIn("유효 5분", note)
        self.assertIn("실전 진입 추천 아님", note)
        self.assertNotIn("0.0", note)  # no BTC quantity, RSI or tiny indicators
        self.assertLessEqual(len(note.splitlines()), 10)
        for field in ("진입 증거금:", "레버리지", "손절가:", "목표가1:", "목표가2:"):
            self.assertIn(field, note)
        self.assertLess(note.index("진입 증거금:"), note.index("목표가2:"))

    def test_countertrend_disclosure_before_entry_not_buried(self):
        plan = self.plan.copy()
        plan["sampled_context"] = {"trend15_pct": -.12,
                                   "reversal_confirmed": False}
        note = format_compact_entry_alert(
            plan, "2026-10-16T04:25:01Z", **self.args)
        self.assertIn("45분 표본 역추세·반전 미확인", note)
        self.assertLess(note.index("45분 표본 역추세"), note.index("진입가:"))
        plan["sampled_context"]["reversal_confirmed"] = True
        note2 = format_compact_entry_alert(
            plan, "2026-10-16T04:25:01Z", **self.args)
        self.assertNotIn("45분 표본 역추세", note2)

    def test_short_alert_and_reference_field(self):
        plan = dict(self.plan, side="숏", direction=-1)
        note = format_compact_entry_alert(
            plan, "2026-10-16T04:25:01Z", **self.args)
        self.assertIn("🔴 숏", note)
        self.assertIn("81,946.8 USDT", note)
        self.assertNotIn("신호번호", note)

    def test_config_budget_and_margin_stays_within_limits(self):
        self.assertAlmostEqual(monitor.LEVERAGE, 30)
        self.assertAlmostEqual(monitor.MAX_MARGIN_FRACTION, .15)
        self.assertAlmostEqual(monitor.RISK_FRACTION, .005)
        prices = [80000. + 25 * i + (45 if i % 2 else 0) for i in range(60)]
        plan = monitor.classify(prices)
        self.assertIsNotNone(plan)
        self.assertLessEqual(plan["margin"], monitor.REF_BALANCE * .15 + 1e-8)
        self.assertLessEqual(plan["net_loss"], monitor.REF_BALANCE * .005 + 1e-8)
        self.assertAlmostEqual(plan["margin"] * 30, plan["notional"], places=6)
        message = monitor.format_entry_alert(
            plan, "2026-10-16T04:25:01Z", reference_number=99)
        self.assertIn(f"{plan['margin']:,.2f} USDT", message)
        self.assertIn("신호번호: #99", message)


if __name__ == "__main__":
    unittest.main()
