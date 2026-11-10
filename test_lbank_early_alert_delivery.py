"""Tests for opt-in observational Telegram, no entry orders."""
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from lbank_early_alert_delivery import (activation_check, send_early_candidate,
                                         send_feed_warning, FEED_STALE_SECONDS)


class EarlyDeliveryTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.ledger = SimpleNamespace(db=self.db)
        self.start = 1794000000 - 1794000000 % 300

    def rows(self, *, c=103, v=20, n=21):
        rows = []
        for i in range(n):
            current = i == n - 1
            close = c if current else 100
            rows.append(dict(
                start=self.start + i * 300, period=300,
                open=100, high=max(102, close), low=min(98, close), close=close,
                volume=v if current else 10,
                quality="stream_closed",
                received_at=self.start + (i+1)*300+1))
        return rows

    def send(self, rows, send, **extra):
        return send_early_candidate(
            self.ledger, rows, now=rows[-1]["start"] + 302,
            final_status="no_qualified_entry", send=send, enabled=True, **extra)

    def test_opt_in_required_and_no_order_calls(self):
        rows = self.rows()
        send = Mock(return_value=True)
        self.assertEqual(send_early_candidate(
            self.ledger, rows, now=rows[-1]["start"] + 302,
            final_status="no_qualified_entry", send=send, enabled=False), "disabled")
        send.assert_not_called()
        self.assertEqual(self.send(rows, send), "accepted")
        body = send.call_args.args[0]
        self.assertIn("관찰", body)
        self.assertIn("매매 진입 신호 아님", body)
        self.assertNotIn("목표가", body)
        self.assertNotIn("손절가", body)

    def test_replay_and_restart_cooldown_suppress_duplicate(self):
        send = Mock(return_value=True)
        x = self.rows()
        self.assertEqual(self.send(x, send), "accepted")
        self.assertEqual(self.send(x, send), "cooldown")
        later = self.rows(n=22)
        self.assertEqual(self.send(later, send), "cooldown")
        send.assert_called_once()

    def test_after_30_min_cooldown_can_send(self):
        send = Mock(return_value=True)
        rows = self.rows()
        self.assertEqual(self.send(rows, send), "accepted")
        next_rows = self.rows(n=27)
        self.assertEqual(self.send(next_rows, send), "accepted")
        self.assertEqual(send.call_count, 2)

    def test_full_signal_priority(self):
        send = Mock()
        rows = self.rows()
        self.assertEqual(send_early_candidate(
            self.ledger, rows, now=rows[-1]["start"]+302,
            final_status="telegram_accepted", send=send, enabled=True), "entry_alert_priority")
        send.assert_not_called()

    def test_stale_and_insufficient_and_non_candidate(self):
        send = Mock()
        rows = self.rows()
        self.assertEqual(send_early_candidate(
            self.ledger, rows, now=rows[-1]["start"]+332,
            final_status="no_qualified_entry", send=send, enabled=True), "stale_or_unverified")
        self.assertEqual(self.send(rows[:20], send), "insufficient_history")
        self.assertEqual(self.send(self.rows(c=100), send), "no_breakout")
        send.assert_not_called()

    def test_unconfirmed_send_not_retried(self):
        send = Mock(side_effect=TimeoutError("timeout"))
        self.assertEqual(self.send(self.rows(), send), "unconfirmed")
        self.assertEqual(self.send(self.rows(), send), "cooldown")
        send.assert_called_once()

    def test_activation_check_once_per_revision_and_disabled(self):
        send = Mock(return_value=True)
        self.assertEqual(activation_check(self.ledger, now=1, revision="abc",
            send=send, enabled=False), "disabled")
        self.assertEqual(activation_check(self.ledger, now=2, revision="abc",
            send=send, enabled=True), "accepted")
        self.assertEqual(activation_check(self.ledger, now=3, revision="abc",
            send=send, enabled=True), "already_reserved")
        self.assertEqual(send.call_count, 1)
        self.assertIn("시험 메시지", send.call_args.args[0])

    def test_stale_feed_warning_capped_and_labeled(self):
        send = Mock(return_value=True)
        self.assertEqual(send_feed_warning(self.ledger, now=1000,
             last_fresh=1000-FEED_STALE_SECONDS+1, send=send, enabled=True),
             "healthy_or_grace")
        self.assertEqual(send_feed_warning(self.ledger, now=1000,
             last_fresh=0, send=send, enabled=True), "accepted")
        self.assertEqual(send_feed_warning(self.ledger, now=1001,
             last_fresh=0, send=send, enabled=True), "already_reserved")
        send.assert_called_once()
        self.assertIn("연속 완료봉", send.call_args.args[0])
        self.assertIn("40개", send.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
