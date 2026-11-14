"""Double-bottom research notifications: causal geometry and delivery tests."""
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from lbank_double_bottom_alerts import (
    detect_double_bottom, send_double_bottom_candidate, format_double_bottom,
)


class DoubleBottomTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.ledger=SimpleNamespace(db=self.db)
        self.start=1794700000//300*300
        self.rows=self.w_shape()

    def w_shape(self):
        # Downtrend, W (low1 at 18, low2 at 29), breakout at 33,
        # persistence confirmed at 35 and later neckline failure at 36.
        cs=[1033.-i*2 for i in range(18)]
        cs += [990.,994.,998.,1002.,1005.,1006.,1003.,
               999.,995.,991.,990.,989.,990.,990.5,
               997.,1009.,1010.,1009.,1003.]
        rows=[]
        for i,close in enumerate(cs):
            opening = cs[i-1] if i else close+2
            high = max(opening,close)+1
            low = min(opening,close)-1
            if i==18:
                low=985.
            if i==29:
                low=986.
            volume = 30. if i==18 else 9. if i==29 else 40. if i==33 else 10.
            start=self.start+i*300
            rows.append(dict(period=300,start=start,
                             open=opening,high=high,low=low,
                             close=close,volume=volume,
                             quality="stream_closed",received_at=start+301))
        return rows

    def call(self, end=31, **kw):
        seq=self.rows[:end+1]
        send=kw.pop("send",Mock(return_value=True))
        return send_double_bottom_candidate(
            self.ledger,seq,now=seq[-1]["start"]+302,
            started=self.start-300,final_status="no_qualified_entry",
            send=send,enabled=True,**kw)

    def test_defense_identified_before_any_breakout(self):
        z=detect_double_bottom(self.rows[:32])
        self.assertEqual(z["status"],"candidate")
        self.assertEqual(z["low1_start"],self.rows[18]["start"])
        self.assertEqual(z["low2_start"],self.rows[29]["start"])
        self.assertTrue(z["volume_softened"])
        self.assertGreater(z["neckline"],z["low2"])
        self.assertIn("정식 진입 알람 아님",format_double_bottom(
            dict(z,stage="defense"),"defense",self.rows[31]))

    def test_four_stages_in_order_then_failure(self):
        sent=Mock(return_value=True)
        self.assertEqual(self.call(31,send=sent),"defense:accepted")
        self.assertEqual(self.call(31,send=sent),"already_reserved")
        self.assertEqual(self.call(32,send=sent),"rebound:accepted")
        self.assertEqual(self.call(33,send=sent),"breakout:accepted")
        self.assertEqual(self.call(34,send=sent),"monitoring_hold")
        self.assertEqual(self.call(35,send=sent),"hold:accepted")
        self.assertEqual(self.call(36,send=sent),"invalidated:accepted")
        self.assertEqual(sent.call_count,5)
        row=self.db.execute("SELECT stage FROM video_double_bottom_stages").fetchone()
        self.assertEqual(row[0],"invalidated")

    def test_restart_does_not_duplicate_messages(self):
        sender=Mock(return_value=True)
        self.assertEqual(self.call(31,send=sender),"defense:accepted")
        new=SimpleNamespace(db=self.db)
        self.assertEqual(send_double_bottom_candidate(new,self.rows[:32],
            now=self.rows[31]["start"]+303,started=self.start-300,
            final_status="no_qualified_entry",send=sender,enabled=True),
            "already_reserved")
        sender.assert_called_once()

    def test_missing_bar_or_future_receipt_blocks_every_stage(self):
        broken=self.rows[:32]
        broken[22]["start"] += 300
        self.assertEqual(detect_double_bottom(broken)["status"],"invalid_source")
        sender=Mock()
        self.assertEqual(send_double_bottom_candidate(self.ledger,broken,
            now=broken[-1]["start"]+302,started=self.start-300,
            final_status="no_qualified_entry",send=sender,enabled=True),
            "invalid_source")
        future=self.rows[:32]
        future[20]["received_at"]=future[-1]["start"]+600
        self.assertEqual(send_double_bottom_candidate(self.ledger,future,
            now=future[-1]["start"]+302,started=self.start-300,
            final_status="no_qualified_entry",send=sender,enabled=True),
            "stale_or_before_start")
        sender.assert_not_called()

    def test_no_replayed_or_formal_duplicate(self):
        sender=Mock()
        seq=self.rows[:32]
        r=send_double_bottom_candidate(self.ledger,seq,
            now=seq[-1]["start"]+350,started=self.start-300,
            final_status="no_qualified_entry",send=sender,enabled=True)
        self.assertEqual(r,"stale_or_before_start")
        self.assertEqual(send_double_bottom_candidate(self.ledger,seq,
            now=seq[-1]["start"]+302,started=self.start-300,
            final_status="telegram_accepted",send=sender,enabled=True),
            "formal_alert_priority")
        self.assertEqual(send_double_bottom_candidate(self.ledger,seq,
            now=seq[-1]["start"]+302,started=self.start-300,
            final_status="no_qualified_entry",send=sender,enabled=False),
            "disabled")
        sender.assert_not_called()

    def test_breakout_must_have_volume_and_continuation(self):
        sender=Mock(return_value=True)
        self.assertEqual(self.call(31,send=sender),"defense:accepted")
        self.assertEqual(self.call(32,send=sender),"rebound:accepted")
        self.rows[33]["volume"]=0.1
        self.assertEqual(self.call(33,send=sender),"breakout_volume_unconfirmed")
        self.assertEqual(sender.call_count,2)

    def test_uncertain_send_is_reserved_and_not_retried(self):
        sender=Mock(side_effect=TimeoutError("no confirmation"))
        self.assertEqual(self.call(31,send=sender),"defense:unconfirmed")
        self.assertEqual(self.call(31,send=sender),"already_reserved")
        sender.assert_called_once()

    def test_wrong_shape_is_rejected(self):
        self.rows[29]["low"]=970.
        self.rows[29]["close"]=975.
        self.rows[29]["high"]=max(self.rows[29]["open"],975.)+1
        self.assertNotEqual(detect_double_bottom(self.rows[:32])["status"],
                            "candidate")


if __name__=="__main__":
    unittest.main()
