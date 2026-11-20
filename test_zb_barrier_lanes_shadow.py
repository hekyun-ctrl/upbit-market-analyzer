"""Deterministic regression checks for the research-only barrier lanes."""
import copy
import unittest

from research.zb_barrier_lanes_shadow import evaluate_barrier_lanes


class BarrierLanesShadowTests(unittest.TestCase):
    def test_observed_61774_8_barrier_is_not_a_profitable_first_exit(self):
        result = evaluate_barrier_lanes(
            -1, 61774.8, 62000.0, 61723.8, 61192.8)
        self.assertFalse(result["entry_approved"])
        self.assertTrue(result["shadow_only"])
        self.assertEqual(result["first_barrier"]["status"], "insufficient_net_rr")
        self.assertLess(result["first_barrier"]["net_rr"], 1)
        self.assertEqual(result["continuation"]["status"],
                         "awaiting_confirmed_break_and_retest")

    def test_distant_target_does_not_override_first_barrier_risk(self):
        before = evaluate_barrier_lanes(-1, 100, 101, 99.9, 90)
        after = evaluate_barrier_lanes(-1, 100, 101, 99.9, 70)
        self.assertEqual(before["first_barrier"], after["first_barrier"])
        self.assertEqual(before["first_barrier"]["status"], "insufficient_net_rr")

    def test_fresh_rebreak_requires_two_observable_closed_minutes(self):
        bars = [
            dict(start=1020, received_at=1081, quality="stream_closed",
                 open=96, high=96.3, low=93.7, close=94),
            dict(start=1080, received_at=1141, quality="stream_closed",
                 open=94, high=95.1, low=93.8, close=94.2),
        ]
        copy_bars = copy.deepcopy(bars)
        result = evaluate_barrier_lanes(
            -1, 100, 101, 95, 90, bars, observed_at=1141,
            next_target=80, next_target_known_at=1000)
        self.assertEqual(result["continuation"]["status"], "net_rr_pass_shadow")
        self.assertGreaterEqual(result["continuation"]["net_rr"], 2)
        self.assertFalse(result["entry_approved"])
        self.assertEqual(bars, copy_bars)
        early = evaluate_barrier_lanes(
            -1, 100, 101, 95, 90, bars, observed_at=1140,
            next_target=80)
        self.assertEqual(early["continuation"]["status"],
                         "invalid_or_future_candles")
        no_next = evaluate_barrier_lanes(
            -1, 100, 101, 95, 90, bars, observed_at=1141)
        self.assertEqual(no_next["continuation"]["status"],
                         "missing_prior_structural_target")
        future_target = evaluate_barrier_lanes(
            -1, 100, 101, 95, 90, bars, observed_at=1141,
            next_target=80, next_target_known_at=1080)
        self.assertEqual(future_target["continuation"]["status"], "target_not_known_before_break")

    def test_incomplete_or_failed_retest_does_not_issue_signal(self):
        bars = [
            dict(start=1020, received_at=1080, quality="stream_closed",
                 open=96, high=96.3, low=93.7, close=94),
            dict(start=1080, received_at=1140, quality="stream_closed",
                 open=94, high=94.6, low=93.8, close=94.2),
        ]
        result = evaluate_barrier_lanes(
            -1, 100, 101, 95, 90, bars, observed_at=1140, next_target=80)
        self.assertEqual(result["continuation"]["status"],
                         "awaiting_confirmed_break_and_retest")
        bars[1]["quality"] = "forming"
        result = evaluate_barrier_lanes(
            -1, 100, 101, 95, 90, bars, observed_at=1140, next_target=80)
        self.assertEqual(result["continuation"]["status"],
                         "invalid_or_future_candles")

    def test_no_invalid_direction_or_fake_target(self):
        bad = evaluate_barrier_lanes(-1, 100, 99, 95, 90)
        self.assertEqual(bad["first_barrier"]["status"], "invalid")
        result = evaluate_barrier_lanes(1, 100, 99, 105, 110)
        self.assertFalse(result["entry_approved"])


if __name__ == "__main__":
    unittest.main()
