import unittest

from alert_limits import early_watch_daily_limits, restored_early_watch_usage


class EarlyWatchDailyLimitTests(unittest.TestCase):
    def test_overnight_does_not_consume_priority_reserve(self):
        self.assertEqual(early_watch_daily_limits(8, 2, 0), (8, 0))
        self.assertEqual(early_watch_daily_limits(8, 2, 7), (8, 0))

    def test_priority_reserve_opens_at_eight_kst(self):
        self.assertEqual(early_watch_daily_limits(8, 2, 8), (10, 2))
        self.assertEqual(early_watch_daily_limits(8, 2, 23), (10, 2))

    def test_invalid_hour_is_rejected(self):
        with self.assertRaises(ValueError):
            early_watch_daily_limits(8, 2, 24)

    def test_legacy_overnight_overflow_does_not_lock_morning_reserve(self):
        self.assertEqual(restored_early_watch_usage(10, 0, 0, 8), (8, 0))
        self.assertEqual(restored_early_watch_usage(10, 1, 1, 8), (9, 1))

    def test_normal_usage_is_preserved(self):
        self.assertEqual(restored_early_watch_usage(6, 2, 1, 8), (8, 1))


if __name__ == "__main__":
    unittest.main()
