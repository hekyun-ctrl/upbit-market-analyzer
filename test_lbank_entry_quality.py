import copy
import json
import unittest

from lbank_entry_quality import confirmation_quality, POLICY
from lbank_native_entry import SOURCE
from lbank_performance import PerformanceBook
from lbank_signal_risk import SignalRiskTracker


def future_confirmation_state(state, watch):
    """Synthetic future bars for boundary tests, never replay market data."""
    result = copy.deepcopy(state)
    d, p, start = watch['direction'], watch['price'], watch['native_watch_1m_start']
    for i in range(1,4):
        close = p + d*(35+25*(i-1))
        opening = close-d*5
        result['minute_bars'].append(dict(start=start+i*60,open=opening,
            high=max(opening,close)+2,low=min(opening,close)-2,close=close,
            quality='stream_closed',volume=1))
    result['prices'] = [b['close'] for b in result['minute_bars']]
    result['quote'] = p+d*85
    return result


def structural_state(d=1):
    bars=[]
    for i in range(30):
        c=80000.+d*40*i
        bars.append(dict(start=i*300,open=c-d*5,high=c+150,low=c-150,
                         close=c,volume=1.,quality='stream_closed'))
    quote=bars[-1]['close']+d*5
    minutes=[dict(start=t,open=quote-d*3,high=quote+5,low=quote-5,
                  close=quote,volume=1.,quality='stream_closed')
             for t in range(9000-65*60,9000,60)]
    return dict(source=SOURCE,quote=quote,five_bars=bars,minute_bars=minutes,
                prices=[b['close'] for b in minutes],last5_start=8700,
                closes15=[bars[i]['close'] for i in (17,20,23,26,29)],
                rsi5=dict(rsi=[55.,56.,57.] if d==1 else [45.,44.,43.]))


class EntryQualityTests(unittest.TestCase):
    def fixture(self,d=1):
        s=structural_state(d);p=s['quote']
        w=dict(direction=d,price=p,native_watch_1m_start=8940,
               strategy='breakout',level=p-d*20,stop=p-d*200,
               target1=p+d*600,target2=p+d*1000,quantity_btc=.01,net_loss=3)
        return s,w

    def test_no_universal_two_minute_wait_for_both_directions(self):
        for d in (1,-1):
            s,w=self.fixture(d);before=copy.deepcopy((s,w))
            ok,reason,evidence=confirmation_quality(s,w)
            self.assertTrue(ok,reason)
            self.assertFalse(evidence['requires_two_minute_wait'])
            self.assertFalse(evidence['minute_followthrough'])
            self.assertTrue(evidence['priority']['dominant_intact'])
            self.assertEqual((s,w),before)

    def test_small_regression_cannot_veto_intact_larger_structure(self):
        for d in (1,-1):
            s,w=self.fixture(d);s['quote']-=d*10
            ok,_,evidence=confirmation_quality(s,w)
            self.assertTrue(ok)
            self.assertLess(evidence['signed_progress_pct'],0)
            self.assertTrue(evidence['priority']['dominant_intact'])

    def test_weaker_evidence_returns_to_existing_gates_without_new_wait(self):
        s,w=self.fixture();s['closes15']=[80500.,80400.,80300.]
        ok,_,evidence=confirmation_quality(s,w)
        self.assertTrue(ok)
        self.assertFalse(evidence['priority']['dominant_intact'])
        self.assertFalse(evidence['requires_two_minute_wait'])

    def test_actual_candidate_stop_and_breakout_failure_are_not_overridden(self):
        for d in (1,-1):
            s,w=self.fixture(d);s['quote']=w['stop']
            self.assertEqual(confirmation_quality(s,w)[1],'watch_structure_stop_crossed')
            s,w=self.fixture(d);s['quote']=w['level']
            self.assertEqual(confirmation_quality(s,w)[1],'breakout_level_not_held')

    def test_post_candidate_stop_cross_cannot_be_erased_by_later_recovery(self):
        s,w=self.fixture();s=future_confirmation_state(s,w)
        s['minute_bars'][-2]['low']=w['stop']-1
        self.assertEqual(confirmation_quality(s,w)[1],'watch_structure_stop_crossed')

    def test_corrupted_native_confirmation_evidence_stays_blocked(self):
        for mutation in ('gap','forming','nan','missing'):
            s,w=self.fixture();s=future_confirmation_state(s,w)
            if mutation=='gap':s['minute_bars'][-1]['start']+=60
            elif mutation=='forming':s['minute_bars'][-1]['quality']='forming'
            elif mutation=='nan':s['minute_bars'][-1]['close']=float('nan')
            else:del w['native_watch_1m_start']
            self.assertFalse(confirmation_quality(s,w)[0],mutation)

    def test_stale_upper_bars_never_grant_dominance(self):
        from lbank_entry_quality import direction_priority
        s,w=self.fixture()
        for now in (9301.,9600.):
            self.assertFalse(direction_priority(s,1,s['quote'],w['target1'],now)['dominant_intact'])

    def test_swings_and_15m_alignment_have_priority_over_minute_body(self):
        from lbank_entry_quality import direction_priority
        s,w=self.fixture()
        self.assertTrue(direction_priority(s,1,s['quote'],w['target1'],9020)['dominant_intact'])
        s['five_bars'][-1]['low']=s['five_bars'][-2]['low']-10
        self.assertFalse(direction_priority(s,1,s['quote'],w['target1'],9020)['dominant_intact'])

    def test_caution_does_not_remove_signal_or_close_model(self):
        from lbank_signal_risk import format_risk_alert
        for d in (1,-1):
            s,w=self.fixture(d);w['entry_quality']=confirmation_quality(s,w)[2]
            tracker=SignalRiskTracker();sig=tracker.add_sent_signal(w,9000)
            prices=[w['price']]*5+[w['price']-d*150]
            self.assertEqual(tracker.check(prices,9015,native_state=s),[])
            event=tracker.check(prices,9030,native_state=s)[0]
            self.assertEqual(event[1],'risk_caution')
            note=format_risk_alert(sig,'risk_caution',prices[-1],'KST')
            self.assertIn('단기 역행 주의',note)
            tracker.acknowledge(sig,'risk_caution')
            self.assertIn(sig,tracker.signals)
            self.assertEqual(tracker.check(prices,9045,native_state=s),[])
            book=PerformanceBook(':memory:',1000);self.addCleanup(book.db.close)
            book.add(sig.number,w,9000)
            book.record_caution(sig.number,9000,9030,prices[-1],sig.priority_evidence)
            book.record_caution(sig.number,9000,9030,prices[-1],sig.priority_evidence)
            self.assertEqual(book.summary()['warning_exit']['open'],1)
            self.assertEqual(book.db.execute('SELECT count(*) FROM risk_cautions').fetchone()[0],1)

    def test_caution_escalates_when_upper_evidence_weakens(self):
        s,w=self.fixture();w['entry_quality']=confirmation_quality(s,w)[2]
        tr=SignalRiskTracker();sig=tr.add_sent_signal(w,9000)
        prices=[w['price']]*5+[w['price']-150]
        tr.check(prices,9015,native_state=s);tr.check(prices,9030,native_state=s)
        tr.acknowledge(sig,'risk_caution')
        s['closes15']=[80500.,80400.,80300.]
        self.assertEqual(tr.check(prices,9045,native_state=s)[0][1],'early_invalidation')

    def test_hard_stop_always_precedes_dominant_evidence(self):
        for d in (1,-1):
            s,w=self.fixture(d);w['entry_quality']=confirmation_quality(s,w)[2]
            tr=SignalRiskTracker();tr.add_sent_signal(w,9000)
            self.assertEqual(tr.check([w['stop']-d],9015,native_state=s)[0][1],'stop_breached')

    def test_legacy_signals_keep_their_original_risk_policy(self):
        s,w=self.fixture();tr=SignalRiskTracker();tr.add_sent_signal(w,9000)
        p=[w['price']]*5+[w['price']-150]
        tr.check(p,9015,native_state=s)
        self.assertEqual(tr.check(p,9030,native_state=s)[0][1],'early_invalidation')

    def test_actual_post_entry_stop_wick_cannot_be_overridden_by_upper_trend(self):
        for d in (1,-1):
            s,w=self.fixture(d);w['entry_quality']=confirmation_quality(s,w)[2]
            tr=SignalRiskTracker();tr.add_sent_signal(w,8800)
            s['minute_bars'][-1]['low' if d==1 else 'high']=w['stop']-d
            p=[w['price']]*5+[w['price']-d*150]
            tr.check(p,9015,native_state=s)
            self.assertEqual(tr.check(p,9030,native_state=s)[0][1],'early_invalidation')
            self.assertTrue(tr.signals[0].priority_evidence['post_entry_stop_wick'])

    def test_caution_state_survives_restart_and_retries_until_acknowledged(self):
        import tempfile,os
        with tempfile.TemporaryDirectory() as folder:
            path=os.path.join(folder,'signals.json')
            s,w=self.fixture();w['entry_quality']=confirmation_quality(s,w)[2]
            tr=SignalRiskTracker(path);sig=tr.add_sent_signal(w,9000)
            p=[w['price']]*5+[w['price']-150]
            tr.check(p,9015,native_state=s)
            self.assertEqual(tr.check(p,9030,native_state=s)[0][1],'risk_caution')
            self.assertEqual(tr.check(p,9045,native_state=s)[0][1],'risk_caution')
            tr.acknowledge(sig,'risk_caution')
            restored=SignalRiskTracker(path)
            self.assertEqual(restored.signals[0].entry_priority_policy,POLICY)
            self.assertEqual(restored.check(p,9060,native_state=s),[])

    def test_entry_evidence_is_frozen_in_accounting(self):
        s,w=self.fixture();w['entry_quality']=confirmation_quality(s,w)[2]
        book=PerformanceBook(':memory:',1000);self.addCleanup(book.db.close);book.add(1,w,9000)
        w['entry_quality']['priority']['support']=0
        saved=json.loads(book.db.execute('SELECT plan FROM signals LIMIT 1').fetchone()[0])
        self.assertEqual(saved['entry_quality']['policy'],POLICY)
        self.assertGreater(saved['entry_quality']['priority']['support'],0)

    def test_known_target_obstacle_prevents_dominant_classification(self):
        from lbank_entry_quality import direction_priority
        for d in (1,-1):
            s,w=self.fixture(d)
            s['five_bars'][-6]['high' if d==1 else 'low']=w['target1']-d*5
            result=direction_priority(s,d,s['quote'],w['target1'],9020)
            self.assertEqual(result['target_room'],'conflict')
            self.assertFalse(result['dominant_intact'])

    def test_larger_structure_can_outweigh_weak_minute_retest_but_not_cost_gate(self):
        from unittest.mock import patch
        import lbank_futures_monitor as m
        from test_lbank_native_entry import NativeEntryTests
        f=NativeEntryTests();f.setUp();self.addCleanup(f.doCleanups)
        s,_=f.snapshot();s['rsi5']['rsi']=[83.,84.,85.]
        w=m.classify(s['prices'],native_state=s)
        w.update(strategy='trend_retest',structure_anchor=w['price']-150,level=w['price']-100)
        self.assertTrue(confirmation_quality(s,w)[2]['priority']['dominant_intact'])
        with patch.object(m,'confirm_structure',return_value=False), \
             patch.object(m,'trend_retest_momentum_gate',return_value=(False,'weak',0,0)):
            p=m.confirm_watch(s['prices'],w,60,native_state=s)
            self.assertIsNotNone(p)
            self.assertFalse(p['entry_quality']['requires_two_minute_wait'])
            with patch.object(m,'calculate_plan',return_value=None):
                diag={}
                self.assertIsNone(m.confirm_watch(s['prices'],w,60,native_state=s,diagnostics=diag))
                self.assertEqual(diag['reason'],'risk_or_target')


if __name__=='__main__':unittest.main()
