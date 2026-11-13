"""Three-cause B-short release: independent entry, provenance and projections."""
from copy import deepcopy
import unittest

from lbank_b_target_projection import project_short_target
from lbank_b_retest_research import trend_priority
from lbank_b_momentum_trial import (
    evaluate_five_break, evaluate_trial, quote_check, TEST_RR,
)
from test_lbank_b_momentum_trial import MomentumTests

class ThreeCauseTests(unittest.TestCase):
    def five_break(self):
        rows=[]
        for i in range(130):
            t=300*(i+1)
            close=74800-20*i
            rows.append(dict(start=t,period=300,open=close+2,
                    high=close+20,low=close-90,close=close,
                    volume=10.,quality="stream_closed",
                    received_at=t+301))
        support=min(r["low"] for r in rows[-7:-1])
        rows[-1].update(open=support+40,high=support+45,
                low=support-101,close=support-100,volume=40)
        return rows

    def test_live_ready_74_fives_no_96_gate(self):
        m,f=MomentumTests().valid_candidate()
        self.assertEqual(trend_priority(f[-74:])["status"],"ready")
        full=evaluate_trial(m,f)
        short=evaluate_trial(m,f[-74:])
        self.assertEqual(full["status"],"test_candidate")
        self.assertEqual(short["status"],"test_candidate")
        self.assertEqual(full["target"],short["target"])

    def test_independent_five_first_break_without_rsi_confirmation(self):
        f=self.five_break()
        z=evaluate_five_break(f)
        self.assertEqual(z["status"],"test_candidate")
        self.assertEqual(z["bar_seconds"],300)
        self.assertEqual(z["direction"],"short")
        self.assertEqual(z["target_basis"],
                         "bounded_volatility_projection_NOT_known_support")
        self.assertGreaterEqual(z["net_rr"],2.0)
        self.assertLess(z["target"],z["signal_close"])
        self.assertGreater(z["stop"],z["signal_close"])
        t=f[-1]["start"]+302
        r=quote_check(z,now=t,clock=lambda:t,
                      get_book=lambda:(z["signal_close"]-.1,
                                       z["signal_close"]))
        self.assertEqual(r["status"],"quote_qualified_test")

    def test_five_never_advises_on_missing_candles_or_future(self):
        f=self.five_break()
        self.assertEqual(evaluate_five_break(f[-72:])["status"],
                         "test_candidate")
        broken=deepcopy(f[-72:])
        broken[-20]["start"]+=300
        self.assertEqual(evaluate_five_break(broken)["status"],
                         "insufficient_five_history")
        forming=deepcopy(f[-72:])
        forming[-1]["quality"]="forming"
        self.assertEqual(evaluate_five_break(forming)["status"],
                         "insufficient_five_history")

    def test_invalid_wide_or_unbounded_target_fails_closed(self):
        self.assertEqual(project_short_target(100000.,100050.,100.)["status"],
                         "projected_target_candidate")
        self.assertEqual(project_short_target(100000.,100500.,1.)["status"],
                         "projection_outside_volatility_cap")
        self.assertEqual(project_short_target(100000.,100000.,100.)["status"],
                         "invalid_projection_inputs")
        self.assertEqual(project_short_target(100000.,100500.,-1.)["status"],
                         "invalid_projection_inputs")

    def test_native_quote_validates_5m_real_clock_not_1m_clock(self):
        z=evaluate_five_break(self.five_break())
        start=z["signal_start"]
        bid=z["signal_close"]-.1
        self.assertEqual(quote_check(z,now=start+62,
                         clock=lambda:start+62,
                         get_book=lambda:(bid,bid+.1))["status"],
                         "stale_quote_window")
        self.assertEqual(quote_check(z,now=start+330,
                         clock=lambda:start+330,
                         get_book=lambda:(bid,bid+.1))["status"],
                         "stale_quote_window")

    def test_strong_bearish_candle_with_too_wide_risk_is_not_forced(self):
        f=self.five_break()
        f[-1]["low"]=f[-1]["close"]-10
        f[-1]["high"]=f[-1]["open"]+400
        z=evaluate_five_break(f)
        self.assertNotEqual(z["status"],"test_candidate")

if __name__=="__main__":
    unittest.main()
