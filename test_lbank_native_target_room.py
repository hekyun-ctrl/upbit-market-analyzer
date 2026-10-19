"""Known target conflicts must reach the live plan gate, not just a label.

Only the 49-bar signal #11 fixture is recorded market data. Other boundary
fixtures are synthetic and make no statement about trade performance.
"""
import copy
import json
from pathlib import Path
import unittest

import lbank_futures_monitor as monitor
from lbank_entry_quality import observed_target_room


class NativeTargetRoomTests(unittest.TestCase):
    def test_signal11_known_support_conflicted_at_entry_without_future_bars(self):
        record = json.loads((Path(__file__).parent /
            'research/fixtures/lbank_signal11_20261019.json').read_text())
        bars = record['five_bars']
        self.assertEqual(len(bars), 49)
        self.assertTrue(all(b['start']-a['start'] == 300 for a,b in zip(bars,bars[1:])))
        self.assertTrue(all(b['updated'] < 1792397457 for b in bars))
        evidence = observed_target_room(bars, -1, record['entry'], record['target1'])
        self.assertEqual(evidence['target_room'], 'conflict')
        self.assertEqual(evidence['observed_barrier'], 82508.5)
        # A target reached before the same obstacle can be judged clear.
        self.assertEqual(observed_target_room(bars, -1, record['entry'], 82530.)[
            'target_room'], 'clear')

    def test_both_directions_reject_known_obstacle_in_shared_plan_gate(self):
        from test_lbank_entry_geometry import state
        for d in (1, -1):
            s = state()
            setup = dict(strategy='breakout', level=80000-d*20)
            accepted = monitor.calculate_plan(s['prices'], d, setup, native_state=s)
            self.assertIsNotNone(accepted)
            s['five_bars'][-6]['high' if d == 1 else 'low'] = 80000+d*60
            self.assertIsNone(monitor.calculate_plan(s['prices'], d, setup, native_state=s))
            # Removing the conflict leaves the original monetary model intact.
            s['five_bars'][-6]['high' if d == 1 else 'low'] = 80000+d*10
            restored = monitor.calculate_plan(s['prices'], d, setup, native_state=s)
            self.assertEqual(restored, accepted)

    def test_near_target_buffer_and_unconfirmed_extrema_are_not_conflated(self):
        for d in (1, -1):
            key = 'high' if d == 1 else 'low'
            bars = [dict(high=101., low=99.) for _ in range(7)]
            bars[3][key] = 100+d*5
            self.assertEqual(observed_target_room(bars,d,100.,100+d*4.99)[
                'target_room'], 'conflict')
            self.assertEqual(observed_target_room(bars,d,100.,100+d*4.98)[
                'target_room'], 'clear')
            self.assertEqual(observed_target_room(bars,d,100+d*6,100+d*7)[
                'target_room'], 'unknown')
            bars[-2][key] = 100+d*6
            bars[3][key] = 101. if d == 1 else 99.
            self.assertEqual(observed_target_room(bars,d,100.,100+d*7)[
                'target_room'], 'unknown')

    def test_watch_and_confirmation_cannot_bypass_conflict_with_momentum(self):
        from test_lbank_native_entry import NativeEntryTests
        fixture = NativeEntryTests()
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        s, _ = fixture.snapshot()
        s['rsi5']['rsi'] = [84., 85.]
        watch = monitor.classify(s['prices'], native_state=s)
        self.assertIsNotNone(watch)
        # No universal extra wait: a clean existing snapshot still confirms.
        before = monitor.confirm_watch(s['prices'],watch,60,native_state=s)
        self.assertIsNotNone(before)
        self.assertFalse(before['entry_quality']['requires_two_minute_wait'])
        s['five_bars'][-6]['high'] = s['quote']+50
        original = copy.deepcopy(s)
        for stage in ('watch','confirm'):
            diag = {}
            plan = (monitor.assess_candidate(s['prices'],1,watch,native_state=s,diagnostics=diag)
                    if stage == 'watch' else
                    monitor.confirm_watch(s['prices'],watch,60,native_state=s,diagnostics=diag))
            self.assertIsNone(plan,stage)
            self.assertEqual(diag['reason'],'risk_or_target')
        self.assertEqual(s,original)


if __name__ == '__main__':
    unittest.main()
