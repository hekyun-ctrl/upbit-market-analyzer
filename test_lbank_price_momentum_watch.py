
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

    def test_five_minute_staircase_after_early_impulse(self):
        # Two strong minutes followed by three small positive closes.
        # The last 3m move is too small for the previous impulse-only rule.
        data=[]; p=64000.
        for i in range(21):
            t=self.base+i*60
            change={16:80.,17:80.,18:4.,19:4.,20:10.}.get(i,0.)
            op=p; p+=change
            data.append(dict(period=60,start=t,open=op,high=max(op,p)+1.,
                low=min(op,p)-1.,close=p,volume=1. if i==20 else 20.,
                quality="stream_closed",received_at=t+62))
        x=evaluate_minute_watch(data)
        self.assertEqual(x["status"],"watch",x)
        self.assertEqual((x["direction"],x["trigger_type"]),
                         ("up","five_minute_staircase"))
        self.assertEqual(x["movement"],178.)
        sender=Mock(return_value=True); now=data[-1]["start"]+64
        delivered=send_price_watch(self.ledger,data,period=60,now=now,
                                    started=now-600,send=sender,enabled=True)
        self.assertEqual(delivered,"up:accepted")
        self.assertIn("five_minute_staircase",sender.call_args.args[0])

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

    def test_same_minute_prealert_suppresses_duplicate_watch(self):
        rows=self.minutes(); now=rows[-1]["start"]+64
        with self.db:
            self.db.execute("""CREATE TABLE video_one_minute_notice
                (bar_start INTEGER, direction TEXT, status TEXT)""")
            self.db.execute("INSERT INTO video_one_minute_notice VALUES(?,?,?)",
                            (rows[-1]["start"],"up","accepted"))
        sender=Mock(return_value=True)
        self.assertEqual(send_price_watch(self.ledger,rows,period=60,
            now=now,started=now-600,send=sender,enabled=True),
            "existing_minute_prealert")
        sender.assert_not_called()

    def test_cooldown_shared_between_1m_and_5m(self):
        minutes=self.minutes();now=minutes[-1]["start"]+65
        sender=Mock(return_value=True)
        self.assertEqual(send_price_watch(
            self.ledger,minutes,period=60,now=now,started=now-600,
            send=sender,enabled=True),"up:accepted")
        five=self.fives()
        # Place 5m event immediately after the minute event, keeping its
        # original valid source receipts.
        next_5m_close=((minutes[-1]["start"]+60+299)//300)*300
        shift=next_5m_close-(five[-1]["start"]+300)
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


    def test_real_20261118_1415_price_watch(self):
        # 21 historical native 5m OHLCV closes ending 14:15 KST.
        # Receipts +2s are synthetic for deterministic offline replay only.
        values=[[1794972600,64436,64536.5,64435.9,64500.1,15.046100000000001],[1794972900,64500.1,64544.3,64466.3,64484.1,2.7025],[1794973200,64484.1,64528.4,64466.3,64494.9,1.6206],[1794973500,64494.9,64527.8,64462.8,64505,45.1146],[1794973800,64505,64547.8,64483.5,64483.6,23.720100000000002],[1794974100,64483.6,64488.5,64424.7,64424.7,6.5197],[1794974400,64424.7,64424.7,64350.2,64409.8,83.6357],[1794974700,64409.8,64422.5,64360,64385.1,32.1552],[1794975000,64385.1,64485,64369.1,64379.7,17.5487],[1794975300,64379.7,64560,64379.6,64550.4,0.7715],[1794975600,64550.4,64664,64480.1,64502,20.020599999999998],[1794975900,64502,64559.7,64485.7,64541.3,34.3459],[1794976200,64541.3,64566.9,64470.1,64480.5,7.2556],[1794976500,64480.5,64480.5,64420,64441.7,36.7791],[1794976800,64441.7,64487.8,64325.4,64325.4,16.399900000000002],[1794977100,64325.4,64400,64281.2,64395.8,11.6913],[1794977400,64395.8,64450,64370.5,64403.4,31.7638],[1794977700,64403.4,64448.4,64358,64395.2,2.0786],[1794978000,64395.1,64395.1,64307.6,64360.1,1.8838000000000001],[1794978300,64360.1,64410.2,64333,64410.2,28.973],[1794978600,64410.2,64577,64407.2,64572.6,6.0635]]
        rows=[dict(start=t,period=300,open=o,high=h,low=l,
                   close=c,volume=v,quality="stream_closed",received_at=t+302)
              for t,o,h,l,c,v in values]
        watch=evaluate_five_watch(rows)
        self.assertEqual((watch["status"],watch["direction"]),
                         ("watch","up"))
        from lbank_early_breakout_audit import evaluate_early_breakout
        old=evaluate_early_breakout(rows)
        self.assertNotEqual(old["status"],"candidate")

    def test_real_20261118_1650_price_watch(self):
        # 21 historical native 5m OHLCV closes ending 16:50 KST.
        # Receipts +2s are synthetic for deterministic offline replay only.
        values=[[1794981900,64630.1,64707.4,64608.6,64664.5,8.2393],[1794982200,64664.5,64682,64610.9,64682,8.2477],[1794982500,64682,64700,64617.6,64700,8.7643],[1794982800,64700,64706.9,64635.5,64638,2.0927],[1794983100,64638,64691,64636,64655,15.846499999999999],[1794983400,64655,64668.5,64600.1,64654.3,26.1658],[1794983700,64654.3,64667,64610.9,64648.3,1.6831],[1794984000,64648.3,64659.2,64560,64624.8,12.578299999999999],[1794984300,64624.8,64661.1,64601.8,64610.2,30.1208],[1794984600,64610.2,64610.2,64497.8,64563.7,26.978199999999998],[1794984900,64563.7,64640,64563.7,64578.9,3.9396],[1794985200,64578.9,64698.5,64534.2,64657.5,18.2578],[1794985500,64657.5,64753,64657.4,64737.5,16.4512],[1794985800,64737.5,64756.3,64713.6,64756.3,1.846],[1794986100,64756.3,64774.2,64685.6,64713.6,18.9821],[1794986400,64713.6,64747.5,64708.5,64742.8,35.9219],[1794986700,64742.8,64742.9,64675.4,64711,26.384],[1794987000,64711,64718.6,64609.5,64611,6.3847000000000005],[1794987300,64611,64625.4,64448.2,64518.2,37.0147],[1794987600,64518.2,64604.3,64504.9,64565.5,47.6528],[1794987900,64565.5,64731.5,64560,64731.3,4.0287999999999995]]
        rows=[dict(start=t,period=300,open=o,high=h,low=l,
                   close=c,volume=v,quality="stream_closed",received_at=t+302)
              for t,o,h,l,c,v in values]
        watch=evaluate_five_watch(rows)
        self.assertEqual((watch["status"],watch["direction"]),
                         ("watch","up"))
        from lbank_early_breakout_audit import evaluate_early_breakout
        old=evaluate_early_breakout(rows)
        self.assertNotEqual(old["status"],"candidate")

    def test_real_20261118_1345_price_watch(self):
        # 21 historical native 5m OHLCV closes ending 13:45 KST.
        # Receipts +2s are synthetic for deterministic offline replay only.
        values=[[1794970800,64295.4,64438.5,64290.6,64415.9,21.0182],[1794971100,64415.9,64438.4,64351.6,64418.9,0.6272],[1794971400,64418.9,64487.6,64402.3,64459.9,17.3225],[1794971700,64459.9,64460,64372,64391.9,14.6012],[1794972000,64391.9,64404.5,64310,64393.9,3.2809999999999997],[1794972300,64393.9,64436,64388.1,64436,6.805],[1794972600,64436,64536.5,64435.9,64500.1,15.046100000000001],[1794972900,64500.1,64544.3,64466.3,64484.1,2.7025],[1794973200,64484.1,64528.4,64466.3,64494.9,1.6206],[1794973500,64494.9,64527.8,64462.8,64505,45.1146],[1794973800,64505,64547.8,64483.5,64483.6,23.720100000000002],[1794974100,64483.6,64488.5,64424.7,64424.7,6.5197],[1794974400,64424.7,64424.7,64350.2,64409.8,83.6357],[1794974700,64409.8,64422.5,64360,64385.1,32.1552],[1794975000,64385.1,64485,64369.1,64379.7,17.5487],[1794975300,64379.7,64560,64379.6,64550.4,0.7715],[1794975600,64550.4,64664,64480.1,64502,20.020599999999998],[1794975900,64502,64559.7,64485.7,64541.3,34.3459],[1794976200,64541.3,64566.9,64470.1,64480.5,7.2556],[1794976500,64480.5,64480.5,64420,64441.7,36.7791],[1794976800,64441.7,64487.8,64325.4,64325.4,16.399900000000002]]
        rows=[dict(start=t,period=300,open=o,high=h,low=l,
                   close=c,volume=v,quality="stream_closed",received_at=t+302)
              for t,o,h,l,c,v in values]
        watch=evaluate_five_watch(rows)
        self.assertEqual((watch["status"],watch["direction"]),
                         ("watch","down"))
        from lbank_early_breakout_audit import evaluate_early_breakout
        old=evaluate_early_breakout(rows)
        self.assertNotEqual(old["status"],"candidate")

    def test_real_20261118_1640_price_watch(self):
        # 21 historical native 5m OHLCV closes ending 16:40 KST.
        # Receipts +2s are synthetic for deterministic offline replay only.
        values=[[1794981300,64651.5,64670.6,64635,64651.2,1.4824],[1794981600,64651.3,64651.3,64561.6,64630.1,1.5609],[1794981900,64630.1,64707.4,64608.6,64664.5,8.2393],[1794982200,64664.5,64682,64610.9,64682,8.2477],[1794982500,64682,64700,64617.6,64700,8.7643],[1794982800,64700,64706.9,64635.5,64638,2.0927],[1794983100,64638,64691,64636,64655,15.846499999999999],[1794983400,64655,64668.5,64600.1,64654.3,26.1658],[1794983700,64654.3,64667,64610.9,64648.3,1.6831],[1794984000,64648.3,64659.2,64560,64624.8,12.578299999999999],[1794984300,64624.8,64661.1,64601.8,64610.2,30.1208],[1794984600,64610.2,64610.2,64497.8,64563.7,26.978199999999998],[1794984900,64563.7,64640,64563.7,64578.9,3.9396],[1794985200,64578.9,64698.5,64534.2,64657.5,18.2578],[1794985500,64657.5,64753,64657.4,64737.5,16.4512],[1794985800,64737.5,64756.3,64713.6,64756.3,1.846],[1794986100,64756.3,64774.2,64685.6,64713.6,18.9821],[1794986400,64713.6,64747.5,64708.5,64742.8,35.9219],[1794986700,64742.8,64742.9,64675.4,64711,26.384],[1794987000,64711,64718.6,64609.5,64611,6.3847000000000005],[1794987300,64611,64625.4,64448.2,64518.2,37.0147]]
        rows=[dict(start=t,period=300,open=o,high=h,low=l,
                   close=c,volume=v,quality="stream_closed",received_at=t+302)
              for t,o,h,l,c,v in values]
        watch=evaluate_five_watch(rows)
        self.assertEqual((watch["status"],watch["direction"]),
                         ("watch","down"))
        from lbank_early_breakout_audit import evaluate_early_breakout
        old=evaluate_early_breakout(rows)
        self.assertEqual(old["status"],"candidate")

if __name__ == "__main__":
    unittest.main()
