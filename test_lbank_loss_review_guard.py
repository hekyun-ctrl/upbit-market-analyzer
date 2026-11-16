"""Fail-closed no-live-entry gate and causal independent lane tests."""
import sqlite3
import unittest
from unittest.mock import Mock
from tempfile import TemporaryDirectory
from pathlib import Path
from lbank_video_alert import process, DeliveryLedger
from test_lbank_video_entry import breakout
from copy import deepcopy
from types import SimpleNamespace
from lbank_loss_review_guard import (
    entry_delivery_allowed,parallel_observation,persist_parallel_review,MIN_NET_RR,
)


class LossReviewGuardTests(unittest.TestCase):
    def rows(self):
        start=1794700800
        rows=[]
        for i in range(21):
            t=start+i*300
            rows.append(dict(period=300,start=t,open=1000.,high=1010.,
                             low=990.,close=1000.,volume=10.,
                             quality="stream_closed",received_at=t+301))
        return rows

    def test_review_lock_is_not_a_runtime_environment_switch(self):
        self.assertFalse(entry_delivery_allowed())
        self.assertEqual(MIN_NET_RR,2.)

    def test_formal_signal_blocked_before_quote_or_send(self):
        fixture=breakout()[:46]
        rows=[dict(x,received_at=x["start"]+301) for x in fixture]
        end=rows[-1]["start"]+300
        with TemporaryDirectory() as tmp:
            ledger=DeliveryLedger(Path(tmp)/"delivery.db")
            self.addCleanup(ledger.db.close)
            quote=Mock(side_effect=AssertionError("quote should not be needed"))
            send=Mock(side_effect=AssertionError("formal send must stay locked"))
            state,reasons=process(rows,now=end+2,started=end-1,
                clock=lambda:end+2,get_book=quote,ledger=ledger,send=send,
                review_locked=True)
            self.assertEqual(state,"review_lock_unverified_live_performance")
            quote.assert_not_called()
            send.assert_not_called()
            event=ledger.db.execute(
              "SELECT evidence FROM video_alert_decisions").fetchone()[0]
            self.assertIn("review_blocked",event)

    def test_b_support_break_not_gated_on_a_retest(self):
        rows=self.rows()
        rows[-1].update(open=992.,high=995.,low=972.,close=978.,volume=32.)
        got=parallel_observation(rows)
        self.assertEqual(got["status"],"observed")
        self.assertEqual(got["lanes"]["B"]["status"],"observed_not_entry")
        self.assertEqual(got["lanes"]["A"]["status"],"no_event")
        self.assertFalse(got["entry_approved"])

    def test_a_rejection_and_b_break_can_be_recorded_together(self):
        rows=self.rows()
        # Wide range goes through the upper threshold and below support,
        # both must remain visible (though chase guard rejects execution).
        rows[-1].update(open=1015.,high=1015.,low=970.,close=975.,volume=35.)
        got=parallel_observation(rows)
        self.assertTrue(got["lanes"]["A"]["triggered"])
        self.assertTrue(got["lanes"]["B"]["triggered"])
        self.assertNotEqual(got["lanes"]["B"]["status"],"no_event")

    def test_breakout_up_independently_evaluated(self):
        rows=self.rows()
        rows[-1].update(open=1003.,high=1028.,low=1001.,close=1025.,volume=30.)
        x=parallel_observation(rows)
        self.assertTrue(x["lanes"]["C"]["triggered"])
        self.assertFalse(x["lanes"]["A"]["triggered"])

    def test_new_low_without_known_target_does_not_invent_target(self):
        rows=self.rows()
        rows[-1].update(open=992.,high=995.,low=972.,close=978.,volume=32.)
        x=parallel_observation(rows)
        self.assertIsNone(x["lanes"]["B"]["known_barrier"])

    def test_large_candle_rejected_as_extended(self):
        rows=self.rows()
        rows[-1].update(open=1010.,high=1010.,low=935.,close=937.,volume=35.)
        self.assertEqual(parallel_observation(rows)["lanes"]["B"]["status"],
                         "already_extended")

    def test_gap_and_forming_candle_fail_closed(self):
        rows=self.rows()
        rows[-2]["start"]+=60
        self.assertEqual(parallel_observation(rows)["status"],
                         "invalid_or_gapped_candles")
        rows=self.rows()
        rows[-1]["quality"]="forming"
        self.assertEqual(parallel_observation(rows)["status"],
                         "invalid_or_gapped_candles")

    def test_cannot_use_newer_data_to_decide_past_bar(self):
        rows=self.rows()
        rows[-1].update(open=992.,high=995.,low=972.,close=978.,volume=32.)
        first=parallel_observation(rows)
        later=dict(rows[-1],start=rows[-1]["start"]+300,
                   received_at=rows[-1]["received_at"]+300,
                   high=1500.,low=900.,open=1000.,close=1020.)
        self.assertEqual(first,parallel_observation(rows+[later][:-1]))

    def test_ledger_one_record_per_completed_bar_and_no_send(self):
        db=sqlite3.connect(":memory:");self.addCleanup(db.close)
        x=SimpleNamespace(db=db)
        rows=self.rows()
        self.assertEqual(persist_parallel_review(x,rows)["status"],"observed")
        self.assertEqual(persist_parallel_review(x,rows)["status"],"observed")
        self.assertEqual(db.execute("SELECT count(*) FROM zb_parallel_review").fetchone()[0],1)
        self.assertIn('"entry_approved": false',
            db.execute("SELECT evidence FROM zb_parallel_review").fetchone()[0])


if __name__=="__main__":
    unittest.main()
