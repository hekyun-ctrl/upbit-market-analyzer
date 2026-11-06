"""Synthetic boundary regressions; none of these fixtures are market returns."""
import copy
import unittest
from unittest.mock import patch

import lbank_futures_monitor as m
from lbank_native_entry import SOURCE
from lbank_structural_target_plan import (StructuralTargetPolicy, available_barriers,
                                          entry_bound, rounded)


def fixture(d=1):
    bars = [dict(start=180000+i*300, open=100., close=100., high=101.,
                 low=99., quality='stream_closed', received_at=180300+i*300)
            for i in range(9)]
    bars[3].update(high=120., low=80.)
    return dict(source=SOURCE, five_bars=bars)


def policy(d=1, **kw):
    args = dict(now=183000., pnl=m.net_pnl_per_btc, ratio=1.5, tick=.1,
                quantity_step=.001, minimum_quantity=.001, minimum_notional=1.)
    args.update(kw)
    return StructuralTargetPolicy(**args)


class StructuralPlanTests(unittest.TestCase):
    def test_symmetric_structural_target_and_inverse_entry_equation(self):
        for d in (1,-1):
            p=policy(d); result=p(d,100.,95. if d==1 else 105.,{},fixture(d))
            self.assertEqual(result['status'],'qualified')
            self.assertLess(d*(result['target1']-result['barrier']['price']),0)
            self.assertEqual(result['target2'],result['target1'])
            bound=result['maximum_permissible_entry']
            gain=m.net_pnl_per_btc(d,bound,result['target1'],exit_kind='take_profit')
            loss=-m.net_pnl_per_btc(d,bound,result['stop'])
            self.assertAlmostEqual(gain/loss,1.5,places=9)
            self.assertEqual(p(d,bound+d*.01,result['stop'],{},fixture(d))['status'],'room_insufficient')

    def test_no_pivot_never_invents_target(self):
        s=fixture();s['five_bars'][3]['high']=101.
        result=policy()(1,100.,95.,{},s)
        self.assertEqual(result['status'],'room_unknown')
        self.assertNotIn('target1',result)

    def test_late_right_confirmation_receipt_cannot_be_backdated(self):
        bars=fixture()['five_bars'];bars[5]['received_at']=184000.
        self.assertEqual(available_barriers(bars,1,100.,183000.),[])
        self.assertEqual(available_barriers(bars,1,100.,184000.)[0]['known_at'],184000.)

    def test_missing_receipt_requires_explicit_nominal_scenario(self):
        bars=fixture()['five_bars']
        for b in bars:b.pop('received_at')
        self.assertEqual(available_barriers(bars,1,100.,183000.),[])
        result=available_barriers(bars,1,100.,183000.,15)[0]
        self.assertIsNone(result['known_at'])
        self.assertEqual(result['availability_basis'],'nominal_assumption')
        self.assertEqual(result['available_at'],181800.+15)

    def test_forming_update_before_close_is_not_closed_receipt(self):
        bars=fixture()['five_bars'];bars[5]['received_at']=bars[5]['start']+299
        self.assertEqual(available_barriers(bars,1,100.,183000.),[])

    def test_gap_duplicate_forming_nan_and_bad_ohlc_fail_closed(self):
        for bad in ('gap','duplicate','forming','nan','ohlc'):
            s=fixture();b=s['five_bars'][4]
            if bad=='gap':b['start']+=300
            elif bad=='duplicate':b['start']-=300
            elif bad=='forming':b['quality']='forming'
            elif bad=='nan':b['high']=float('nan')
            else:b['high']=90.
            self.assertEqual(policy()(1,100.,95.,{},s)['status'],'invalid_structural_evidence',bad)

    def test_broken_barrier_is_not_reused(self):
        bars=fixture()['five_bars'];bars[-1]['high']=121.
        self.assertEqual(available_barriers(bars,1,100.,183000.),[])

    def test_future_receipt_cannot_invalidate_known_barrier(self):
        bars=fixture()['five_bars'];bars[-1]['high']=121.;bars[-1]['received_at']=184000.
        self.assertEqual(available_barriers(bars,1,100.,183000.)[0]['price'],120.)
        self.assertEqual(available_barriers(bars,1,100.,184000.),[])

    def test_nearest_pivot_wins_and_hourly_context_is_not_a_gate(self):
        s=fixture();s['five_bars'] += [dict(start=182700+i*300,open=100.,close=100.,high=h,
                low=99.,quality='stream_closed',received_at=183000+i*300)
                for i,h in enumerate((101.,110.,101.,101.))]
        p=policy(now=184000.);a=p(1,100.,95.,{},s)
        self.assertEqual(a['barrier']['price'],110.)
        s.update(hourly_direction=-1,four_hour_direction='UNKNOWN')
        self.assertEqual(p(1,100.,95.,{},s),a)

    def test_confirmation_cannot_widen_stop_or_extend_target(self):
        p=policy();a=p(1,100.,95.,{},fixture());frozen=copy.deepcopy(a)
        b=p(1,101.,90.,{'structural_target_plan':a},fixture())
        self.assertEqual(b['stop'],a['stop']);self.assertEqual(b['target1'],a['target1'])
        self.assertEqual(a,frozen)

    def test_frozen_plan_rejects_new_nearer_barrier_or_source_revision(self):
        p=policy();a=p(1,100.,95.,{},fixture())
        revised=fixture();revised['five_bars'][3]['high']=180.
        self.assertEqual(p(1,101.,95.,{'structural_target_plan':a},revised)['status'],
                         'structural_barrier_invalidated')
        s=fixture();s['five_bars'] += [dict(start=182700+i*300,open=100.,close=100.,high=h,
                low=99.,quality='stream_closed',received_at=183000+i*300)
                for i,h in enumerate((101.,110.,101.,101.))]
        p.now=184000.
        self.assertEqual(p(1,101.,95.,{'structural_target_plan':a},s)['status'],'room_conflict')

    def test_tick_rounding_is_outward_stop_and_before_barrier(self):
        for d in (1,-1):
            result=policy()(d,100.,95.06 if d==1 else 104.94,{},fixture(d))
            self.assertEqual(result['stop'],95. if d==1 else 105.)
            self.assertAlmostEqual(result['target1']/.1,round(result['target1']/.1))
        self.assertEqual(rounded(.30000000000000004,.1),.3)

    def test_quantity_steps_minimum_and_cap(self):
        p=policy()
        self.assertEqual(p.quantity(.0129,100.),.012)
        self.assertEqual(p.quantity(.009,100.),0.)
        self.assertLessEqual(p.quantity(.0129,100.),.0129)

    def test_cost_increase_never_improves_entry_bound(self):
        def expensive(d,e,x,**kw):return m.net_pnl_per_btc(d,e,x,**kw)-.1
        for d,s,t in ((1,95.,120.),(-1,105.,80.)):
            a=entry_bound(d,s,t,m.net_pnl_per_btc,1.5)
            b=entry_bound(d,s,t,expensive,1.5)
            self.assertLess(d*(b-a),0)

    def test_wrong_source_and_setup_barrier_fail_closed(self):
        s=fixture();s['source']='spot'
        self.assertEqual(policy()(1,100.,95.,{},s)['status'],'invalid_structural_evidence')
        self.assertEqual(policy()(1,100.,95.,{'barrier':101.},fixture())['status'],'room_conflict')

    def test_invalid_metadata_and_geometry_rejected(self):
        with self.assertRaises(ValueError):policy(tick=0)
        with self.assertRaises(ValueError):entry_bound(1,110.,100.,m.net_pnl_per_btc,1.5)

    def test_integrated_plan_preserves_risk_margin_quantity_and_structural_bounds(self):
        from test_lbank_entry_location import retest_state
        for d in (1,-1):
            s=retest_state(d);now=s['minute_bars'][-1]['start']+60
            s['five_bars'][-6]['high' if d==1 else 'low']=80500. if d==1 else 79500.
            p=policy(now=now,nominal_delay=0,quantity_step=.0001,
                     minimum_quantity=.0001,minimum_notional=1.)
            setup=dict(strategy='breakout',direction=d,level=s['quote']-d)
            original=copy.deepcopy(s)
            with patch.object(m,'ENTRY_REFINEMENT_MODE','capture'):
                plan=m.calculate_plan(s['prices'],d,setup,native_state=s,target_policy=p)
                self.assertIsNotNone(plan,p.last)
                self.assertLessEqual(plan['net_loss'],m.REF_BALANCE*m.RISK_FRACTION+1e-7)
                self.assertLessEqual(plan['margin'],m.REF_BALANCE*m.MAX_MARGIN_FRACTION+1e-7)
                self.assertGreaterEqual(plan['rr1'],m.MIN_NET_RR-1e-7)
                self.assertAlmostEqual(plan['quantity_btc']/.0001,round(plan['quantity_btc']/.0001))
                self.assertLess(d*(plan['target1']-p.last['barrier']['price']),0)
                self.assertEqual(s,original)
                s['quote']=plan['structural_target_plan']['maximum_permissible_entry']+d
                self.assertIsNone(m.calculate_plan(s['prices'],d,plan,native_state=s,target_policy=p))


if __name__=='__main__':unittest.main()
