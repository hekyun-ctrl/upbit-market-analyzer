from datetime import datetime, timedelta, timezone
import unittest

from volume_history import build_72h_turnover_snapshot, summarize_72h_ranked_outcomes


class VolumeHistoryTests(unittest.TestCase):
    def test_ranks_completed_72_hours_and_excludes_current_hour(self):
        as_of = datetime(2026, 10, 7, 2, 37, tzinfo=timezone(timedelta(hours=9)))
        boundary = as_of.astimezone(timezone.utc).replace(minute=0, second=0, microsecond=0)

        def history(value, hours=80):
            return [
                {
                    "candle_date_time_utc": (boundary - timedelta(hours=hours - index)).strftime("%Y-%m-%dT%H:%M:%S"),
                    "candle_acc_trade_price": value,
                }
                for index in range(hours)
            ] + [{
                "candle_date_time_utc": boundary.strftime("%Y-%m-%dT%H:%M:%S"),
                "candle_acc_trade_price": 1_000_000,
            }]

        snapshot = build_72h_turnover_snapshot(
            {"KRW-AAA": history(1_000), "KRW-BBB": history(2_000)}, as_of
        )
        rows = {row["market"]: row for row in snapshot["items"]}
        self.assertEqual(snapshot["ranked_market_count"], 2)
        self.assertEqual(rows["KRW-BBB"]["rank_72h"], 1)
        self.assertEqual(rows["KRW-AAA"]["turnover_72h_krw"], 72_000)
        self.assertEqual(rows["KRW-BBB"]["turnover_72h_krw"], 144_000)

    def test_incomplete_history_is_not_assigned_a_rank(self):
        as_of = datetime(2026, 10, 7, 2, 0, tzinfo=timezone.utc)
        boundary = as_of
        bars = [
            {
                "candle_date_time_utc": (boundary - timedelta(hours=23 - i)).strftime("%Y-%m-%dT%H:%M:%S"),
                "candle_acc_trade_price": 100,
            }
            for i in range(23)
        ]
        snapshot = build_72h_turnover_snapshot({"KRW-NEW": bars}, as_of)
        row = snapshot["items"][0]
        self.assertFalse(row["history_complete"])
        self.assertIsNone(row["rank_72h"])
        self.assertEqual(snapshot["ranked_market_count"], 0)

    def test_candidate_outcomes_are_compared_by_rank_band_without_auto_tuning(self):
        summary = summarize_72h_ranked_outcomes([
            {"volume_72h_rank": 2, "result": "target_1_first"},
            {"volume_72h_rank": 40, "result": "stop_first"},
            {"volume_72h_rank": 78, "result": "stop_first"},
            {"volume_72h_rank": None, "result": "target_1_first"},
        ])
        top = summary["by_rank_band"]["top_50"]
        self.assertEqual(top["sample_count"], 2)
        self.assertEqual(top["target_1_first_rate_pct"], 50.0)
        self.assertEqual(summary["unrated_outcome_count"], 1)
        self.assertFalse(summary["automatic_threshold_tuning"])


if __name__ == "__main__":
    unittest.main()
