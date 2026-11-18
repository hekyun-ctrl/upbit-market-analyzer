"""Fail-closed and at-most-once tests for prioritized candidate delivery."""
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from lbank_opportunity_review import evaluate_review, send_review, announce_release


class OpportunityReviewTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.ledger = SimpleNamespace(db=self.db)
        self.start = 1794700000 // 300 * 300
        self.bars = []
        for i in range(90):
            t = self.start + i*300
            self.bars.append(dict(start=t, period=300, open=10000.,
                high=10008., low=9990., close=10000., volume=50.,
                quality="stream_closed", received_at=t+301))
        self.now = self.bars[-1]["start"]+303
        self.obs = dict(status="observed", atr14=45.,
            resistance=10050., support=9990., volume_multiple=2.5,
            lanes={"A":{"status":"no_event"}, "B":{"status":"observed_not_entry"},
                   "C":{"status":"no_event"}})
        self.rank = dict(status="ready", trend="bearish",
                         directions={"5m":-1,"15m":-1,"1h":-1})

    def check(self, *, price=(9999.9, 10000.1), barriers=True):
        b = [{"price":9400.,"available_at":self.now-10}] if barriers else []
        with patch("lbank_opportunity_review.parallel_observation",return_value=self.obs), \
             patch("lbank_opportunity_review.trend_priority",return_value=self.rank), \
             patch("lbank_opportunity_review.available_barriers",return_value=b):
            return evaluate_review(self.bars, now=self.now,
                                   get_book=Mock(return_value=price),
                                   clock=lambda:self.now)

    def test_eligible_short_has_real_stop_and_observed_target(self):
        x = self.check()
        self.assertEqual(x["status"], "review_candidate")
        self.assertEqual(x["lane"], "B")
        self.assertGreater(x["stop"], x["quote"])
        self.assertLess(x["target"], x["quote"])
        self.assertGreaterEqual(x["net_rr_assumed"], 2.)

    def test_unknown_target_never_qualifies(self):
        self.assertEqual(self.check(barriers=False)["status"],"screen_rejected")

    def test_crossed_book_never_qualifies(self):
        self.assertEqual(self.check(price=(10000.,10000.))["status"],
                         "stale_or_wide_book")

    def test_stale_candle_cannot_be_sent(self):
        self.assertEqual(evaluate_review(self.bars,now=self.now+60,
                         get_book=Mock()), {"status":"stale_or_unverified"})

    def test_no_candidate_no_message(self):
        sender=Mock()
        self.assertEqual(send_review(self.ledger,self.bars,now=self.now,
            started=self.now-300,get_book=Mock(),send=sender,
            enabled=False,final_status="no_qualified_entry"),"disabled")
        sender.assert_not_called()

    def test_manual_signal_lock_wins(self):
        sender=Mock()
        self.assertEqual(send_review(self.ledger,self.bars,now=self.now,
            started=self.now-300,get_book=Mock(),send=sender,
            enabled=True,final_status="telegram_accepted"),"formal_alert_priority")
        sender.assert_not_called()

    def test_release_notice_dedupes_across_restarts(self):
        sender=Mock(return_value=True)
        self.assertEqual(announce_release(self.ledger,revision="abc",
            now=self.now,send=sender,enabled=True),"accepted")
        self.assertEqual(announce_release(self.ledger,revision="abc",
            now=self.now,send=sender,enabled=True),"already_reserved")
        self.assertEqual(sender.call_count,1)
        self.assertIn("진입 신호가 아닙니다",sender.call_args.args[0])


if __name__ == "__main__":
    unittest.main()
