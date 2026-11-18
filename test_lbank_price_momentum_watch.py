
"""Fast-observation and entry-screen separation regression tests."""
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from lbank_price_momentum_watch import (
    evaluate_minute_watch, evaluate_five_watch, send_price_watch,
)


class PriceMomentumWatchTests(unittest.TestCase):
    def setUp(self):
        self.db=sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.ledger=SimpleNamespace(db=self.db)
        self.base=1794800000//300*300

    def fives(self, volume=1.):
        data=[]
        for i in range(21):
            t=self.base+i*300
            if i==20:
                o,h,l,c,v=64000.,64210.,63990.,64200.,volume
            else:
                o,h,l,c,v=64000.,64010.,63990.,64000.,20.
            data.append(dict(period=300,start=t,open=o,high=h,low=l,
                close=c,volume=v,quality="stream_closed",
                received_at=t+302))
        return data

    def minutes(self, volume=1.):
        data=[]; t=self.base
        for i in range(21):
            op=64000.;hi=64010.;lo=63990.;c=64000.;v=20.
            if i==18: op,hi,lo,c,v=64000.,64035.,63995.,64030.,20.
            if i==19: op,hi,lo,c,v=64030.,64075.,64025.,64070.,20.
            if i==20: op,hi,lo,c,v=64070.,64140.,64065.,64130.,volume
            data.append(dict(period=60,start=t+i*60,open=op,high=hi,
                low=lo,close=c,volume=v,quality="stream_closed",
                received_at=t+i*60+62))
        return data

    def test_low_volume_5m_warning_independent_of_entry_screen(self):
        rows=self.fives(volume=1.)
        x=evaluate_five_watch(rows)
        self.assertEqual((x["status"],x["direction"]),("watch","up"))
        self.assertTrue(x["volume_warning"])
        sender=Mock(return_value=True)
        now=rows[-1]["start"]+304
        status=send_price_watch(self.ledger,rows,period=300,now=now,
            started=now-300,send=sender,enabled=True)
        self.assertEqual(status,"up:accepted")
        msg=sender.call_args.args[0]
        self.assertIn("빠른 가격 움직임 관찰",msg)
        self.assertIn("매매 진입 적격 미확정",msg)
        self.assertIn("낮은 거래량",msg)
        self.assertIn("정식 진입 신호 아님",msg)
        self.assertIn("자동주문 없음",msg)

    def test_1m_three_minute_acceleration_even_if_low_volume(self):
        rows=self.minutes()
        x=evaluate_minute_watch(rows)
        self.assertEqual(x["status"],"watch")
        self.assertEqual(x["direction"],"up")
        self.assertTrue(x["volume_warning"])

    def test_no_open_or_stale_candle_sent(self):
        rows=self.fives();sender=Mock()
        t=rows[-1]["start"]+304
        rows[-1]["received_at"]=rows[-1]["start"]+299
        self.assertEqual(send_price_watch(self.ledger,rows,period=300,
           now=t,started=t-300,send=sender,enabled=True),
           "stale_or_before_start")
        sender.assert_not_called()

    def test_no_gap_or_invalid_ohlc(self):
        rows=self.fives();rows[-7]["start"]+=300
        self.assertEqual(evaluate_five_watch(rows)["status"],"invalid_source")
        rows=self.minutes();rows[-4]["high"]=1.
        self.assertEqual(evaluate_minute_watch(rows)["status"],"invalid_source")

    def test_quiet_market_rejected(self):
        rows=self.fives();rows[-1].update(
            open=64000.,close=64005.,high=64010.,low=63990.)
        self.assertEqual(evaluate_five_watch(rows)["status"],
                         "no_fast_price_structure")
        rows=self.minutes();rows[-1].update(
            open=64070.,close=64070.,high=64075.,low=64065.)
        self.assertEqual(evaluate_minute_watch(rows)["status"],
                         "no_fast_price_structure")

    def test_cooldown_shared_between_1m_and_5m(self):
        minutes=self.minutes();now=minutes[-1]["start"]+65
        sender=Mock(return_value=True)
        self.assertEqual(send_price_watch(
            self.ledger,minutes,period=60,now=now,started=now-600,
            send=sender,enabled=True),"up:accepted")
        five=self.fives()
        # Place 5m event immediately after the minute event, keeping its
        # original valid source receipts.
        shift=(minutes[-1]["start"]+60)-(five[-1]["start"]+300)
        for row in five:
            row["start"]+=shift
            row["received_at"]+=shift
        five_now=five[-1]["start"]+304
        self.assertEqual(send_price_watch(self.ledger,five,period=300,
            now=five_now,started=five_now-300,send=sender,enabled=True),
            "cooldown")
        sender.assert_called_once()

    def test_same_bar_and_network_timeout_not_retried(self):
        rows=self.fives()
        sender=Mock(side_effect=TimeoutError("delivery"))
        now=rows[-1]["start"]+303
        status=send_price_watch(self.ledger,rows,period=300,now=now,
            started=now-300,send=sender,enabled=True)
        self.assertEqual(status,"up:unconfirmed_no_retry")
        status=send_price_watch(self.ledger,rows,period=300,now=now,
            started=now-300,send=sender,enabled=True)
        self.assertEqual(status,"cooldown")
        sender.assert_called_once()

    def test_disabled_does_not_send(self):
        rows=self.fives();sender=Mock()
        now=rows[-1]["start"]+303
        self.assertEqual(send_price_watch(self.ledger,rows,period=300,
            now=now,started=0,send=sender,enabled=False),"disabled")
        sender.assert_not_called()

    def test_formal_lock_remains_unchanged(self):
        from lbank_loss_review_guard import entry_delivery_allowed
        self.assertFalse(entry_delivery_allowed())

    def test_existing_1m_prealert_not_overwritten(self):
        from lbank_one_minute_prealert import evaluate_one_minute_move
        # Its 1.3x threshold is intentionally unchanged.
        rows=self.minutes(volume=1.)
        self.assertEqual(evaluate_one_minute_move(rows)["status"],
                         "volume_below_threshold")

    def test_25m_checkin_mentions_a_real_early_watch(self):
        from lbank_five_bar_checkin import format_checkin
        report=dict(source="verified",decision="진입 보류",
           priority="롱 관찰",opening=64000.,price=64130.,
           change_pct=.2,move="상승",range_high=64140.,range_low=63990.,
           bars=21,trend="상위봉 미확인",review=None,
           recent_fast_watch="상승(1분봉)",reason="호가/목표 미검증")
        msg=format_checkin(self.base,report)
        self.assertIn("최근 25분 조기 가격 움직임",msg)
        self.assertIn("진입 승인은 별도",msg)


if __name__ == "__main__":
    unittest.main()
