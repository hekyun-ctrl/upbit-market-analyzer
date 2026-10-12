"""Offline unit checks for LBank BTC cost-aware watch signal.

Run: python -m unittest test_lbank_fee_risk.py
"""
import unittest

from lbank_futures_monitor import (
    REF_BALANCE, RISK_FRACTION, classify,
    minimum_net_rr_target, net_pnl_per_btc,
    confirm_watch, format_entry_alert,
)


class LBankCostModelTests(unittest.TestCase):
    def test_flat_price_is_loss_after_fees_slippage_and_funding_reserve(self):
        for side in (-1, 1):
            self.assertLess(net_pnl_per_btc(side, 83000, 83000), -100.0)

    def test_both_directions_targets_clear_net_rr(self):
        for direction in (-1, 1):
            entry = 83000.
            stop = entry - direction * 260
            loss = -net_pnl_per_btc(direction, entry, stop)
            self.assertGreater(loss, 0)
            for ratio in (1.5, 2.5):
                target = minimum_net_rr_target(direction, entry, loss, ratio)
                self.assertIsNotNone(target)
                self.assertGreater(direction * (target - entry), 0)
                self.assertAlmostEqual(net_pnl_per_btc(direction, entry, target) / loss,
                                       ratio, places=5)

    def test_no_alert_before_60_samples(self):
        self.assertIsNone(classify([80000. + i * 25 for i in range(59)]))

    def test_risk_limit_and_target_after_realistic_uptrend(self):
        # Alternating advances and small declines produce RSI < 80 while
        # preserving a confirmed high and strong short-term momentum.
        data = [80000. + 25 * i + (45 if i % 2 else 0) for i in range(60)]
        signal = classify(data)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["side"], "롱 관찰")
        self.assertLessEqual(signal["net_loss"], REF_BALANCE * RISK_FRACTION + 1e-8)
        self.assertLessEqual(signal["margin"], REF_BALANCE * 0.10 + 1e-8)
        self.assertGreaterEqual(signal["rr1"], 1.5 - 1e-7)
        self.assertGreaterEqual(signal["rr2"], 2.5 - 1e-7)
        self.assertGreater(signal["fee_roundtrip_est"], 0)

    def test_two_stage_watch_converts_to_entry_review_with_confirmation(self):
        base = [80000. + 25 * i + (45 if i % 2 else 0) for i in range(60)]
        watch = classify(base)
        self.assertIsNotNone(watch)
        self.assertEqual(watch["direction"], 1)
        # Next one-minute quote stabilizes near the breakout rather than chasing.
        now = base + [base[-1] + 5]
        confirmed = confirm_watch(now, watch, 60)
        self.assertIsNotNone(confirmed)
        self.assertEqual(confirmed["side"], "롱")
        self.assertLessEqual(confirmed["net_loss"], REF_BALANCE * RISK_FRACTION + 1e-8)
        self.assertGreaterEqual(confirmed["rr1"], 1.5 - 1e-7)
        alert = format_entry_alert(confirmed, "2026-10-13T00:00:00Z")
        self.assertIn("조건부 진입 검토", alert)
        self.assertIn("손절 예상 순손실", alert)
        self.assertIn("95.80 USDT", alert)

    def test_stage_two_rejects_stale_watch_and_price_chasing(self):
        base = [80000. + 25 * i + (45 if i % 2 else 0) for i in range(60)]
        watch = classify(base)
        self.assertIsNotNone(watch)
        self.assertIsNone(confirm_watch(base + [base[-1]], watch, 300))
        self.assertIsNone(confirm_watch(base + [base[-1] * 1.003], watch, 60))

    def test_avoids_narrow_targets_that_only_cover_fees(self):
        entry = 82000.
        loss = -net_pnl_per_btc(1, entry, entry * (1 - .0025))
        target = minimum_net_rr_target(1, entry, loss, 1.5)
        self.assertGreater((target - entry) / entry, .007)  # > 0.7% gross move


if __name__ == "__main__":
    unittest.main()
