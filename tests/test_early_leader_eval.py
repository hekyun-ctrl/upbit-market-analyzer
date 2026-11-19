"""Regression tests for read-only, outcome-time-safe comparison."""
import json
import unittest
from research.early_leader_eval import load, compare, tally

def event(sid, day, result, pct=5.0, momentum=2.0, approved=False):
    return {
        "signal_id": sid,
        "signal_time_utc": day + "T10:00:00+00:00",
        "completed_at_utc": day + "T11:00:00+00:00",
        "market": "KRW-TEST",
        "relative_strength_percentile": pct,
        "momentum_5m_pct": momentum,
        "result": result,
        "approved_candidate": approved,
    }

class EarlyLeaderEvalTests(unittest.TestCase):
    def test_same_signal_replay_is_deduplicated(self):
        a = event("one", "2026-11-17", "target_first")
        result = load([json.dumps(a), json.dumps(a)])
        self.assertEqual(len(result), 1)

    def test_conflicting_outcome_is_rejected(self):
        a = event("one", "2026-11-17", "target_first")
        b = event("one", "2026-11-17", "stop_first")
        with self.assertRaises(ValueError):
            load([json.dumps(a), json.dumps(b)])

    def test_signal_time_defines_cohort_not_outcome_time(self):
        rows = [
            dict(event("one", "2026-11-17", "target_first"),
                 completed_at_utc="2026-11-18T00:10:00+00:00"),
            event("two", "2026-11-18", "stop_first", pct=50),
        ]
        r = compare(rows, "2026-11-17", "2026-11-18")
        self.assertEqual(r["results"]["baseline_all_raw"]["2026-11-17"]["target_first"], 1)
        self.assertEqual(r["results"]["baseline_all_raw"]["2026-11-18"]["stop_first"], 1)
        self.assertEqual(r["results"]["relative_strength_top10"]["2026-11-18"]["signals"], 0)
        self.assertFalse(r["production_approval"])

    def test_tally_counts_expired_without_treating_as_loss(self):
        rows = [event("a", "2026-11-18", "target_first"),
                event("b", "2026-11-18", "stop_first"),
                event("c", "2026-11-18", "expired")]
        result = tally(rows)
        self.assertEqual(result["signals"], 3)
        self.assertEqual(result["expired"], 1)
        self.assertEqual(result["target_first_rate_resolved_pct"], 50.0)
        self.assertEqual(result["hypothetical_net_pct_per_resolved"], 0.8)

    def test_log_line_prefix_handling(self):
        a = event("one", "2026-11-17", "target_first")
        lines = ["2026-11-17 INFO:app:RAW_SIGNAL_OUTCOME " + json.dumps(a),
                 "2026-11-17 INFO:app:NOT_AN_OUTCOME " + json.dumps(a)]
        self.assertEqual(len(load(lines)), 1)

if __name__ == "__main__":
    unittest.main()
