"""Regression suite for independent, one-minute early heads-up notifications."""
import sqlite3
import unittest
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock
from lbank_one_minute_prealert import (
    evaluate_one_minute_move, format_one_minute_prealert,
    send_one_minute_prealert,
)


class OneMinutePrealertTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.ledger = SimpleNamespace(db=self.db)
        self.base = 1794600000 // 60 * 60

    def bars(self, direction="down", shift=0):
        rows=[]
        for i in range(21):
            start=self.base+shift+i*60
            o,c,h,l,volume=100.,100.,100.1,99.9,10.
            if i==20:
                if direction=="down":
                    o,c,h,l,volume=100.,99.5,100.1,99.45,20.
                if direction=="up":
                    o,c,h,l,volume=100.,100.5,100.55,99.9,20.
                if direction=="no_volume":
                    o,c,h,l,volume=100.,99.5,100.1,99.45,10.
                if direction=="weak":
                    o,c,h,l,volume=100.,99.95,100.1,99.9,20.
            rows.append(dict(start=start,period=60,open=o,high=h,low=l,
                             close=c,volume=volume,quality="stream_closed",
                             received_at=start+61))
        return rows

    def send(self,bars,sender=None,**kw):
        sender = sender or Mock(return_value=True)
        now=bars[-1]["start"]+62
        status=send_one_minute_prealert(
            self.ledger,bars,now=now,started=bars[0]["start"]-60,
            send=sender,enabled=True,clock=lambda:now+0.5,**kw)
        return status,sender

    def test_first_one_minute_break_not_blocked_by_five_minute_warmup(self):
        for direction in ("down","up"):
            with self.subTest(direction=direction):
                z=evaluate_one_minute_move(self.bars(direction))
                self.assertEqual(z["status"],"candidate")
                self.assertEqual(z["direction"],direction)
                self.assertGreaterEqual(z["volume_multiple"],2.)
                self.assertEqual(z["start"],self.base+20*60)

    def test_notice_explicitly_not_entry_or_order(self):
        a=self.bars()
        sender=Mock(return_value=True)
        state,_=self.send(a,sender)
        self.assertEqual(state,"down:accepted")
        message=sender.call_args.args[0]
        self.assertIn("1분봉 조기",message)
        self.assertIn("정식 진입 신호 아님",message)
        self.assertIn("서버 판정",message)
        self.assertNotIn("손절가:",message)
        self.assertNotIn("목표가:",message)

    def test_quiet_candle_and_low_volume_do_not_page(self):
        sender=Mock(return_value=True)
        self.assertEqual(self.send(self.bars("weak"),sender)[0],"move_too_small")
        self.assertEqual(self.send(self.bars("no_volume"),sender)[0],
                         "volume_below_threshold")
        sender.assert_not_called()

    def test_reservation_and_restart_dedupe(self):
        a=self.bars()
        sender=Mock(return_value=True)
        self.assertEqual(self.send(a,sender)[0],"down:accepted")
        self.assertEqual(self.send(a,sender)[0],"cooldown")
        other=SimpleNamespace(db=self.db)
        t=a[-1]["start"]+63
        self.assertEqual(send_one_minute_prealert(
            other,a,now=t,started=self.base-60,
            send=sender,enabled=True,clock=lambda:t),"cooldown")
        sender.assert_called_once()
        self.assertEqual(self.db.execute(
            "SELECT count(*) FROM video_one_minute_notice").fetchone()[0],1)

    def test_after_cooldown_and_independent_direction(self):
        sender=Mock(return_value=True)
        self.assertEqual(self.send(self.bars(),sender)[0],"down:accepted")
        self.assertEqual(self.send(self.bars("up",shift=60),sender)[0],"up:accepted")
        self.assertEqual(self.send(self.bars("down",shift=16*60),sender)[0],"down:accepted")
        self.assertEqual(sender.call_count,3)

    def test_stale_before_start_and_suppression(self):
        a=self.bars(); t=a[-1]["start"]+62; sender=Mock()
        args=dict(send=sender,enabled=True,clock=lambda:t)
        self.assertEqual(send_one_minute_prealert(
            self.ledger,a,now=t,started=t,**args),"before_worker_start")
        self.assertEqual(send_one_minute_prealert(
            self.ledger,a,now=t+60,started=0,**args),"stale_or_unverified")
        self.assertEqual(send_one_minute_prealert(
            self.ledger,a,now=t,started=0,suppress=True,**args),
            "higher_priority_alert")
        sender.assert_not_called()

    def test_invalid_gap_future_receipt_and_forming_candle_suppressed(self):
        original=self.bars()
        sender=Mock()
        for kind in ("gap","receipt","forming"):
            a=deepcopy(original)
            if kind=="gap":
                a[18]["start"]+=60
            elif kind=="receipt":
                a[12]["received_at"]=a[-1]["start"]+120
            else:
                a[-1]["quality"]="forming"
            status,_=self.send(a,sender)
            self.assertNotIn("accepted",status)
        sender.assert_not_called()

    def test_telegram_timeout_is_uncertain_and_never_retried(self):
        a=self.bars();sender=Mock(side_effect=TimeoutError("timeout"))
        self.assertEqual(self.send(a,sender)[0],"down:unconfirmed")
        self.assertEqual(self.send(a,sender)[0],"cooldown")
        sender.assert_called_once()

    def test_timestamp_and_timing_persisted(self):
        a=self.bars();t=a[-1]["start"]+62
        clk=Mock(side_effect=[t,t+1.5])
        sender=Mock(return_value=True)
        self.assertEqual(send_one_minute_prealert(
            self.ledger,a,now=t,started=0,
            send=sender,enabled=True,clock=clk),"down:accepted")
        record=self.db.execute("SELECT status,sent_at FROM video_one_minute_notice").fetchone()
        self.assertEqual(record,("accepted",t+1.5))
        self.assertEqual(clk.call_count,2)

    def test_no_closed_moving_bar_replay(self):
        a=self.bars();a[-1]["received_at"]=a[-1]["start"]+59
        sender=Mock()
        self.assertEqual(self.send(a,sender)[0],"stale_or_unverified")
        sender.assert_not_called()


if __name__=="__main__":
    unittest.main()
