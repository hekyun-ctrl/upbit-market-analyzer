import copy
import json
import tempfile
import unittest
from pathlib import Path

from test_lbank_entry_quality import structural_state
from lbank_signal_thesis import attach_thesis,assess_thesis,observed_stop_touch,entry_thesis_line,POLICY,TRACKING_SECONDS
from lbank_signal_risk import SignalRiskTracker,format_risk_alert
from lbank_performance import PerformanceBook
from lbank_native_entry import SOURCE
from lbank_trend_runner import runner_decision


def fixture(d=1):
    s=structural_state(d);entry=s['quote']
    p=dict(direction=d,side='롱' if d==1 else '숏',price=entry,stop=entry-d*200,
           target1=entry+d*600,target2=entry+d*1000,structure_anchor=entry-d*150,
           strategy='trend_retest',data_source=SOURCE,runner_enabled=True,
           quantity_btc=.01,net_loss=3.,entry_quality={'priority':{'support':entry-d*180}})
    return s,attach_thesis(p,s,9000)


def append_bar(s,start,close,low=None,high=None):
    o=s['five_bars'][-1]['close']
    s['five_bars'].append(dict(start=start,open=o,close=close,high=max(o,close)+1 if high is None else high,
                              low=min(o,close)-1 if low is None else low,volume=1.,quality='stream_closed'))
    s['last5_start']=start
    s['minute_bars'].append(dict(start=start+240,open=close,close=close,
                                high=close+1,low=close-1,volume=1.,quality='stream_closed'))


class FrozenThesisTests(unittest.TestCase):
    def test_attach_is_pure_and_freezes_the_original_level_for_both_sides(self):
        for d in (1,-1):
            s,p=fixture(d);original=copy.deepcopy(p)
            s['five_bars'][-1]['low']-=100
            self.assertEqual(p,original)
            self.assertEqual(p['followup_thesis']['anchor'],p['structure_anchor'])
            self.assertIn('완료 5분봉',entry_thesis_line(p))
            q=attach_thesis({k:v for k,v in p.items() if k!='followup_thesis'},s,9010)
            self.assertEqual({k:q[k] for k in p if k!='followup_thesis'},
                             {k:v for k,v in p.items() if k!='followup_thesis'})

    def test_reference_behind_hard_stop_is_not_an_early_invalidation_level(self):
        s,p=fixture();p['structure_anchor']=p['stop']-100
        self.assertIsNone(attach_thesis(p,s,9000)['followup_thesis']['anchor'])

    def test_minor_adversity_does_not_emit_urgent_or_caution_when_larger_direction_holds(self):
        for d in (1,-1):
            s,p=fixture(d);tr=SignalRiskTracker();sig=tr.add_sent_signal(p,9000)
            prices=[p['price']]*5+[p['price']-d*120]
            for now in (9015,9030,9045):self.assertEqual(tr.check(prices,now,s),[])
            self.assertEqual(sig.thesis_status,'intact')
            self.assertEqual(sig.bad_samples,1)
            self.assertEqual(sig.stop,p['stop'])

    def test_weak_evidence_and_repeated_poll_are_not_confirmed_reversal(self):
        s,p=fixture();s['closes15']=[81000.,80990.,80980.]
        tr=SignalRiskTracker();sig=tr.add_sent_signal(p,9000)
        prices=[p['price']]*5+[p['price']-120]
        for now in (9015,9030,9045):self.assertEqual(tr.check(prices,now,s),[])
        b=dict(s['minute_bars'][-1],start=9000);s['minute_bars'].append(b)
        events=tr.check(prices,9075,s);self.assertEqual(events[0][1],'risk_caution')
        tr.acknowledge(sig,'risk_caution')
        self.assertIn(sig,tr.signals)
        self.assertFalse(sig.priority_evidence['invalidated'])

    def test_missing_data_or_malformed_reference_is_unknown_not_reversal(self):
        s,p=fixture();tr=SignalRiskTracker();sig=tr.add_sent_signal(p,9000)
        prices=[p['price']]*5+[p['price']-120]
        e=tr.check(prices,9015,None)[0]
        self.assertEqual(e[1],'risk_caution');self.assertEqual(sig.thesis_status,'unknown')
        tr.acknowledge(sig,'risk_caution')
        self.assertEqual(tr.check(prices,9030,None),[])
        self.assertEqual(tr.check([p['stop']],9045,None)[0][1],'stop_breached')
        bad=dict(p['followup_thesis'],anchor='broken')
        self.assertEqual(assess_thesis(bad,s,1,p['price'],9000,9015)['status'],'unknown')

    def test_actual_frozen_level_close_break_invalidates_without_waiting_for_two_polls(self):
        for d in (1,-1):
            s,p=fixture(d);tr=SignalRiskTracker();sig=tr.add_sent_signal(p,9000)
            close=p['structure_anchor']-d*10
            append_bar(s,9000,close)
            event=tr.check([close],9305,s)[0]
            self.assertEqual(event[1],'early_invalidation')
            self.assertEqual(sig.priority_evidence['reason'],'frozen_structure_close_break')
            self.assertIn('고정 구조',format_risk_alert(sig,event[1],close,'KST'))

    def test_pre_entry_bar_cannot_invalidate_and_future_forming_wick_is_ignored(self):
        s,p=fixture();tr=SignalRiskTracker();tr.add_sent_signal(p,9000)
        s['five_bars'][-1]['close']=p['structure_anchor']-10
        s['five_bars'][-1]['low']=p['stop']-10
        self.assertEqual(tr.check([p['price']],9015,s),[])
        b=dict(s['minute_bars'][-1],start=9000,low=p['stop']-1,quality='forming')
        s['minute_bars'].append(b)
        self.assertIsNone(observed_stop_touch(s,1,p['stop'],9000,9015))

    def test_observed_post_entry_stop_wick_beats_rebound_and_target_queue(self):
        for d in (1,-1):
            s,p=fixture(d);tr=SignalRiskTracker();sig=tr.add_sent_signal(p,9000)
            b=dict(s['minute_bars'][-1],start=9000)
            b['low' if d==1 else 'high']=p['stop']-d
            s['minute_bars'].append(b)
            e=tr.check([p['target1']+d],9065,s)[0]
            self.assertEqual(e[1],'stop_breached')
            self.assertIn('완료 1분봉',format_risk_alert(sig,e[1],e[2],'KST'))

    def test_new_stop_does_not_apply_retroactively_before_tp1_ack(self):
        s,p=fixture();tr=SignalRiskTracker();sig=tr.add_sent_signal(p,9000)
        append_bar(s,9000,p['target1'],low=p['price']-10)
        e=tr.check([p['target1']],9305,s)[0]
        self.assertEqual(e[1],'target1_reached');tr.acknowledge(sig,e[1],now=9305)
        self.assertEqual(sig.stop_active_since,9305)
        self.assertIsNone(observed_stop_touch(s,1,sig.stop,sig.stop_active_since,9340))
        b=dict(s['minute_bars'][-1],start=9360,low=p['price']-1)
        s['minute_bars'].append(b)
        self.assertEqual(tr.check([p['target1']],9425,s)[0][1],'stop_breached')

    def test_confirmed_opposite_structure_is_distinct_from_missing_alignment(self):
        s,p=fixture();t=dict(p['followup_thesis'],anchor=None)
        bars=[]
        for i in range(30):
            c=82000-i*100
            bars.append(dict(start=2700+i*300,open=c+20,high=c+30,low=c-30,close=c,volume=1.,quality='stream_closed'))
        s.update(five_bars=bars,last5_start=11400,closes15=[80000.,79700.,79400.])
        self.assertEqual(assess_thesis(t,s,1,79100.,9000,11715)['reason'],'confirmed_higher_reversal')
        self.assertFalse(assess_thesis(t,s,1,79100.,11000,11715)['invalidated'])

    def test_six_hours_reviews_and_continues_then_tracking_limit_is_not_a_close_order(self):
        s,p=fixture();tr=SignalRiskTracker();sig=tr.add_sent_signal(p,9000)
        e=tr.check([p['price']+100],30600,None)[0]
        self.assertEqual(e[1],'time_review')
        note=format_risk_alert(sig,e[1],e[2],'KST')
        self.assertIn('추적 계속',note);self.assertNotIn('청산을 확인',note)
        tr.acknowledge(sig,'time_review');self.assertIn(sig,tr.signals)
        self.assertEqual(tr.check([p['price']+100],30630,None),[])
        e=tr.check([p['price']+100],9000+TRACKING_SECONDS,None)[0]
        self.assertEqual(e[1],'tracking_expired');self.assertIn('청산 신호가 아닙니다',format_risk_alert(sig,e[1],e[2],'KST'))
        tr.acknowledge(sig,e[1]);self.assertEqual(tr.signals,[])

    def test_caution_and_review_delivery_are_retryable_and_persisted(self):
        s,p=fixture()
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'state.json');tr=SignalRiskTracker(path);sig=tr.add_sent_signal(p,9000)
            for now in (30600,30615):self.assertEqual(tr.check([p['price']],now,None)[0][1],'time_review')
            tr.acknowledge(sig,'time_review')
            restored=SignalRiskTracker(path)
            self.assertEqual(restored.signals[0].followup_thesis,p['followup_thesis'])
            self.assertTrue(restored.signals[0].time_review_notified)
            self.assertEqual(restored.check([p['price']],30630,None),[])

    def test_runner_weak_reaction_cannot_override_the_same_frozen_thesis(self):
        s,p=fixture();s['rsi5']['rsi']=[50.,50.,50.]
        # This is a unit fixture, not a claimed historical replay.
        s['five_bars'][-1]['close']=s['five_bars'][-2]['close']-10
        from unittest.mock import patch
        kwargs=dict(direction=1,entry=p['price']-500,stop=p['price']-500,target2=p['target2'],
                    sent_at=8000,last_bar=-1,state=s,now=9015,price=p['price'])
        with patch('lbank_trend_runner.neutral_stall',return_value=True):
            self.assertEqual(runner_decision(**kwargs)['reason'],'neutral_exit')
            result=runner_decision(**kwargs,thesis_evidence={'invalidated':False,'status':'weakened'})
            self.assertTrue(result is None or result['reason'] not in ('neutral_exit','trend_exit'))

    def test_paper_cohort_keeps_new_policy_and_does_not_count_tracking_end_as_profit(self):
        s,p=fixture();book=PerformanceBook(':memory:',1000);self.addCleanup(book.db.close)
        book.add(1,p,9000)
        for now in (30600,9000+TRACKING_SECONDS):
            with book.db:book.db.execute("UPDATE signals SET last_at=? WHERE status='open'",(now-30,))
            book.observe(now,p['price']+100)
            row=book.db.execute("SELECT * FROM signals WHERE policy='trend_runner'").fetchone()
            self.assertEqual(row['status'],'open' if now==30600 else 'censored_tracking_limit')
        self.assertIsNone(row['net']);self.assertEqual(book.summary()['trend_runner']['censored'],1)
        saved=json.loads(row['plan']);self.assertEqual(saved['followup_thesis']['policy'],POLICY)
        self.assertEqual(saved['policy_version'],'native_atr_runner_frozen_thesis_v3')

    def test_unresolved_intrabar_stop_never_becomes_a_paper_win(self):
        s,p=fixture();book=PerformanceBook(':memory:',1000);self.addCleanup(book.db.close);book.add(1,p,9000)
        b=dict(s['minute_bars'][-1],start=9000,low=p['stop']-1);s['minute_bars'].append(b)
        book.observe(9065,p['price']+1,s)
        row=book.db.execute("SELECT * FROM signals WHERE policy='trend_runner'").fetchone()
        self.assertEqual(row['status'],'censored_intrabar_stop');self.assertIsNone(row['net'])
        self.assertEqual(book.summary()['trend_runner']['wins'],0)

    def test_paper_and_live_share_affirmative_structure_failure(self):
        s,p=fixture();tr=SignalRiskTracker();tr.add_sent_signal(p,9000)
        book=PerformanceBook(':memory:',1000);self.addCleanup(book.db.close);book.add(1,p,9000)
        close=p['structure_anchor']-10;append_bar(s,9000,close)
        with book.db:book.db.execute('UPDATE signals SET last_at=9290')
        book.observe(9305,close,s)
        self.assertEqual(tr.check([close],9305,s)[0][1],'early_invalidation')
        row=book.db.execute("SELECT * FROM signals WHERE policy='trend_runner'").fetchone()
        self.assertEqual(row['status'],'structure_exit');self.assertLess(row['net'],0)

    def test_extended_outcomes_preserve_price_result_but_do_not_invent_funding_net(self):
        s,p=fixture();book=PerformanceBook(':memory:',1000);self.addCleanup(book.db.close);book.add(1,p,9000)
        now=9000+7*3600
        with book.db:book.db.execute('UPDATE signals SET last_at=?',(now-30,))
        book.observe(now,p['stop']-1)
        row=book.db.execute("SELECT * FROM signals WHERE policy='trend_runner'").fetchone()
        self.assertEqual(row['status'],'censored_funding_unverified');self.assertIsNone(row['net'])
        end=json.loads(row['plan'])['extended_outcome']
        self.assertEqual(end['reason'],'stop');self.assertLess(end['fixed_cost_estimate_usdt'],0)

    def test_confirmed_failure_survives_delivery_failure_rebound_and_restart(self):
        s,p=fixture()
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'state.json');tr=SignalRiskTracker(path);tr.add_sent_signal(p,9000)
            close=p['structure_anchor']-10;append_bar(s,9000,close)
            self.assertEqual(tr.check([close],9305,s)[0][1],'early_invalidation')
            append_bar(s,9300,p['price']+100)
            restored=SignalRiskTracker(path)
            self.assertEqual(restored.check([p['price']+100],9605,s)[0][1],'early_invalidation')
            # A subsequent actual stop escalates the latched structural event.
            self.assertEqual(restored.check([p['stop']-1],9620,s)[0][1],'stop_breached')
            self.assertEqual(restored.check([p['price']],9635,s)[0][1],'stop_breached')

    def test_actual_screenshot_audit_does_not_invent_a_target_for_unreached_cases(self):
        from research.audit_signal_coherence import audit
        result=audit();cases={c['reference']:c for c in result['cases']}
        self.assertTrue(cases[8]['no_observed_stop_before_target1'])
        for reference in (9,10):
            self.assertFalse(cases[reference]['no_observed_stop_before_target1'])
            self.assertIsNone(cases[reference]['first_observed_target1_interval_kst'])


if __name__=='__main__':unittest.main()
