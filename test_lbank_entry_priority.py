"""Policy and live-path regressions; no claims about fills or profitability."""
import copy
import json
from pathlib import Path
import unittest
from unittest.mock import patch

import lbank_futures_monitor as monitor
from lbank_entry_priority import ranked_entry_evidence
from lbank_native_entry import SOURCE, wilder_rsi_series
from test_lbank_entry_quality import structural_state


def recorded_states():
    record=json.loads((Path(__file__).parent /
        'research/fixtures/lbank_direction_priority_20261019.json').read_text())
    result={}
    for case in record['cases']:
        t=case['at'];tail=[]
        for b in reversed([b for b in record['bars'] if b['start']+300<=t]):
            if tail and tail[-1]['start']-b['start']!=300:break
            tail.append(b)
        bars=list(reversed(tail));by={b['start']:b for b in bars}
        end=int(t//900)*900-900
        state=dict(source=SOURCE,five_bars=bars,last5_start=bars[-1]['start'],
            closes15=[by[k+600]['close'] for k in range(end-3600,end+1,900)],
            rsi5=dict(rsi=wilder_rsi_series([b['close'] for b in bars])))
        result[case['name']]=(case,state)
    return result


def synthetic_state(d=1):
    s=structural_state(d)
    for b in s['five_bars']:
        b.update(high=b['close']+80,low=b['close']-80)
    s['rsi5']['closes']=[b['close'] for b in s['five_bars']]
    return s


class RankedEntryTests(unittest.TestCase):
    def test_recorded_weak_signal11_cannot_override_known_support(self):
        c,s=recorded_states()['signal11']
        before=copy.deepcopy(s)
        e=ranked_entry_evidence(s,c['direction'],c['price'],c['target1'],c['at'])
        self.assertTrue(e['ready'])
        self.assertFalse(e['higher_direction_intact'])
        self.assertFalse(e['may_override_single_pivots'])
        self.assertEqual(e['barrier_groups'][0]['price'],82508.5)
        self.assertEqual(s,before)

    def test_recorded_1859_and_1910_are_not_retroactively_approved(self):
        for name in ('early_breakout_1859','early_retest_1910','early_retest_1914'):
            c,s=recorded_states()[name]
            e=ranked_entry_evidence(s,c['direction'],c['price'],c['target1'],c['at'])
            self.assertFalse(e['higher_direction_intact'],name)
            self.assertFalse(e['may_override_single_pivots'],name)
            self.assertFalse(e['atr_target_exception'],name)

    def test_recorded_1918_has_higher_direction_before_strict_three_swings(self):
        c,s=recorded_states()['early_breakout_1918']
        e=ranked_entry_evidence(s,c['direction'],c['price'],c['target1'],c['at'])
        self.assertTrue(e['higher_direction_intact'])
        self.assertFalse(e['upper_evidence']['swing_structure'])
        self.assertTrue(e['may_override_single_pivots'])
        self.assertEqual([g['price'] for g in e['barrier_groups']],[82553.5,82508.5])
        self.assertTrue(e['location_ok'])
        self.assertTrue(e['atr_target_exception'])

    def test_recorded_1923_is_extended_even_with_aligned_direction(self):
        c,s=recorded_states()['signal12']
        e=ranked_entry_evidence(s,c['direction'],c['price'],c['target1'],c['at'])
        self.assertTrue(e['higher_direction_intact'])
        self.assertGreater(e['extension_atr'],2.5)
        self.assertFalse(e['location_ok'])
        self.assertFalse(e['may_override_minor_reaction'])

    def test_single_barrier_override_and_repeated_barrier_veto_both_directions(self):
        for d in (1,-1):
            s=synthetic_state(d);p=s['quote'];key='high' if d==1 else 'low'
            s['five_bars'][-6][key]=p+d*80
            e=ranked_entry_evidence(s,d,p,p+d*600,9020)
            self.assertTrue(e['may_override_single_pivots'])
            s['five_bars'][-12][key]=p+d*85
            e=ranked_entry_evidence(s,d,p,p+d*600,9020)
            self.assertTrue(e['repeated_barrier'])
            self.assertFalse(e['may_override_single_pivots'])

    def test_deeper_repeated_obstacle_is_not_hidden_by_nearer_single_pivot(self):
        s=synthetic_state();p=s['quote']
        for i,extra in ((-6,80),(-12,250),(-18,255)):
            s['five_bars'][i]['high']=p+extra
        e=ranked_entry_evidence(s,1,p,p+600,9020)
        self.assertEqual(e['barrier_groups'][0]['touches'],1)
        self.assertTrue(e['repeated_barrier'])
        self.assertFalse(e['may_override_single_pivots'])

    def test_unknown_stale_forming_and_opposed_direction_never_grant_exception(self):
        for mutation in ('source','stale','forming','15m','ema'):
            s=synthetic_state();now=9020
            if mutation=='source':s['source']='ticker'
            elif mutation=='stale':now+=301
            elif mutation=='forming':s['five_bars'][-1]['quality']='forming'
            elif mutation=='15m':s['closes15']=[80500.,80400.,80300.]
            else:
                for b in s['five_bars']:b['close']=80000.
            self.assertFalse(ranked_entry_evidence(s,1,s['quote'],s['quote']+600,now)[
                'may_override_single_pivots'],mutation)

    def test_live_plan_prioritizes_direction_but_keeps_risk_and_target_bounds(self):
        for d in (1,-1):
            s=synthetic_state(d);p=s['quote'];key='high' if d==1 else 'low'
            s['five_bars'][-6][key]=p+d*80
            setup=dict(strategy='breakout',level=p-d*20)
            plan=monitor.calculate_plan(s['prices'],d,setup,native_state=s)
            self.assertIsNotNone(plan)
            self.assertIn('single_pivots_under_aligned_15m_5m',plan['entry_decision_priority']['overrides'])
            self.assertIn('historical_range_within_6atr',plan['entry_decision_priority']['overrides'])
            self.assertLessEqual(plan['net_loss'],monitor.REF_BALANCE*monitor.RISK_FRACTION)
            self.assertLessEqual(plan['margin'],monitor.REF_BALANCE*monitor.MAX_MARGIN_FRACTION)
            self.assertGreaterEqual(plan['rr1'],monitor.MIN_NET_RR-1e-7)
            box=dict(strategy='range_rebound',structure_anchor=p-d*100,barrier=p+d*60)
            self.assertIsNone(monitor.calculate_plan(s['prices'],d,box,native_state=s))
            with patch.object(monitor,'minimum_net_rr_target',return_value=p+d*2000):
                self.assertIsNone(monitor.calculate_plan(s['prices'],d,setup,native_state=s))
            s['five_bars'][-12][key]=p+d*85
            self.assertIsNone(monitor.calculate_plan(s['prices'],d,setup,native_state=s))

    def test_confirmation_keeps_existing_timing_and_hard_invalidation(self):
        for d in (1,-1):
            s=synthetic_state(d);p=s['quote'];key='high' if d==1 else 'low'
            s['five_bars'][-6][key]=p+d*80
            w=monitor.calculate_plan(s['prices'],d,dict(strategy='breakout',level=p-d*20),native_state=s)
            w.update(native_watch_1m_start=s['minute_bars'][-1]['start'])
            self.assertIsNotNone(monitor.assess_candidate(s['prices'],d,w,native_state=s))
            confirmed=monitor.confirm_watch(s['prices'],w,60,native_state=s)
            self.assertIsNotNone(confirmed)
            self.assertFalse(confirmed['entry_quality']['requires_two_minute_wait'])
            self.assertIsNone(monitor.confirm_watch(s['prices'],w,10,native_state=s))
            for crossed in (w['stop'],w['level']):
                s['quote']=crossed
                self.assertIsNone(monitor.confirm_watch(s['prices'],w,60,native_state=s))

    def test_retest_cannot_recover_arbitrarily_far_from_its_anchor(self):
        s=synthetic_state();p=s['quote'];e=ranked_entry_evidence(s,1,p,now=9020)
        setup=dict(strategy='trend_retest',structure_anchor=p-2.6*e['atr5'],level=p-100)
        with self.assertLogs(monitor.LOG,level='INFO') as logs:
            self.assertIsNone(monitor.calculate_plan(s['prices'],1,setup,native_state=s))
        self.assertTrue(any('retest_recovery_already_extended' in x for x in logs.output))

    def test_live_plan_rejects_extended_price_before_sizing_for_both_directions(self):
        for d in (1,-1):
            s=synthetic_state(d);p=s['quote']
            e=ranked_entry_evidence(s,d,p,now=9020)
            s['quote']=e['ema9']+d*3*e['atr5']
            geometry=monitor.native_entry_geometry(s)
            with patch.object(monitor,'native_entry_geometry',return_value=geometry), \
                 patch.object(monitor,'minimum_net_rr_target',side_effect=AssertionError('late sizing')):
                with self.assertLogs(monitor.LOG,level='INFO') as logs:
                    self.assertIsNone(monitor.calculate_plan(s['prices'],d,native_state=s))
            self.assertTrue(any('entry_extended_from_native_ema' in x for x in logs.output))

    def test_ranked_evidence_is_frozen_and_user_caution_is_visible(self):
        from lbank_performance import PerformanceBook
        s=synthetic_state();p=s['quote'];s['five_bars'][-6]['high']=p+80
        plan=monitor.calculate_plan(s['prices'],1,dict(strategy='breakout',level=p-20),native_state=s)
        plan.update(data_source=SOURCE,entry_quality=dict(priority={},
            ranked_entry=plan['entry_decision_priority']))
        note=monitor.format_entry_alert(plan,'2026-10-19T10:00:00Z')
        self.assertIn('15분·5분 방향 우선·앞선 지지/저항 유의',note)
        book=PerformanceBook(':memory:',1000);self.addCleanup(book.db.close)
        book.add(1,plan,9020)
        plan['entry_decision_priority']['overrides'].clear()
        saved=json.loads(book.db.execute('SELECT plan FROM signals LIMIT 1').fetchone()[0])
        self.assertIn('single_pivots_under_aligned_15m_5m',saved['entry_quality']['ranked_entry']['overrides'])


if __name__=='__main__':unittest.main()
