"""Offline tests for safe LBank ticker-only context and source separation."""
import unittest

from unittest.mock import patch

from lbank_sampled_context import (
    completed_sample_closes, sampled_context_gate, three_step_sample_reversal,
    verified_reversal_recovery)
from lbank_futures_monitor import (
    REF_BALANCE, RISK_FRACTION, classify, format_entry_alert)


class SampledContextTests(unittest.TestCase):
    @staticmethod
    def snapshots(fn, *, count=120):
        # Aligned to 15-minute epochs; 1-minute ticker observations, not OHLCV.
        start = 1800000000 // 900 * 900
        return [(start + 60 * i + 55, float(fn(i))) for i in range(count)]

    def test_completed_buckets_exclude_open_bar_and_future_tick(self):
        normal = self.snapshots(lambda i: 83000 + i * 7)
        changed = normal[:-1] + [(normal[-1][0], normal[-1][1] + 10000)]
        self.assertEqual(completed_sample_closes(normal, 300, 8),
                         completed_sample_closes(changed, 300, 8))
        self.assertEqual(completed_sample_closes(normal, 900, 5),
                         completed_sample_closes(changed, 900, 5))

    def test_flat_market_both_directions_blocked(self):
        obs = self.snapshots(lambda i: 83000)
        for direction in (-1, 1):
            allowed, reason, ctx = sampled_context_gate(obs, direction, "range_rebound")
            self.assertFalse(allowed)
            self.assertEqual(reason, "flat_sampled_market")
            self.assertEqual(ctx["context_type"], "sampled_perpetual_ticker_not_exchange_ohlcv")

    def test_impulse_rejects_late_fomo(self):
        obs = self.snapshots(lambda i: 82000 + i * 48)
        allowed, reason, _ = sampled_context_gate(obs, 1, "breakout")
        self.assertFalse(allowed)
        self.assertEqual(reason, "avoid_late_5m_impulse_chase")

    def test_strong_countertrend_rejects_falling_knife_and_squeeze(self):
        down = self.snapshots(lambda i: 84500 - i * 16)
        up = self.snapshots(lambda i: 82000 + i * 16)
        for obs, direction in ((down, 1), (up, -1)):
            allowed, reason, _ = sampled_context_gate(obs, direction, "range_rebound")
            self.assertFalse(allowed)
            self.assertEqual(reason, "avoid_15m_strong_countertrend")

    def test_discontinuous_prices_fail_closed(self):
        obs = self.snapshots(lambda i: 82500 + (i % 7) * 35)
        broken = obs[:60] + obs[66:]
        allowed, reason, ctx = sampled_context_gate(broken, 1, "breakout")
        self.assertFalse(allowed)
        self.assertEqual(reason, "context_discontinuous_quotes")
        self.assertIsNone(ctx)

    def test_step_pattern_is_diagnostic_not_executable_candle_rule(self):
        self.assertTrue(three_step_sample_reversal([84000, 83900, 83800, 83700, 83770], 1))
        self.assertTrue(three_step_sample_reversal([83000, 83100, 83200, 83300, 83200], -1))
        self.assertFalse(three_step_sample_reversal([83000, 82999, 82998, 82997, 83000], 1))
        self.assertFalse(three_step_sample_reversal([83000, 82900, 82800, 82700, 82600], 1))

    def test_verified_reversal_only_after_two_closed_5m_recovery_buckets(self):
        # 23:06 UTC-reconstructed ticker bucket sequence, NOT candle OHLCV.
        five = [80800., 80500., 80600., 80679.6, 80450.1, 79868.9,
                80137.8, 80498.4]
        fifteen = [81000., 80507.9, 80217.9, 80679.6, 80137.8]
        trend15 = fifteen[-1] / fifteen[-4] - 1
        yes, move, last = verified_reversal_recovery(five, 1, trend15)
        self.assertTrue(yes)
        self.assertGreater(move, .6)
        self.assertGreater(last, .15)
        obs = self.snapshots(lambda i: 80500 + i * 7)
        with patch("lbank_sampled_context.completed_sample_closes",
                   side_effect=lambda observations, interval_seconds, n:
                   five if interval_seconds == 300 else fifteen):
            ok, reason, ctx = sampled_context_gate(obs, 1, "breakout")
        self.assertTrue(ok, reason)
        self.assertTrue(ctx["reversal_confirmed"])
        self.assertEqual(reason, "context_verified_reversal")
        self.assertEqual(ctx["context_type"],
                         "sampled_perpetual_ticker_not_exchange_ohlcv")

    def test_first_early_bounce_is_not_verified_reversal(self):
        # At ~22:40 KST two 5m sampled rises only recovered ~0.38%.
        five = [80500., 80600., 80800., 80440., 80397.,
                80217.9, 80355.7, 80520.5]
        fifteen = [81100., 81000., 80800., 80400., 80351.]
        trend15 = fifteen[-1] / fifteen[-4] - 1
        self.assertFalse(verified_reversal_recovery(five, 1, trend15)[0])
        obs = self.snapshots(lambda i: 80500 + i * 7)
        with patch("lbank_sampled_context.completed_sample_closes",
                   side_effect=lambda observations, interval_seconds, n:
                   five if interval_seconds == 300 else fifteen):
            ok, reason, ctx = sampled_context_gate(obs, 1, "breakout")
        self.assertFalse(ok)
        self.assertEqual(reason, "avoid_15m_strong_countertrend")
        self.assertFalse(ctx["reversal_confirmed"])

    def test_squeeze_after_overextended_5m_impulse_still_blocked(self):
        # Even verified reversal data do not waive the 0.70% late-chase veto.
        five = [81000., 81000., 81000., 80450., 80000.,
                80160., 80500., 81200.]
        fifteen = [81000., 81000., 80800., 80300., 80500.]
        obs = self.snapshots(lambda i: 80500 + i * 7)
        with patch("lbank_sampled_context.completed_sample_closes",
                   side_effect=lambda observations, interval_seconds, n:
                   five if interval_seconds == 300 else fifteen):
            ok, reason, ctx = sampled_context_gate(obs, 1, "breakout")
        self.assertFalse(ok)
        self.assertEqual(reason, "avoid_late_5m_impulse_chase")

    def test_reversal_does_not_bypass_extremely_opposed_15m_trend(self):
        five = [80800., 80500., 80600., 80679.6, 80450.1, 79868.9,
                80137.8, 80498.4]
        self.assertFalse(verified_reversal_recovery(five, 1, -.014)[0])
        self.assertFalse(verified_reversal_recovery(five, -1, -.0046)[0])
        self.assertFalse(verified_reversal_recovery(five, 0, -.0046)[0])

    def test_existing_risk_and_alert_label_unmodified(self):
        p = [80000. + 25 * i + (45 if i % 2 else 0) for i in range(60)]
        signal = classify(p)
        self.assertIsNotNone(signal)
        self.assertLessEqual(signal["net_loss"], REF_BALANCE * RISK_FRACTION + 1e-8)
        signal["sampled_context"] = {"range5_pct": 0.35, "trend5_pct": 0.18,
                                      "trend15_pct": 0.30, "three_step_recovery": False}
        alert = format_entry_alert(signal, "2026-10-14T05:30:00Z")
        self.assertIn("완료된 선물 가격표본", alert)
        self.assertIn("실제 캔들 3틱룰과 다름", alert)


if __name__ == "__main__":
    unittest.main()
