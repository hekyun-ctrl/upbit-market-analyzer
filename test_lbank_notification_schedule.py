"""Offline exact-boundary and weekday-rollover tests for Korean sleep hours."""
import unittest
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

from lbank_notification_schedule import is_quiet_new_entry_time

KST = timezone(timedelta(hours=9))


def at(weekday_date, hour, minute=0):
    return datetime.fromisoformat(weekday_date).replace(
        hour=hour, minute=minute, tzinfo=KST).timestamp()


class NotificationScheduleTests(unittest.TestCase):
    def test_wednesday_through_saturday_nights_quiet_from_23_to_next_06(self):
        # 2026-10-14 = Wednesday, 15 Thu, 16 Fri, 17 Sat
        for date in ("2026-10-14", "2026-10-15", "2026-10-16", "2026-10-17"):
            with self.subTest(date=date):
                self.assertFalse(is_quiet_new_entry_time(at(date, 22, 59)))
                self.assertTrue(is_quiet_new_entry_time(at(date, 23, 0)))
                self.assertTrue(is_quiet_new_entry_time(at(date, 23, 59)))
                tomorrow = (datetime.fromisoformat(date) + timedelta(days=1)).date().isoformat()
                self.assertTrue(is_quiet_new_entry_time(at(tomorrow, 0, 0)))
                self.assertTrue(is_quiet_new_entry_time(at(tomorrow, 5, 59)))
                self.assertFalse(is_quiet_new_entry_time(at(tomorrow, 6, 0)))

    def test_sunday_monday_tuesday_night_shifts_stay_enabled(self):
        for date in ("2026-10-18", "2026-10-19", "2026-10-20"):
            with self.subTest(date=date):
                self.assertFalse(is_quiet_new_entry_time(at(date, 23)))
                tomorrow = (datetime.fromisoformat(date) + timedelta(days=1)).date().isoformat()
                self.assertFalse(is_quiet_new_entry_time(at(tomorrow, 2, 30)))

    def test_daytime_is_always_active(self):
        for date in ("2026-10-14", "2026-10-15", "2026-10-16",
                     "2026-10-17", "2026-10-18", "2026-10-19", "2026-10-20"):
            for hour in (6, 7, 12, 18, 22):
                self.assertFalse(is_quiet_new_entry_time(at(date, hour)))

    def test_live_entry_gate_only_and_no_daily_quota_consumption(self):
        # Risk monitoring is never routed through entry gate. Only the
        # discretionary ENTRY REVIEW and reversal preview use alert_gate.
        import lbank_futures_monitor as monitor
        state = {"day": "2026-10-16", "count": 0,
                 "last_time": None, "last_side": None}
        with patch.object(monitor, "is_quiet_new_entry_time",
                          side_effect=is_quiet_new_entry_time):
            self.assertEqual(monitor.alert_gate(at("2026-10-16", 23),
                                                 "롱", state),
                             "sleep_hours")
            self.assertEqual(state["count"], 0)
            self.assertEqual(monitor.alert_gate(at("2026-10-17", 5, 59),
                                                 "숏", state),
                             "sleep_hours")
            self.assertEqual(monitor.alert_gate(at("2026-10-17", 6),
                                                 "롱", state),
                             "ok")
            monitor.record_alert(at("2026-10-17", 6), "롱", state)
            self.assertEqual(state["count"], 1)

    def test_quiet_entry_gate_does_not_override_active_night_work(self):
        import lbank_futures_monitor as monitor
        state = {"day": None, "count": 0,
                 "last_time": None, "last_side": None}
        for date in ("2026-10-18", "2026-10-19", "2026-10-20"):
            self.assertEqual(monitor.alert_gate(at(date, 23), "롱", state), "ok")


if __name__ == "__main__":
    unittest.main()
