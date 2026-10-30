"""Policy invariants and honest replay accounting, not profitability proof."""
import copy
import json
import tempfile
import unittest
from unittest.mock import patch

import lbank_futures_monitor as m
from lbank_breakout_freshness import evidence, confirmation_allowed
from lbank_entry_refinement import minute_atr, local_stop_floor, impulse_breakout
from research.compare_entry_refinement import (DATA, load_archives, make_feed, available_state,
    outcome, aggregate, iso)
from test_lbank_entry_geometry import state as geometry_state


def complete_minutes(state):
    for i, b in enumerate(state['minute_bars']):
        b.update(start=180000+i*60, quality='stream_closed')
    return state


def impulse_state(direction):
    prices = [80000.+direction*2*i for i in range(65)]
    prices[-1] = prices[-2]+direction*70
    bars = []
    for i, close in enumerate(prices):
        op = prices[max(0,i-1)]
        bars.append(dict(open=op, close=close, high=max(op,close)+5,
                         low=min(op,close)-5, quality='stream_closed', start=180000+i*60))
    return dict(prices=prices, minute_bars=bars)


class RefinementTests(unittest.TestCase):
    def test_default_is_legacy_and_range_geometry_is_identical(self):
        self.assertEqual(m.ENTRY_REFINEMENT_MODE, 'legacy')
        s = complete_minutes(geometry_state())
        original = m.native_entry_geometry(s, 'range_rebound')
        for mode in ('structure_cost','capture'):
            with patch.object(m,'ENTRY_REFINEMENT_MODE',mode):
                self.assertEqual(m.native_entry_geometry(s,'range_rebound'), original)
                self.assertEqual(m.native_entry_geometry(s), original)

    def test_structural_stops_and_money_caps_survive_shorter_targets(self):
        s = complete_minutes(geometry_state())
        for b in s['five_bars']:
            b.update(high=80100., low=79900.)
        for direction in (1,-1):
            for strategy in ('breakout','trend_retest'):
                setup = dict(strategy=strategy, structure_anchor=80000-direction*90)
                with patch.object(m,'ENTRY_REFINEMENT_MODE','legacy'):
                    old = m.calculate_plan(s['prices'], direction, setup, native_state=s)
                with patch.object(m,'ENTRY_REFINEMENT_MODE','structure_cost'):
                    new = m.calculate_plan(s['prices'], direction, setup, native_state=s)
                    blocked = dict(setup, barrier=80000+direction*10)
                    self.assertIsNone(m.calculate_plan(s['prices'],direction,blocked,native_state=s))
                self.assertIsNotNone(old)
                self.assertIsNotNone(new)
                self.assertLess(abs(new['target1']-new['price']), abs(old['target1']-old['price']))
                anchor = (min(b['low'] for b in s['minute_bars'][-8:]) if direction==1 else
                          max(b['high'] for b in s['minute_bars'][-8:])) if strategy=='breakout' else setup['structure_anchor']
                self.assertLess(direction*(new['stop']-anchor), 0)
                self.assertLessEqual(new['net_loss'],m.REF_BALANCE*m.RISK_FRACTION)
                self.assertLessEqual(new['margin'],m.REF_BALANCE*m.MAX_MARGIN_FRACTION)
                self.assertGreaterEqual(new['rr1'],1.5-1e-7)

    def test_invalid_or_missing_actual_minute_data_has_no_substitute(self):
        valid = complete_minutes(geometry_state())
        self.assertIsNotNone(minute_atr(valid))
        for change in ('gap','forming','invalid_ohlc','nan','unaligned','short_history'):
            s = copy.deepcopy(valid)
            if change == 'gap': s['minute_bars'].pop(20)
            if change == 'forming': s['minute_bars'][-1]['quality']='forming'
            if change == 'invalid_ohlc': s['minute_bars'][-1]['low']=90000
            if change == 'nan': s['minute_bars'][-1]['close']=float('nan')
            if change == 'unaligned': s['minute_bars'][-1]['start']+=1
            if change == 'short_history': s['minute_bars']=s['minute_bars'][-14:]
            with self.subTest(change=change):
                self.assertIsNone(minute_atr(s))
                self.assertIsNone(local_stop_floor(s,.003))
                with patch.object(m,'ENTRY_REFINEMENT_MODE','capture'):
                    self.assertIsNone(m.calculate_plan(s['prices'],1,native_state=s))

    def test_impulse_path_needs_closed_body_actual_break_and_alignment_both_sides(self):
        for direction in (1,-1):
            s = impulse_state(direction)
            old = evidence(s['prices'],s,m.ema)
            new = evidence(s['prices'],s,m.ema,adaptive=True)
            key = 'breakout_long' if direction==1 else 'breakout_short'
            self.assertFalse(old[key])
            self.assertTrue(new[key])
            # The shared confirmation guard accepts exactly the same new evidence.
            plan = dict(direction=direction,entry_quality=dict(priority={},ranked_entry={}))
            self.assertTrue(confirmation_allowed(plan,new))
            s['minute_bars'][-1]['open']=s['prices'][-1]
            self.assertFalse(evidence(s['prices'],s,m.ema,adaptive=True)[key])
            s = impulse_state(direction)
            s['minute_bars'][-2]['high' if direction==1 else 'low']=s['prices'][-1]+direction
            self.assertFalse(evidence(s['prices'],s,m.ema,adaptive=True)[key])

    def test_extra_impulse_rejects_unaligned_prices(self):
        s = impulse_state(1)
        for prices in ([], s['prices'][:-1], [p+1 for p in s['prices']]):
            self.assertFalse(impulse_breakout(prices,s,m.ema,80000,79999)['long'])


class ReplayEvidenceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bundles, cls.bars = load_archives(DATA/'archive_chunks.json')

    def test_archive_hashes_and_duplicate_bars_are_verified(self):
        self.assertEqual(len(self.bundles),5)
        self.assertEqual(sum(k[0]==60 for k in self.bars),1123)
        self.assertEqual(sum(k[0]==300 for k in self.bars),581)
        parts = json.loads((DATA/'archive_chunks.json').read_text())
        with tempfile.TemporaryDirectory() as directory:
            from pathlib import Path
            p = Path(directory)/'chunks.json'
            p.write_text(json.dumps(parts[:-1]))
            with self.assertRaisesRegex(ValueError,'incomplete'):
                load_archives(p)

    def test_future_bars_do_not_change_past_state_and_delay_is_explicit(self):
        from datetime import datetime
        ts = datetime.fromisoformat('2026-10-20T00:50:20+00:00').timestamp()
        with tempfile.TemporaryDirectory() as directory:
            feed = make_feed(directory,self.bars)
            state, _ = available_state(feed,ts,81500.,0)
            self.assertIsNotNone(state)
            self.assertTrue(all(b['start']+60<=ts for b in state['minute_bars']))
            self.assertTrue(all(b['start']+300<=ts for b in state['five_bars']))
            import sqlite3
            db=sqlite3.connect(feed.path)
            db.execute('UPDATE native_bars SET close=999999 WHERE start>=?', (int(ts//60)*60,))
            db.commit();db.close()
            same,_=available_state(feed,ts,81500.,0)
            self.assertEqual(state,same)
            delayed,reason=available_state(feed,ts-15,81500.,15)
            self.assertIsNone(delayed)
            self.assertEqual(reason,'nominal_publication_delay')


class OutcomeTests(unittest.TestCase):
    def setUp(self):
        self.cost = patch.multiple(m,TAKER_FEE=0.,STOP_SLIPPAGE=0.,FUNDING_BUFFER=0.)
        self.cost.start();self.addCleanup(self.cost.stop)
        self.plan = dict(at=iso(300),direction=1,price=100.,stop=99.,target1=102.,
                         target2=103.,quantity_btc=1.)

    def bar(self,start=300,**changes):
        return dict(dict(start=start,open=100.,high=101.,low=99.5,close=100.,quality='stream_closed'),**changes)

    def test_unknown_entry_bar_is_not_assigned_a_win_or_loss(self):
        self.plan['at']=iso(330)
        result=outcome(self.plan,{300:self.bar(high=104.)})
        self.assertEqual(result['reason'],'entry_bar_before_after_unknown')

    def test_same_bar_stop_and_targets_produce_bounds(self):
        result=outcome(self.plan,{300:self.bar(low=98.,high=104.)})
        self.assertEqual(result['status'],'intrabar_range')
        self.assertEqual(result['net_usdt_low'],-1.)
        self.assertEqual(result['net_usdt_high'],2.5)

    def test_partial_target_then_stop_has_fractional_costs(self):
        with patch.multiple(m,TAKER_FEE=.001,STOP_SLIPPAGE=.002,FUNDING_BUFFER=.0001):
            result=outcome(self.plan,{300:self.bar(high=102.5,close=102.),600:self.bar(start=600,open=102.,high=102.,low=98.)})
            expected=.5*m.net_pnl_per_btc(1,100,102,exit_kind='take_profit')+.5*m.net_pnl_per_btc(1,100,99)
            self.assertEqual(result['status'],'exact_in_model')
            self.assertAlmostEqual(result['net_usdt_low'],expected)

    def test_gap_never_becomes_a_synthetic_flat_bar(self):
        result=outcome(self.plan,{300:self.bar(),900:self.bar(start=900,high=104.)})
        self.assertEqual(result['reason'],'missing_or_invalid_outcome_bar')

    def test_open_gap_beyond_stop_uses_worse_open(self):
        result=outcome(self.plan,{300:self.bar(open=97.,low=96.,high=104.)})
        self.assertEqual(result['net_usdt_low'],-3.)
        self.assertEqual(result['net_usdt_high'],-3.)

    def test_open_beyond_both_targets_exits_before_later_stop(self):
        result=outcome(self.plan,{300:self.bar(open=104.,low=98.,high=105.)})
        self.assertEqual(result['status'],'exact_in_model')
        self.assertEqual(result['net_usdt_low'],2.5)

    def test_short_outcomes_are_symmetric(self):
        self.plan.update(direction=-1,stop=101.,target1=98.,target2=97.)
        result=outcome(self.plan,{300:self.bar(low=96.,high=102.)})
        self.assertEqual(result['net_usdt_low'],-1.)
        self.assertEqual(result['net_usdt_high'],2.5)

    def test_unresolved_trade_prevents_total_return_or_drawdown_claim(self):
        window = dict(ready_observations=2,raw_candidate_bar_families=1,
            qualified_candidate_bars=1,modeled_alerts=1,
            signals=[dict(benchmark=dict(status='unresolved',reason='gap'))])
        result=aggregate(dict(test=window))
        self.assertIsNone(result['total_net_usdt'])
        self.assertIsNone(result['closed_trade_drawdown_usdt'])


if __name__ == '__main__':
    unittest.main()
