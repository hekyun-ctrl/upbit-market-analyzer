"""Standard-library regressions for completed-breakout setup re-anchoring."""
import unittest

from candidate_analysis import (
    _fresh_structure_breadth_ok,
    _fresh_structure_leadership_ok,
    _setup_anchor_context,
)


class FreshSetupLifecycleTests(unittest.TestCase):
    def test_completed_breakout_reanchors_the_new_plan_without_losing_origin_audit(self):
        result = _setup_anchor_context(
            {"market": "KRW-DRV", "signal_id": "new-5m-setup",
             "origin_signal_price": 610, "price": 646,
             "original_signal_time_utc": "2026-10-09T01:00:00+00:00"},
            652,
            {"close": 650, "as_of": 1791508500.0},
            True,
        )
        self.assertEqual(result["setup_anchor_price"], 650)
        self.assertAlmostEqual(result["origin_extension_pct"], (652 / 610 - 1) * 100, places=3)
        self.assertAlmostEqual(result["setup_extension_pct"], (652 / 650 - 1) * 100, places=3)
        self.assertEqual(result["setup_anchor_reason"], "completed_5m_structure_close")
        self.assertEqual(result["setup_id"], "new-5m-setup")

    def test_rank_boundary_is_not_a_top_two_percent_cliff(self):
        self.assertTrue(_fresh_structure_leadership_ok(True, 2.12, .5, .3))
        self.assertTrue(_fresh_structure_leadership_ok(True, 5.0, .5, .3))
        self.assertFalse(_fresh_structure_leadership_ok(True, 5.01, .5, .3))
        self.assertFalse(_fresh_structure_leadership_ok(True, 3.0, .5, .19))

    def test_market_breadth_only_blocks_near_total_collapse_for_fresh_setup(self):
        self.assertTrue(_fresh_structure_breadth_ok(17.1))
        self.assertTrue(_fresh_structure_breadth_ok(8.0))
        self.assertFalse(_fresh_structure_breadth_ok(7.99))


if __name__ == "__main__":
    unittest.main()
