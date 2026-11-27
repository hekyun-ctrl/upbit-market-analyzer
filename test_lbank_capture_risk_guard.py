"""Decision-evidence regression cases; no trade fill or return claims."""
import unittest
from lbank_capture_risk_guard import POLICY, entry_guard, caution_kind


def case(entry, stop, *, higher=True, room="unknown", barrier=None,
         direction=-1, retry=False, fresh=False, strategy="trend_retest",
         dominant=False, breakout_followthrough=False):
    d = direction
    watch = dict(native_watch_1m_start=60, price=entry-d*20, strategy=strategy,
                 level=(entry-d*40 if strategy=="breakout" else None))
    rows = [dict(start=180, quality="stream_closed", open=entry-d*15,
                 close=entry-d*5)] if fresh else []
    plan = dict(direction=d, price=entry, stop=stop, net_loss=abs(entry-stop)+90,
                quantity_btc=1,
                entry_quality=dict(priority=dict(ready=True, dominant_intact=dominant, target_room=room,
                    observed_barrier=barrier), confirmed_1m_starts=[180] if fresh else []),
                entry_decision_priority=dict(ready=True, higher_direction_intact=higher))
    native = dict(quote=entry, minute_bars=rows)
    return entry_guard(plan, watch, native, stopped_retry=retry,
                       breakout_followthrough=breakout_followthrough)


class CaptureRiskGuardTests(unittest.TestCase):
    def test_82_near_barrier_override_not_allowed(self):
        allowed, reason, evidence = case(60316.7,60479.66334,room="conflict",barrier=60292.5)
        self.assertFalse(allowed)
        self.assertEqual(reason,"capture_guard_near_target_barrier")
        self.assertAlmostEqual(evidence["barrier_distance"],24.2)
        self.assertFalse(case(60316.7,60479.66334,room="conflict",barrier=60292.5,fresh=True)[0])

    def test_83_weak_context_waits(self):
        self.assertEqual(case(59364.9,59629.6195,higher=False)[1],
                         "capture_guard_wait_fresh_minute")
        self.assertTrue(case(59364.9,59629.6195,higher=False,fresh=True)[0])

    def test_84_retry_requires_independent_minute(self):
        self.assertEqual(case(59285.6,59632.98453,retry=True,strategy="breakout")[1],
                         "capture_guard_retry_wait_fresh_minute")
        self.assertTrue(case(59285.6,59632.98453,retry=True,strategy="breakout",fresh=True)[0])

    def test_shadow_84_as_new_breakout_checks_the_same_unfinished_structure(self):
        kwargs = dict(strategy="breakout", higher=True, dominant=False)
        self.assertTrue(case(59285.6,59632.98453, **kwargs)[0])
        self.assertEqual(case(59285.6,59632.98453, **kwargs, breakout_followthrough=True)[1],
                         "capture_shadow_breakout_wait_fresh_minute")
        self.assertTrue(case(59285.6,59632.98453, **kwargs,
                             fresh=True, breakout_followthrough=True)[0])
        self.assertTrue(case(60000,59800,direction=1,strategy="breakout",
                             dominant=True,breakout_followthrough=True)[0])

    def test_aligned_nonretry_not_universally_delayed(self):
        self.assertTrue(case(60000,60200)[0])
        self.assertTrue(case(60000,59800,direction=1)[0])

    def test_long_near_obstacle_symmetric(self):
        self.assertEqual(case(60000,59800,direction=1,room="conflict",barrier=60024.2)[1],
                         "capture_guard_near_target_barrier")

    def test_92_actual_adverse_quote_no_target_evidence(self):
        # Recorded watch 17:20:13, confirmation 17:20:45 KST.
        plan = dict(direction=1, price=62060.7, stop=61765.38786,
                    net_loss=388.15952844145056, quantity_btc=1,
                    entry_quality=dict(priority=dict(ready=True, dominant_intact=False,
                        target_room="unknown", observed_barrier=None), confirmed_1m_starts=[]),
                    entry_decision_priority=dict(ready=True, higher_direction_intact=True))
        watch = dict(native_watch_1m_start=1795767540, price=62079.1, strategy="trend_retest")
        native = dict(quote=62060.7, minute_bars=[])
        allowed, reason, evidence = entry_guard(plan, watch, native)
        self.assertFalse(allowed)
        self.assertEqual(reason, "capture_guard_unproven_room_wait_progress")
        self.assertAlmostEqual(evidence["signed_quote_progress"], -18.4)
        self.assertFalse(evidence["fresh_completed_minute"])

    def test_stalled_or_adverse_progress_symmetric_and_bounded(self):
        for d in (-1, 1):
            entry = 60000
            plan = dict(direction=d, price=entry, stop=entry-d*200,
                        net_loss=290, quantity_btc=1,
                        entry_quality=dict(priority=dict(ready=True, dominant_intact=False,
                            target_room="unknown", observed_barrier=None), confirmed_1m_starts=[]),
                        entry_decision_priority=dict(ready=True, higher_direction_intact=True))
            watch = dict(native_watch_1m_start=60, price=entry+d*10, strategy="trend_retest")
            native = dict(quote=entry, minute_bars=[])
            self.assertFalse(entry_guard(plan, watch, native)[0])
            watch["price"] = entry
            self.assertFalse(entry_guard(plan, watch, native)[0])
            watch["price"] = entry-d*10
            self.assertTrue(entry_guard(plan, watch, native)[0])
            watch["price"] = entry+d*10
            plan["entry_quality"]["priority"]["dominant_intact"] = True
            self.assertTrue(entry_guard(plan, watch, native)[0])
            plan["entry_quality"]["priority"]["dominant_intact"] = False
            plan["entry_quality"]["priority"]["target_room"] = "clear"
            self.assertTrue(entry_guard(plan, watch, native)[0])

    def test_bad_proof_fails_closed(self):
        self.assertFalse(entry_guard({}, {}, {})[0])

    def test_pre_stop_advisory_both_directions(self):
        for d in (-1,1):
            policy=dict(capture_guard_policy=POLICY,breakeven=60000+d*90)
            self.assertEqual(caution_kind(policy,{},direction=d,entry=60000,stop=60000-d*200,
                          current=60000-d*150,thesis_status="intact"),"pre_stop_risk")
            self.assertIsNone(caution_kind(policy,{},direction=d,entry=60000,stop=60000-d*200,
                          current=60000-d*200,thesis_status="intact"))

    def test_pre_target_net_giveback_advisory(self):
        for d in (-1,1):
            policy=dict(capture_guard_policy=POLICY,breakeven=60000+d*90)
            args=dict(direction=d,entry=60000,stop=60000-d*300,current=60000+d*110)
            state=dict(best=60000+d*150)
            self.assertEqual(caution_kind(policy,state,thesis_status="weakened",**args),
                             "pre_target_profit_giveback")
            self.assertIsNone(caution_kind(policy,state,thesis_status="intact",**args))
            self.assertIsNone(caution_kind({},state,thesis_status="weakened",**args))
            self.assertIsNone(caution_kind(policy,{"best":60000+d*50},thesis_status="weakened",**args))


if __name__ == "__main__":
    unittest.main()
