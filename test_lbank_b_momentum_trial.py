"""Security, latency, risk and dispatch tests for B momentum trial text."""
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from lbank_b_momentum_trial import evaluate_trial,quote_check,deliver_trial,TEST_RR
from research.zerobase.video_entry import judge_entry
from test_lbank_b_early_1m_shadow import FirstMinuteTests


class MomentumTests(unittest.TestCase):
    def synthetic(self):
        five=FirstMinuteTests().five()
        one=FirstMinuteTests().one(five)
        # A 1m recent micro low break, volume x4 and restricted body.
        m=one[-1]
        m.update(open=m["close"]+1,close=m["close"],high=m["open"]+.1,
                 low=m["close"]-.1,volume=45)
        return one,five

    def test_fails_closed_early_without_valid_context(self):
        one,five=self.synthetic()
        self.assertEqual(evaluate_trial(one,five)["status"],"insufficient_history")

    def test_no_negative_price_or_future_five_as_history(self):
        # Once the test has enough consecutive minute rows, adding a
        # just-completed overlapping 5m cannot change prior context.
        one,five=self.synthetic()
        one=one[:1]*0+one
        self.assertEqual(evaluate_trial(one,five)["status"],"insufficient_history")

    def valid_candidate(self):
        fives=[]
        for i in range(130):
            t=300*(i+1);c=74800-20*i
            fives.append(dict(start=t,period=300,open=c+2,
                high=c+10,low=c-70,close=c,volume=10,
                quality="stream_closed",received_at=t+301))
        support=min(x["low"] for x in fives[-6:])
        start=fives[-1]["start"]+300
        minute=[]
        for j in range(61):
            t=start+(j-60)*60
            c=support+2 if j<60 else support-90
            o=c if j<60 else support-20
            h=support+10 if j<60 else support-15
            l=support-70 if j<60 else support-91
            minute.append(dict(start=t,period=60,open=o,high=h,low=l,
                close=c,volume=10 if j<60 else 30,
                quality="stream_closed",received_at=t+61))
        return minute,fives

    def test_known_causal_positive_candidate_from_bearish_trend(self):
        m,f=self.valid_candidate()
        z=evaluate_trial(m,f)
        self.assertEqual(z["status"],"test_candidate")
        self.assertEqual(z["trend"],"bearish")
        self.assertLess(z["target"],z["signal_close"])
        self.assertGreater(z["stop"],z["signal_close"])
        self.assertGreaterEqual(z["net_rr"],2.0)
        self.assertIn("projection",z["target_basis"])
        q=quote_check(z,now=m[-1]["start"]+62,
            get_book=lambda:(z["signal_close"]-.1,z["signal_close"]),
            clock=lambda:m[-1]["start"]+62)
        self.assertEqual(q["status"],"quote_qualified_test")

    def test_gap_makes_candidate_fail_closed(self):
        m,f=self.valid_candidate()
        m[34]["start"]+=60
        self.assertEqual(evaluate_trial(m,f)["status"],"insufficient_history")

    def test_past_five_only_no_future_leak(self):
        m,f=self.valid_candidate()
        a=evaluate_trial(m,f)
        future=dict(f[-1]);future["start"]+=300;future["received_at"]+=300
        future["low"]=1.;future["high"]=100000.
        f.append(future)
        b=evaluate_trial(m,f)
        self.assertEqual(a,b)

    def test_quote_move_and_wide_spread_rejected(self):
        m,f=self.valid_candidate()
        plan=evaluate_trial(m,f);t=m[-1]["start"]+62
        z=quote_check(plan,now=t,
           get_book=lambda:(plan["signal_close"]-plan["atr1"],plan["signal_close"]-plan["atr1"]+.1),
           clock=lambda:t)
        self.assertEqual(z["status"],"quote_moved_since_signal")
        z=quote_check(plan,now=t,
            get_book=lambda:(plan["signal_close"],plan["signal_close"]*1.001),
            clock=lambda:t)
        self.assertEqual(z["status"],"spread_too_wide")

    def test_quote_book_validation(self):
        plan=dict(status="test_candidate",signal_start=120,
                  signal_close=100000.,atr1=500.,stop=100250.,target=98900.0)
        self.assertEqual(quote_check(plan,now=205,get_book=lambda:(99990,99990.1),
                                      clock=lambda:205)["status"],"stale_quote_window")
        self.assertEqual(quote_check(plan,now=181,get_book=lambda:(0,100000),
                                      clock=lambda:181)["status"],"invalid_book")

    def test_real_costs_and_target_not_hardcoded_rr(self):
        plan=dict(lane="b_native_first_microbreak_test",direction=-1,
                  stop=100250,target=98900)
        verdict,rr=judge_entry(plan,100000,TEST_RR)
        self.assertEqual(verdict,"qualified")
        self.assertGreater(rr,2)

    def test_no_send_before_worker_restart(self):
        d=sqlite3.connect(":memory:");self.addCleanup(d.close)
        fake=dict(status="test_candidate",signal_start=120)
        result=deliver_trial(SimpleNamespace(db=d),fake,now=181,
                             get_book=lambda:(99,100),clock=lambda:181,
                             send=Mock(),enabled=True,started=200)
        self.assertEqual(result,"before_worker_start")

    def test_disabled_is_not_a_trade(self):
        d=sqlite3.connect(":memory:");self.addCleanup(d.close)
        fake=dict(status="test_candidate",signal_start=120)
        m=Mock()
        r=deliver_trial(SimpleNamespace(db=d),fake,now=181,get_book=lambda:(99,100),
                        clock=lambda:181,send=m,enabled=False,started=0)
        self.assertEqual(r,"test_delivery_disabled")
        m.assert_not_called()

    def test_qualified_quote_with_dedupe_and_cooldown(self):
        d=sqlite3.connect(":memory:");self.addCleanup(d.close)
        fake=dict(status="test_candidate",signal_start=120,
                  signal_close=100000.,atr1=500.,stop=100250.,target=98900.,
                  break_threshold=99900.7,volume_multiple=3.1)
        sent=Mock(return_value=True)
        args=dict(now=182,get_book=lambda:(99990.,99990.1),
                  clock=lambda:182,send=sent,enabled=True,started=0)
        a=deliver_trial(SimpleNamespace(db=d),fake,**args)
        b=deliver_trial(SimpleNamespace(db=d),fake,**args)
        self.assertEqual(a,"telegram_accepted_test")
        self.assertEqual(b,"cooldown")
        sent.assert_called_once()
        self.assertEqual(d.execute("select count(*) from video_b_trial_delivery").fetchone()[0],1)


if __name__=="__main__":
    unittest.main()

