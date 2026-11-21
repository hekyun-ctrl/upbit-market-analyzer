"""Policy/integration tests; synthetic boundaries are not backtest profits."""
import copy
import json
import tempfile
import unittest
from pathlib import Path
from lbank_profit_protection import new_policy,observe,acknowledge,format_notice
from lbank_signal_thesis import assess_thesis
from lbank_signal_risk import SignalRiskTracker,format_target_alert
from lbank_telegram_positions import PositionState
from lbank_performance import PerformanceBook,leg_pnl
from test_lbank_signal_thesis import fixture,append_bar


class ProfitProtectionTests(unittest.TestCase):
    def armed(self,d=1):
        s,p=fixture(d)
        policy=new_policy(d,p['price'],p['stop'])
        state,event=observe(policy,None,direction=d,entry=p['price'],price=p['price']+d*400,now=9015,native_state=s)
        self.assertEqual(event,'profit_guard_armed')
        return s,p,policy,state

    def test_arm_is_not_tp1_and_guard_has_positive_modeled_net_for_both_sides(self):
        for d in (1,-1):
            s,p,policy,state=self.armed(d)
            self.assertLess(d*(state['best']-p['target1']),0)
            model=dict(price=p['price'],direction=d,fee=policy['fee'],slippage=policy['slippage'],
                       funding_reserve=policy['funding'],quantity_btc=1)
            self.assertAlmostEqual(leg_pnl(model,policy['breakeven'],1),0,places=7)
            self.assertGreater(leg_pnl(model,state['guard'],1),0)
            self.assertGreaterEqual(d*(state['best']-state['guard']),.5*state['atr'])
            self.assertIn('방향 반전·신규 진입 신호가 아닙니다',format_notice(state,'profit_guard_armed',state['best'],'KST','#1'))

    def test_small_gain_and_unknown_native_data_do_not_arm(self):
        for d in (1,-1):
            s,p=fixture(d);policy=new_policy(d,p['price'],p['stop'])
            for price,native in ((p['price']+d*100,s),(p['price']+d*400,None)):
                state,event=observe(policy,None,direction=d,entry=p['price'],price=price,now=9015,native_state=native)
                self.assertIsNone(event);self.assertNotIn('guard',state)

    def test_pre_entry_wicks_cannot_create_best_or_guard_and_time_never_moves_back(self):
        s,p=fixture();s['five_bars'][-1]['high']=p['target2']+1000
        policy=new_policy(1,p['price'],p['stop'])
        state,event=observe(policy,None,direction=1,entry=p['price'],price=p['price'],now=9015,native_state=s)
        self.assertEqual(state['best'],p['price']);self.assertIsNone(event)
        old=copy.deepcopy(state)
        self.assertEqual(observe(policy,state,direction=1,entry=p['price'],price=p['target2'],now=9000,native_state=s)[0],old)

    def test_guard_is_monotonic_and_requires_a_new_closed_bar_for_update(self):
        for d in (1,-1):
            s,p,policy,state=self.armed(d);acknowledge(state,'profit_guard_armed');guard=state['guard']
            state,event=observe(policy,state,direction=d,entry=p['price'],price=p['price']+d*900,now=9030,native_state=s)
            self.assertIsNone(event);self.assertEqual(state['guard'],guard)
            append_bar(s,9000,p['price']+d*800)
            state,event=observe(policy,state,direction=d,entry=p['price'],price=p['price']+d*900,now=9305,native_state=s)
            self.assertEqual(event,'profit_guard_updated');self.assertGreater(d*(state['guard']-guard),0)

    def test_guard_touch_survives_send_failure_rebound_and_restart(self):
        for d in (1,-1):
            s,p=fixture(d);p['profit_policy']=new_policy(d,p['price'],p['stop'])
            with tempfile.TemporaryDirectory() as td:
                path=str(Path(td)/'signals.json');tr=SignalRiskTracker(path);sig=tr.add_sent_signal(p,9000)
                e=tr.check([p['price']+d*400],9015,s)[0];self.assertEqual(e[1],'profit_guard_armed')
                # No delivery acknowledgement; price crosses the proposed line.
                guard=sig.profit_state['guard']
                self.assertEqual(tr.check([guard-d],9030,s)[0][1],'profit_guard_touched')
                restored=SignalRiskTracker(path);sig=restored.signals[0]
                self.assertEqual(restored.check([p['price']+d*400],9045,s)[0][1],'profit_guard_touched')
                restored.acknowledge(sig,'profit_guard_touched',9045);self.assertEqual(restored.signals,[])

    def test_original_stop_has_priority_and_legacy_signal_does_not_gain_new_rules(self):
        s,p=fixture();tr=SignalRiskTracker();sig=tr.add_sent_signal(p,9000)
        self.assertEqual(tr.check([p['price']+400],9015,s),[])
        p['profit_policy']=new_policy(1,p['price'],p['stop']);tr=SignalRiskTracker();sig=tr.add_sent_signal(p,9000)
        tr.check([p['price']+400],9015,s)
        self.assertEqual(tr.check([p['stop']-1],9030,s)[0][1],'stop_breached')

    def test_delivered_guard_cannot_be_loosened_at_tp1_transition(self):
        for d in (1,-1):
            s,p=fixture(d);p['profit_policy']=new_policy(d,p['price'],p['stop']);tr=SignalRiskTracker();sig=tr.add_sent_signal(p,9000)
            e=tr.check([p['price']+d*400],9015,s)[0];tr.acknowledge(sig,e[1],9015);guard=sig.profit_state['guard']
            e=tr.check([p['target1']],9030,s)[0];self.assertEqual(e[1],'target1_reached')
            self.assertIn(f'{guard:,.1f}',format_target_alert(sig,e[2],'KST'))
            tr.acknowledge(sig,e[1],9030)
            self.assertGreaterEqual(d*(sig.stop-guard),0)

    def test_post_activation_intrabar_guard_touch_is_latched_without_optimistic_fill(self):
        s,p,policy,state=self.armed();acknowledge(state,'profit_guard_armed')
        # A bar starting before activation cannot retrospectively touch a new guard.
        s['minute_bars'].append(dict(start=9000,open=p['price'],high=p['price']+500,
                                    low=p['price'],close=p['price']+400,quality='stream_closed'))
        state,event=observe(policy,state,direction=1,entry=p['price'],price=p['price']+400,now=9065,native_state=s)
        self.assertIsNone(event)
        b=dict(s['minute_bars'][-1],start=9060);s['minute_bars'].append(b)
        state,event=observe(policy,state,direction=1,entry=p['price'],price=p['price']+400,now=9125,native_state=s)
        self.assertEqual(event,'profit_guard_touched');self.assertEqual(state['touch']['period'],60)

    def test_confirmed_reversal_is_actionable_while_profitable_only_for_new_policy(self):
        s,p=fixture();t=dict(p['followup_thesis'],anchor=None,entry=78000.)
        bars=[]
        for i in range(30):
            c=82000-i*100
            bars.append(dict(start=2700+i*300,open=c+20,high=c+30,low=c-30,close=c,volume=1.,quality='stream_closed'))
        s.update(five_bars=bars,last5_start=11400,closes15=[80000.,79700.,79400.])
        self.assertFalse(assess_thesis(t,s,1,79100.,9000,11715)['invalidated'])
        self.assertTrue(assess_thesis(t,s,1,79100.,9000,11715,allow_profitable_reversal=True)['invalidated'])
        p.update(price=78000.,stop=77700.,target1=80000.,target2=81000.,followup_thesis=t,
                 profit_policy=new_policy(1,78000.,77700.))
        tr=SignalRiskTracker();tr.add_sent_signal(p,9000)
        self.assertEqual(tr.check([79100.],11715,s)[0][1],'early_invalidation')

    def test_paper_policy_uses_observed_worse_quote_and_keeps_baselines_separate(self):
        for d in (1,-1):
            s,p=fixture(d);p['profit_policy']=new_policy(d,p['price'],p['stop'])
            book=PerformanceBook(':memory:',1000);self.addCleanup(book.db.close);book.add(1,p,9000)
            book.observe(9015,p['price']+d*400,s)
            raw=book.db.execute("SELECT * FROM signals WHERE policy='trend_runner'").fetchone();saved=json.loads(raw['plan'])
            guard=saved['profit_state']['guard'];actual_quote=guard-d*5
            book.observe(9030,actual_quote,s)
            row=book.db.execute("SELECT * FROM signals WHERE policy='trend_runner'").fetchone()
            self.assertEqual(row['status'],'profit_guard_exit');self.assertAlmostEqual(row['net'],leg_pnl(saved,actual_quote,1))
            self.assertNotEqual(row['net'],leg_pnl(saved,guard,1))
            for raw in book.db.execute("SELECT * FROM signals WHERE policy!='trend_runner'"):
                self.assertEqual(raw['status'],'open');self.assertNotIn('profit_policy',json.loads(raw['plan']))

    def test_manual_ledger_uses_reported_entry_not_signal_reference_and_keeps_position_until_close(self):
        s,p=fixture()
        with tempfile.TemporaryDirectory() as td:
            path=str(Path(td)/'position.json');ledger=PositionState(path)
            ledger.open('long',p['price']+100,.01,p['stop'],p['target1'])
            self.assertEqual(ledger.profit_check(p['price']+200,9015,s),None)
            self.assertEqual(ledger.profit_check(p['price']+550,9030,s),'profit_guard_armed')
            guard=ledger.position['profit_state']['guard'];ledger.acknowledge_profit('profit_guard_armed')
            ledger.partial(.004)
            self.assertEqual(ledger.position['profit_state']['guard'],guard)
            ledger=PositionState(path)
            self.assertEqual(ledger.profit_check(guard-1,9045,s),'profit_guard_touched')
            ledger.acknowledge_profit('profit_guard_touched');self.assertIsNotNone(ledger.position)
            self.assertIsNone(ledger.profit_check(guard-2,9060,s));ledger.close();self.assertIsNone(ledger.position)

    def test_actual_reference_quote_replay_arms_before_tp1_without_claiming_a_fill(self):
        from research.replay_profit_protection import replay
        result=replay();events=result['events']
        self.assertEqual([e['reason'] for e in events],['profit_guard_armed','profit_guard_touched'])
        self.assertTrue(events[0]['at'].startswith('2026-10-19T10:32:'))
        self.assertTrue(events[1]['at'].startswith('2026-10-19T10:39:'))
        self.assertGreater(events[1]['price'],events[1]['guard'])  # Short gaps through: never ideal fill.
        self.assertFalse(result['actual_pnl'])

    def test_manual_proposal_cannot_loosen_a_user_reported_protected_stop(self):
        s,p=fixture()
        with tempfile.TemporaryDirectory() as td:
            ledger=PositionState(str(Path(td)/'position.json'))
            ledger.open('long',p['price'],.01,p['price']+250,p['target1'])
            reason=ledger.profit_check(p['price']+500,9015,s)
            self.assertEqual(reason,'profit_guard_armed')
            self.assertGreaterEqual(ledger.position['profit_state']['guard'],float(ledger.position['stop']))

    def test_paper_intrabar_touch_is_excluded_from_profit_claims(self):
        s,p=fixture();p['profit_policy']=new_policy(1,p['price'],p['stop'])
        book=PerformanceBook(':memory:',1000);self.addCleanup(book.db.close);book.add(1,p,9000)
        book.observe(9015,p['price']+400,s)
        s['minute_bars'].append(dict(start=9060,open=p['price']+400,close=p['price']+400,
                                    low=p['price'],high=p['price']+410,quality='stream_closed'))
        book.observe(9125,p['price']+400,s)
        row=book.db.execute("SELECT * FROM signals WHERE policy='trend_runner'").fetchone()
        self.assertEqual(row['status'],'censored_intrabar_profit_guard');self.assertIsNone(row['net'])

    def test_profit_policy_survives_runner_extension_being_disabled(self):
        s,p=fixture();p['runner_enabled']=False;p['profit_policy']=new_policy(1,p['price'],p['stop'])
        book=PerformanceBook(':memory:',1000);self.addCleanup(book.db.close);book.add(1,p,9000)
        self.assertEqual(book.db.execute("SELECT count(*) FROM signals WHERE policy='trend_runner'").fetchone()[0],1)

    def test_new_alert_displays_an_exact_kst_expiry_without_an_extra_wait(self):
        from test_lbank_compact_alert import CompactAlertTests
        from lbank_compact_alert import format_compact_entry_alert
        fixture=CompactAlertTests();fixture.setUp();p=fixture.plan
        p['profit_policy']=new_policy(1,p['price'],p['stop'])
        note=format_compact_entry_alert(p,'2026-10-19T10:23:34Z',reference_number=12,**fixture.args)
        self.assertIn('19:28:34까지',note);self.assertIn('1차 목표 전 수익 보호',note)
        self.assertLessEqual(len(note.splitlines()),10)

    def test_reported_position_reversal_warns_while_profitable_and_latches_on_restart(self):
        from unittest.mock import patch
        from lbank_telegram_positions import format_position_management
        s,p=fixture();bars=[]
        for i in range(30):
            c=82000-i*100
            bars.append(dict(start=2700+i*300,open=c+20,high=c+30,low=c-30,close=c,volume=1.,quality='stream_closed'))
        s.update(five_bars=bars,last5_start=11400,closes15=[80000.,79700.,79400.])
        with tempfile.TemporaryDirectory() as td:
            path=str(Path(td)/'position.json');ledger=PositionState(path)
            with patch('lbank_telegram_positions.time.time',return_value=9000):
                ledger.open('long',78000.,.01,77700.,81000.)
            self.assertEqual(ledger.profit_check(79100.,11715,s),'position_reversal')
            ledger=PositionState(path)
            self.assertEqual(ledger.profit_check(79200.,11730,None),'position_reversal')
            note=format_position_management(ledger.position,'position_reversal',79200.,'KST')
            self.assertIn('수익·손실 여부와 관계없이',note)
            ledger.acknowledge_profit('position_reversal');self.assertIsNotNone(ledger.position)
            self.assertIsNone(ledger.target_reached([82000.]))


if __name__=='__main__':unittest.main()
