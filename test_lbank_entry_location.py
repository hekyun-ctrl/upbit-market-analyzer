"""Causal location regressions. Synthetic boundaries are not market returns."""
import copy
import unittest
from unittest.mock import patch

import lbank_futures_monitor as m
from lbank_entry_location import first_retests, location_check, confirmation_check
from lbank_native_entry import SOURCE


def retest_state(d=1):
    prices = [80000.+.3*i for i in range(65)]
    bars = [dict(start=180000+i*60, open=p-.2, close=p, high=p+.8,
                 low=p-.8, quality='stream_closed', volume=1.) for i,p in enumerate(prices)]
    bars[10].update(high=80500.,low=79500.)  # Historical room, not a future candle.
    bars[-3].update(open=80018.3,close=80020.8,high=80021.,low=80018.2)
    bars[-2].update(open=80019.9,close=80019.2,high=80020.,low=80018.8)
    bars[-1].update(open=80019.2,close=80020.3,high=80020.4,low=80019.)
    end=bars[-1]['start']+60
    five=[dict(start=end-9000+i*300,open=80020.3-25*(29-i),close=80020.3-25*(29-i),
               high=80120.3-25*(29-i),low=79920.3-25*(29-i),volume=5.,quality='stream_closed') for i in range(30)]
    if d == -1:
        for b in bars+five:
            b.update(open=160000-b['open'],close=160000-b['close'],
                     high=160000-b['low'],low=160000-b['high'])
    return dict(source=SOURCE,minute_bars=bars,prices=[b['close'] for b in bars],
                quote=bars[-1]['close'],five_bars=five,last5_start=five[-1]['start'],
                closes15=[five[i]['close'] for i in (17,20,23,26,29)],
                rsi5=dict(closes=[b['close'] for b in five],rsi=[51.,52.,53.] if d==1 else [49.,48.,47.]))


class LocationTests(unittest.TestCase):
    def setUp(self):
        mode=patch.object(m,'ENTRY_REFINEMENT_MODE','location')
        mode.start();self.addCleanup(mode.stop)

    def test_first_retest_is_symmetric_closed_and_before_accumulated_momentum(self):
        for d in (1,-1):
            s=retest_state(d);original=copy.deepcopy(s)
            setups=first_retests(s)
            self.assertEqual(len(setups),1)
            self.assertEqual(setups[0]['direction'],d)
            self.assertTrue(setups[0]['entry_location']['first_retest'])
            self.assertFalse(m.trend_retest_momentum_gate(s['prices'],d)[0])
            diag={};plan=m.classify(s['prices'],native_state=s,diagnostics=diag)
            self.assertIsNotNone(plan,diag)
            self.assertTrue(plan['entry_location']['first_retest'])
            self.assertEqual(s,original)
            self.assertLessEqual(plan['net_loss'],m.REF_BALANCE*m.RISK_FRACTION+1e-7)
            self.assertLessEqual(plan['margin'],m.REF_BALANCE*m.MAX_MARGIN_FRACTION)
            self.assertGreaterEqual(plan['rr1'],m.MIN_NET_RR-1e-7)
            self.assertLess(d*(plan['stop']-plan['structure_anchor']),0)

    def test_no_forming_gapped_wrong_source_or_unaligned_substitute(self):
        for change in ('forming','gap','source','prices','nan'):
            s=retest_state()
            if change=='forming':s['minute_bars'][-1]['quality']='forming'
            elif change=='gap':s['minute_bars'][-3]['start']-=60
            elif change=='source':s['source']='spot'
            elif change=='prices':s['prices'][-1]+=1
            else:s['quote']=float('nan')
            self.assertEqual(first_retests(s),[],change)

    def test_only_a_real_retest_with_a_closed_resumption_is_detected(self):
        for change in ('no_touch','lost_level','no_resume','old_resumption'):
            s=retest_state()
            if change=='no_touch':
                for b in s['minute_bars'][-2:]:b['low']=min(b['open'],b['close'])
                s['minute_bars'][-2].update(open=80021.5,close=80021.3,low=80021.2,high=80021.6)
                s['minute_bars'][-1].update(open=80021.3,close=80022.,low=80021.1,high=80022.1)
            elif change=='lost_level':s['minute_bars'][-2]['low']=80000.
            elif change=='no_resume':s['minute_bars'][-1]['close']=80019.5
            else:s['minute_bars'][-2]['close']=80020.5;s['minute_bars'][-2]['high']=80020.6
            s['prices']=[b['close'] for b in s['minute_bars']]
            self.assertEqual(first_retests(s),[],change)

    def test_quote_reclaims_closed_retest_before_the_next_minute_closes(self):
        for d in (1,-1):
            s=retest_state(d)
            b=s['minute_bars'][-1]
            b.update(open=80019.2 if d==1 else 79980.8,
                     close=80019.3 if d==1 else 79980.7,
                     high=80019.4 if d==1 else 79981.1,
                     low=80018.9 if d==1 else 79980.6)
            s['prices']=[b['close'] for b in s['minute_bars']]
            s['quote']=80020.1 if d==1 else 79979.9
            original=copy.deepcopy(s)
            proposals=first_retests(s)
            self.assertEqual(proposals[0]['entry_location']['source'],'completed_retest_live_quote')
            self.assertIsNotNone(m.classify(s['prices'],native_state=s))
            self.assertEqual(s,original)  # No quote appended as a fabricated candle.
            s['quote']=80019.9 if d==1 else 79980.1
            self.assertEqual(first_retests(s),[])

    def test_confirmation_progress_uses_discovery_volatility(self):
        s=retest_state();watch=m.classify(s['prices'],native_state=s)
        watch['location_evidence']['atr1']=100.
        s['quote']=watch['price']+1
        self.assertEqual(confirmation_check(s,watch)[1],'quote_not_progressing_from_watch')

    def test_extended_breakout_and_pullback_are_rejected_without_widening_stop(self):
        for d in (1,-1):
            s=retest_state(d);atr=first_retests(s)[0]['entry_location']['atr1']
            for strategy,anchor,limit in [('breakout','level',1.5),('trend_retest','structure_anchor',2.)]:
                setup=dict(strategy=strategy,**{anchor:s['quote']-d*(limit+.01)*atr})
                self.assertEqual(location_check(s,d,setup)[1],'entry_location_extended_or_lost')
                self.assertIsNone(m.calculate_plan(s['prices'],d,setup,native_state=s))

    def test_standing_or_adverse_quote_cannot_be_confirmed_by_higher_direction(self):
        for d in (1,-1):
            s=retest_state(d);watch=m.classify(s['prices'],native_state=s)
            self.assertIsNotNone(watch)
            for move in (0.,-.1):
                s['quote']=watch['price']+d*move
                diag={}
                self.assertIsNone(m.confirm_watch(s['prices'],watch,30,native_state=s,diagnostics=diag))
                self.assertEqual(diag['reason'],'quote_not_progressing_from_watch')

    def test_actual_progress_confirms_at_30_seconds_without_new_bar_wait(self):
        for d in (1,-1):
            s=retest_state(d);watch=m.classify(s['prices'],native_state=s)
            required=max(.1,.1*watch['location_evidence']['atr1'])
            s['quote']=watch['price']+d*(required+.01)
            self.assertIsNone(m.confirm_watch(s['prices'],watch,29,native_state=s))
            diag={};plan=m.confirm_watch(s['prices'],watch,30,native_state=s,diagnostics=diag)
            self.assertIsNotNone(plan,diag)
            self.assertEqual(plan['entry_quality']['location_confirmation']['extra_wait_seconds'],0)

    def test_first_post_watch_closed_minute_stop_cross_is_not_erased(self):
        for d in (1,-1):
            s=retest_state(d);watch=m.classify(s['prices'],native_state=s)
            p=watch['price'];q=p+d
            s['minute_bars'].append(dict(start=watch['native_watch_1m_start']+60,
                open=p,close=q,high=max(p,q)+1,low=min(p,q)-1,
                volume=1.,quality='stream_closed'))
            s['minute_bars'][-1]['low' if d==1 else 'high']=watch['stop']-d
            s['prices']=[b['close'] for b in s['minute_bars']];s['quote']=q
            self.assertEqual(confirmation_check(s,watch)[1],'watch_structure_stop_crossed')

    def test_frozen_pattern_expires_and_cannot_move_to_another_anchor(self):
        s=retest_state();setup=first_retests(s)[0]
        setup['entry_location']['expires_bar_end']=s['minute_bars'][-1]['start']
        self.assertEqual(location_check(s,1,setup)[1],'first_retest_evidence_lost_or_expired')

    def test_single_known_barrier_is_not_overridden_in_either_stage(self):
        for d in (1,-1):
            s=retest_state(d);watch=m.classify(s['prices'],native_state=s)
            self.assertIsNotNone(watch)
            s['five_bars'][-6]['high' if d==1 else 'low']=s['quote']+d*150.
            # Make this a single confirmed isolated pivot in the target path.
            for stage in ('watch','confirm'):
                s['quote']=watch['price']+d
                diag={}
                plan=(m.assess_candidate(s['prices'],d,watch,native_state=s,diagnostics=diag)
                      if stage=='watch' else m.confirm_watch(s['prices'],watch,30,native_state=s,diagnostics=diag))
                self.assertIsNone(plan,stage)
                self.assertEqual(diag['reason'],'risk_or_target')

    def test_optional_hourly_context_cannot_change_a_location_decision(self):
        s=retest_state();before=m.classify(s['prices'],native_state=s)
        self.assertIsNotNone(before)
        s.update(hourly_direction=-1,four_hour_direction='UNKNOWN')
        self.assertEqual(before,m.classify(s['prices'],native_state=s))


if __name__=='__main__':unittest.main()
