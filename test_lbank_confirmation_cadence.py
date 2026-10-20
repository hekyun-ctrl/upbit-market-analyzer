"""Run the real monitor loop with an injected clock and no external I/O."""
import copy
import unittest
from contextlib import ExitStack
from types import SimpleNamespace
from unittest.mock import MagicMock,patch

import lbank_futures_monitor as monitor
from test_lbank_entry_quality import structural_state


class StopLoop(BaseException):
    pass


def run_clock(*,missing_at=None,journal_fails_at=None,reject_at=None,
              ready_at=None,qualify_at=None,stop_at=9075.,cancel_at=None):
    clock=SimpleNamespace(now=9000.);order=[];saved=[];sent=[];confirmed=[]
    state=structural_state();state['rsi5']['closes']=[b['close'] for b in state['five_bars']]
    p=state['quote'];plan=dict(direction=1,side='롱',strategy='breakout',price=p,
        stop=p-200,target1=p+600,target2=p+1000,rr1=1.5,m5=.2,m15=.3,
        entry_quality={'priority':{}})
    def sleep(_):
        clock.now+=15
        if clock.now>=stop_at:raise StopLoop()
    def snapshot(now,quote):
        fresh=copy.deepcopy(state);fresh['quote']=quote
        return (None if now==missing_at or ready_at is not None and now<ready_at else fresh),{}
    def confirm(prices,watch,age,**kwargs):
        order.append((clock.now,'confirm'));confirmed.append((clock.now,age,kwargs['native_state']['quote']))
        if clock.now==cancel_at:
            kwargs['diagnostics']['reason']=monitor.BREAKOUT_CONFIRMATION_REJECT
            return None
        return None if clock.now==reject_at else copy.deepcopy(plan)
    def sample(now,*args,**kwargs):
        if now==journal_fails_at:raise OSError('journal unavailable')
    position=MagicMock();position.position=None
    position.risk.return_value=None;position.profit_check.return_value=None;position.target_reached.return_value=None
    risk=MagicMock();risk.signals=[];risk.next_number=1
    risk.check.side_effect=lambda *a,**kw:order.append((clock.now,'risk')) or []
    risk.add_sent_signal.return_value=SimpleNamespace(number=1)
    book=MagicMock();book.observe.return_value=[];book.sample.side_effect=sample;book.daily_message.return_value=None
    feed=MagicMock();feed.snapshot.side_effect=snapshot
    research=MagicMock();research.observe.return_value=({},[])
    neutral=MagicMock();neutral.observe.return_value=[]
    exchange=MagicMock();exchange.market.return_value={'swap':True}
    calls=[]
    def classify(*args,**kwargs):
        calls.append(clock.now);order.append((clock.now,'classify'))
        if qualify_at is not None:return copy.deepcopy(plan) if clock.now>=qualify_at else None
        return copy.deepcopy(plan) if len(calls)==1 else None
    classifier=MagicMock(side_effect=classify)
    log=MagicMock()
    with ExitStack() as stack:
        for name,value in dict(POLL=60,RISK_POLL=15,STARTUP_TEST_ENABLED=False).items():stack.enter_context(patch.object(monitor,name,value))
        stack.enter_context(patch.object(monitor.time,'time',lambda:clock.now))
        stack.enter_context(patch.object(monitor.time,'sleep',sleep))
        stack.enter_context(patch.object(monitor.ccxt,'lbank',return_value=exchange))
        stack.enter_context(patch.object(monitor.threading,'Thread'))
        for name in ('NativeCandleThread','MarketContextThread','MacroMonitor','OrderBookShadowThread'):
            stack.enter_context(patch.object(monitor,name))
        for name in ('DivergenceStudy','TechniqueStudy'):stack.enter_context(patch.object(monitor,name,side_effect=RuntimeError('disabled in isolated test')))
        for name,obj in [('NativeEntryFeed',feed),('SignalRiskTracker',risk),('PositionState',position),('ResearchTracker',research),('NeutralExitStudy',neutral),('PerformanceBook',book)]:
            stack.enter_context(patch.object(monitor,name,return_value=obj))
        stack.enter_context(patch.object(monitor,'TelegramBot'))
        stack.enter_context(patch.object(monitor,'load_snapshots',return_value=([],
            {'day':'1970-01-01','count':0,'last_time':None,'last_side':None})))
        stack.enter_context(patch.object(monitor,'save_snapshots',side_effect=lambda path,symbol,poll,observations,*a:saved.append((clock.now,list(observations)))))
        stack.enter_context(patch.object(monitor,'get_perpetual_snapshot',side_effect=lambda _:(p+(clock.now-9000)/10,None)))
        stack.enter_context(patch.object(monitor,'native_entry_geometry',return_value={}))
        stack.enter_context(patch.object(monitor,'classify',classifier))
        stack.enter_context(patch.object(monitor,'confirm_watch',side_effect=confirm))
        stack.enter_context(patch.object(monitor,'verified_reversal_exception',return_value=False))
        for name in ('annotate_market_context','annotate_public_techniques','attach_thesis'):
            stack.enter_context(patch.object(monitor,name,side_effect=lambda p,*a:p))
        stack.enter_context(patch.object(monitor,'format_entry_alert',return_value='isolated test entry'))
        stack.enter_context(patch.object(monitor,'telegram',side_effect=lambda message:sent.append((clock.now,message)) or True))
        stack.enter_context(patch.object(monitor,'LOG',log))
        try:monitor.run()
        except StopLoop:pass
    errors=[a for a in log.error.call_args_list if a.args and a.args[0].startswith('LBank market error')]
    if errors:raise AssertionError(errors)
    return dict(confirmed=confirmed,sent=sent,saved=saved,book=book,risk=risk,order=order,
                classifier=classifier,classify_times=calls)


class ConfirmationCadenceTests(unittest.TestCase):
    def test_existing_native_gates_accept_30s_and_keep_no_chase_limit(self):
        from test_lbank_native_entry import NativeEntryTests
        fixture=NativeEntryTests('test_native_watch_and_confirmation_never_call_sampled_gates')
        fixture.setUp();self.addCleanup(fixture.doCleanups)
        state,_=fixture.snapshot();state['rsi5']['rsi']=[84.,85.]
        watch=monitor.classify(state['prices'],native_state=state)
        self.assertIsNotNone(watch)
        self.assertIsNone(monitor.confirm_watch(state['prices'],watch,29.999,native_state=state))
        self.assertIsNotNone(monitor.confirm_watch(state['prices'],watch,30.,native_state=state))
        state['quote']=watch['price']*1.0011;diagnostics={}
        self.assertIsNone(monitor.confirm_watch(state['prices'],watch,30.,native_state=state,diagnostics=diagnostics))
        self.assertEqual(diagnostics['reason'],'price_deviation')

    def test_live_loop_confirms_at_30s_without_waiting_for_next_minute(self):
        r=run_clock()
        self.assertEqual([t for t,_,_ in r['confirmed']],[9030.])
        self.assertEqual(r['confirmed'][0][1],30.)
        self.assertEqual([t for t,_ in r['sent']],[9030.])
        self.assertEqual(r['risk'].add_sent_signal.call_count,1)
        self.assertEqual(r['classifier'].call_count,2)  # t=0,60; no fast setup scans
        samples=sorted({t for _,rows in r['saved'] for t,_ in rows})
        self.assertEqual(samples,[9000.,9060.])
        self.assertLess(r['order'].index((9030.,'risk')),r['order'].index((9030.,'confirm')))

    def test_rejection_rechecks_on_next_risk_tick_with_fresh_quote(self):
        r=run_clock(reject_at=9030.)
        self.assertEqual([t for t,_,_ in r['confirmed']],[9030.,9045.])
        self.assertGreater(r['confirmed'][1][2],r['confirmed'][0][2])
        self.assertEqual([t for t,_ in r['sent']],[9045.])

    def test_native_gap_cancels_watch_before_fast_confirmation(self):
        r=run_clock(missing_at=9030.)
        self.assertEqual(r['confirmed'],[]);self.assertEqual(r['sent'],[])

    def test_fast_confirmation_fails_closed_on_journal_failure(self):
        r=run_clock(journal_fails_at=9030.)
        self.assertEqual(r['confirmed'],[]);self.assertEqual(r['sent'],[])

    def test_new_ready_native_bar_does_not_wait_for_price_sample_timer(self):
        r=run_clock(ready_at=9015.,qualify_at=9015.,stop_at=9120.)
        self.assertEqual([t for t,_ in r['sent']],[9045.])
        self.assertEqual(r['confirmed'][0][1],30.)
        self.assertEqual(sorted({t for _,rows in r['saved'] for t,_ in rows}),[9000.,9060.])
        self.assertEqual(r['classifier'].call_count,2)  # first-ready + periodic; not every quote
        self.assertLess(r['order'].index((9015.,'risk')),r['order'].index((9015.,'classify')))

    def test_new_ready_scan_requires_journal_and_does_not_create_an_early_watch(self):
        r=run_clock(ready_at=9015.,qualify_at=9015.,journal_fails_at=9015.,stop_at=9075.)
        self.assertNotIn(9015.,r['classify_times'])
        self.assertEqual([t for t,_ in r['sent']],[9060.])  # retry at 30, confirm at 60

    def test_closed_bar_detection_does_not_override_rejected_candidate(self):
        r=run_clock(ready_at=9015.,qualify_at=9200.,stop_at=9120.)
        self.assertEqual(r['sent'],[]);self.assertEqual(r['confirmed'],[])

    def test_lost_trigger_cancels_candidate_instead_of_waiting_to_send_late(self):
        r=run_clock(cancel_at=9030.)
        self.assertEqual([t for t,_,_ in r['confirmed']],[9030.])
        self.assertEqual(r['sent'],[])


if __name__=='__main__':unittest.main()
