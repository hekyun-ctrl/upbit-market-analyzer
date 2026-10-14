"""Closed-sample hypothesis tests: NO actual LBank candles/orders."""
import unittest
from lbank_rsi_research import (
    reversal_hypotheses, neutral_stall, ResearchTracker)


def series(a,b,c, ra,rb,rc):
    return ([83000.]*17+[a,b,c], [50.]*14+[ra]*4+[ra,rb,rc])


class RSIResearchTests(unittest.TestCase):
    def test_basic_long_and_short_require_two_closed_turns(self):
        closes, rsis = series(82500.,82600.,82700.,28.,31.,35.)
        v = reversal_hypotheses(closes,rsis)
        self.assertEqual(v["long"]["zone"],"basic")
        self.assertTrue(v["long"]["two_closed_turns"])
        self.assertIsNone(v["short"])
        closes,rsis = series(83500.,83400.,83300.,72.,68.,64.)
        v = reversal_hypotheses(closes,rsis)
        self.assertEqual(v["short"]["zone"],"basic")
        self.assertTrue(v["short"]["two_closed_turns"])

    def test_near_miss_is_separate_non_executable_hypothesis(self):
        closes,rsis=series(82500.,82600.,82700.,33.,36.,40.)
        v=reversal_hypotheses(closes,rsis)
        self.assertEqual(v["long"]["zone"],"near_miss")
        self.assertTrue(v["long"]["two_closed_turns"])
        closes,rsis=series(83500.,83400.,83300.,67.,63.,59.)
        v=reversal_hypotheses(closes,rsis)
        self.assertEqual(v["short"]["zone"],"near_miss")
        self.assertTrue(v["short"]["two_closed_turns"])

    def test_one_turn_and_no_threshold_do_not_create_two_turn_signal(self):
        c,r=series(82500.,82400.,82500.,28.,26.,29.)
        self.assertFalse(reversal_hypotheses(c,r)["long"]["two_closed_turns"])
        c,r=series(82500.,82600.,82700.,40.,43.,47.)
        self.assertIsNone(reversal_hypotheses(c,r)["long"])

    def test_neutral_stall_needs_two_neutral_and_flat_price_and_rsi(self):
        c=[83000.,82990.,82995.]
        r=[49.,51.,50.]
        self.assertTrue(neutral_stall(c,r,1))
        self.assertTrue(neutral_stall(c,r,-1))
        self.assertFalse(neutral_stall([83000.,83200.,83400.],r,1))
        self.assertFalse(neutral_stall(c,[49.,50.,65.],1))
        self.assertFalse(neutral_stall(c,[35.,41.,42.],1))

    def test_forward_study_waits_for_future_observation(self):
        c,r=series(82500.,82600.,82700.,28.,31.,35.)
        tracker=ResearchTracker()
        v,events=tracker.observe(100,82700.,c,r)
        self.assertEqual([x["event"] for x in events],["candidate"])
        for b in (101,102):
            _,events=tracker.observe(b,82700.,[82700.]*20,[50.]*17)
            self.assertEqual(events,[])
        _,events=tracker.observe(103,82800.,[82800.]*20,[50.]*17)
        horizon=[e for e in events if e["event"]=="horizon"]
        self.assertEqual(len(horizon),1)
        self.assertEqual(horizon[0]["minutes"],15)
        self.assertLess(horizon[0]["net_taker_only_pct"],horizon[0]["gross_pct"])
        for b in range(104,113):
            _,events=tracker.observe(b,82800.,[82800.]*20,[50.]*17)
        self.assertEqual(tracker.pending,[])


if __name__=="__main__":
    unittest.main()
