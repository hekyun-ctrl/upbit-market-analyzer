"""Closed-sample hypothesis tests: NO actual LBank candles/orders."""
import unittest
import os
import tempfile
from lbank_rsi_research import (
    reversal_hypotheses, neutral_stall, ResearchTracker, NeutralExitStudy)


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



class NeutralExitStudyTests(unittest.TestCase):
    @staticmethod
    def history():
        # RSI begins after its warmup; only the last values align.
        return [83000.] * 17 + [83000., 82990., 82995.], [50., 50., 49., 51., 50.]

    def test_neutral_by_itself_is_symmetric_and_not_an_exit_order(self):
        closes, rsis = self.history()
        tracker = NeutralExitStudy()
        signals = tracker.observe(100, closes, rsis)
        self.assertEqual([x["side"] for x in signals], [1, -1])
        self.assertTrue(all(x["ambiguous"] for x in signals))
        self.assertTrue(all(x["variant"] == "neutral" for x in signals))
        self.assertEqual(tracker.observe(100, closes, rsis), [])

    def test_adverse_followthrough_confirms_long_only(self):
        closes, rsis = self.history()
        tracker = NeutralExitStudy()
        tracker.observe(100, closes, rsis)
        signals = tracker.observe(101, closes[1:] + [82950.], rsis + [48.4])
        confirmed = [x for x in signals if x.get("variant") == "direction_confirm"]
        self.assertEqual(len(confirmed), 1)
        self.assertEqual(confirmed[0]["side"], 1)
        self.assertLess(confirmed[0]["adverse_5m_pct"], -.025)
        self.assertLess(confirmed[0]["adverse_rsi_pts"], -1.)

    def test_completed_15_30_60_minute_horizons_and_false_positive(self):
        closes, rsis = self.history()
        tracker = NeutralExitStudy()
        tracker.observe(100, closes, rsis)
        for age in range(1, 13):
            closes = closes[1:] + [82995. + 70. * age]
            rsis = (rsis + [51. + age])[-15:]
            events = tracker.observe(100 + age, closes, rsis)
            h = [x for x in events
                 if x["event"] == "horizon" and x["start_bucket"] == 100]
            if age in (3, 6, 12):
                self.assertEqual({x["minutes"] for x in h}, {age*5})
                long_move = next(x for x in h if x["side"] == 1)
                self.assertGreater(long_move["signed_hold_move_pct"], 0)
                self.assertLess(long_move["exit_now_advantage_gross_pct"], 0)
                self.assertEqual(long_move["fills"], "unverified")
            else:
                self.assertEqual(h, [])
        self.assertTrue(all(x.bucket != 100 for x in tracker.pending))

    def test_restart_state_persistence_and_no_duplicate_events(self):
        closes, rsis = self.history()
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "neutral.json")
            tracker = NeutralExitStudy(path=path)
            self.assertEqual(len(tracker.observe(100, closes, rsis)), 2)
            resumed = NeutralExitStudy(path=path)
            self.assertEqual(resumed.observe(100, closes, rsis), [])
            ev = resumed.observe(101, closes[1:] + [82950.], rsis + [48.4])
            self.assertTrue(any(x.get("variant") == "direction_confirm" for x in ev))
            restarted = NeutralExitStudy(path=path)
            self.assertEqual(restarted.observe(101, closes, rsis), [])
            self.assertEqual(len(restarted.pending), len(resumed.pending))

    def test_neutral_cooldown_suppresses_repeats(self):
        closes, rsis = self.history()
        tracker = NeutralExitStudy()
        self.assertEqual(len(tracker.observe(100, closes, rsis)), 2)
        for bucket in range(101, 106):
            events = tracker.observe(bucket, closes, rsis)
            self.assertFalse(any(x["event"] == "candidate" and
                                 x["variant"] == "neutral" for x in events))
        events = tracker.observe(106, closes, rsis)
        self.assertEqual(len([x for x in events
                              if x["event"] == "candidate" and
                              x["variant"] == "neutral"]), 2)


if __name__=="__main__":
    unittest.main()
