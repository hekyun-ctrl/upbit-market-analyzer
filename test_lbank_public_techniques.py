import copy
import csv
import io
import json
import sqlite3
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import lbank_public_techniques as p
import lbank_futures_monitor as m
from lbank_native_entry import NativeEntryFeed, SOURCE, wilder_rsi_series
from lbank_native_candles import NativeCandleStore
from lbank_compact_alert import format_compact_entry_alert
from research.audit_aoa_public import audit as archive_audit


def candles(closes,period=300):
    result=[]
    for i,c in enumerate(closes):
        o=closes[i-1] if i else c
        result.append(dict(start=180000+i*period,open=o,close=c,high=max(o,c)+.1,
                           low=min(o,c)-.1,volume=1.,quality='stream_closed'))
    return result


def bb_bars(direction=1):
    bars=candles([100.]*21+[100.5])
    bars[-1]['low']=95.
    if direction==-1:
        for b in bars:
            b.update(open=200-b['open'],close=200-b['close'],high=200-b['low'],low=200-b['high'])
    return bars


def paper_state():
    bars=bb_bars();available=bars[-1]['start']+300
    minutes=candles([100.2]*64+[100.5],60)
    for i,b in enumerate(minutes):b['start']=available+(i-64)*60
    return dict(source=SOURCE,five_bars=bars,minute_bars=minutes,prices=[b['close'] for b in minutes],
                rsi5={'rsi':[49.,51.]},quote=100.5,last5_start=bars[-1]['start'])


class PublicTechniqueTests(unittest.TestCase):
    def test_public_wallet_audit_excludes_canceled_cashflows_and_blank_rows(self):
        text=io.StringIO()
        writer=csv.writer(text)
        writer.writerow(['date','transacttype','amount','walletbalance','transactstatus'])
        writer.writerows([['2018-01-01','Deposit','100000000','100000000','Completed'],
                         ['2018-01-02','Withdrawal','-20000000','100000000','Canceled'],
                         ['2018-01-03','RealisedPNL','50000000','150000000','Completed'],
                         ['','','','','']])
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'public.zip')
            with zipfile.ZipFile(path,'w') as archive:archive.writestr('aoa-wallet.csv',text.getvalue())
            result=archive_audit(path)
        self.assertTrue(result['wallet_completed_cashflow_reconciled'])
        self.assertEqual(result['wallet_transaction_statuses'],{'Completed':2,'Canceled':1})
        self.assertNotIn('Withdrawal',result['wallet_completed_amount_btc_by_type'])
        self.assertEqual(result['wallet_blank_rows_excluded'],1)
        self.assertFalse(result['win_rate_computed'])

    def test_open_and_close_sources_population_sd_and_long_short_symmetry(self):
        for direction in (1,-1):
            bars=bb_bars(direction);now=bars[-1]['start']+300
            ctx=p.analyze_bars(bars,now)
            events=[e for e in ctx['setups'] if e['strategy']=='double_bb_5m']
            self.assertEqual(len(events),1)
            self.assertEqual(events[0]['direction'],direction)
            self.assertEqual(events[0]['details']['lower4'],100.)
            self.assertEqual(events[0]['details']['upper4'],100.)
            self.assertAlmostEqual(events[0]['details']['sma20_slope'],direction*.025)
        self.assertEqual(p.band([1,1,3,3],2),(0,4))

    def test_grouped_declines_merge_near_closes_and_are_not_three_red_candles(self):
        bars=candles([100.]*15+[99.7,99.71,99.4,99.41,99.4,99.4,99.1])
        events=p.analyze_bars(bars,bars[-1]['start']+300)['setups']
        self.assertTrue(any(e['strategy']=='grouped_decline_5m' for e in events))
        tiny=candles([100.]*19+[99.99,99.98,99.97])
        self.assertFalse(any(e['strategy']=='grouped_decline_5m' for e in p.analyze_bars(tiny,tiny[-1]['start']+300)['setups']))

    def test_box_is_frozen_before_breakout_and_retest(self):
        bars=candles([100.]*20+[100.8,100.6])
        bars[-2]['open']=100.;bars[-1].update(open=100.3,low=100.09)
        now=bars[-1]['start']+300
        events=[e for e in p.analyze_bars(bars,now)['setups'] if e['strategy']=='box_retest_5m']
        self.assertEqual(len(events),1)
        self.assertAlmostEqual(events[0]['details']['box_high'],100.1)
        self.assertEqual(events[0]['details']['box_end'],bars[-3]['start'])
        later=dict(start=now,open=100.6,close=100.7,high=150.,low=100.4,volume=3.,quality='stream_closed')
        after=p.analyze_bars(bars+[later],now+300)
        same=[e for e in after['setups'] if e['id']==events[0]['id']]
        self.assertEqual(same,events)

    def test_source_gap_forming_corruption_future_and_stale_data_fail_only_optional_module(self):
        for mutation in (lambda b:b.pop(10),lambda b:b[-1].update(quality='forming'),
                         lambda b:b[-1].update(volume=-1),lambda b:b[-1].update(close=float('nan'))):
            bars=bb_bars();now=bars[-1]['start']+300;mutation(bars)
            self.assertFalse(p.analyze_bars(bars,now)['ready'])
        bars=bb_bars();now=bars[-1]['start']+300
        self.assertFalse(p.analyze_bars(bars,now-1)['ready'])
        self.assertFalse(p.analyze_bars(bars,now+300)['ready'])
        self.assertFalse(p.analyze({'source':'spot','five_bars':bars},now)['ready'])

    def test_hourly_is_not_a_renamed_five_minute_bar_and_no_partial_hour(self):
        bars=candles([100.]*263+[100.5]);bars[-1]['low']=95.
        now=bars[-1]['start']+300
        hours=p.aggregate_hourly(bars)
        self.assertEqual(len(hours),22)
        self.assertEqual(hours[-1]['volume'],12.)
        ctx=p.analyze_bars(bars,now)
        self.assertTrue(ctx['hourly_ready'])
        self.assertTrue(any(e['strategy']=='double_bb_1h' for e in ctx['setups']))
        self.assertEqual(len(p.aggregate_hourly(bars[:-1])),21)
        self.assertFalse(p.analyze_bars(bars[-250:],now)['hourly_ready'])

    def test_reaction_is_a_fresh_minute_starting_after_setup_and_invalidates_on_anchor_break(self):
        state=paper_state();now=state['minute_bars'][-1]['start']+60
        event=[e for e in p.analyze(state,now)['setups'] if e['strategy']=='double_bb_5m'][0]
        state['minute_bars'][-1].update(close=100.6,high=100.7)
        self.assertTrue(p.confirmation(state,event,now))
        self.assertFalse(p.confirmation(state,event,now-60))
        self.assertFalse(p.confirmation(state,dict(event,available_at=now),now))
        state['minute_bars'][-1]['low']=94.
        self.assertFalse(p.confirmation(state,event,now))

    def test_annotation_cannot_change_price_risk_qualification_or_cooldown(self):
        prices=[80000.+25*i+(45 if i%2 else 0) for i in range(60)]
        plan=m.classify(prices);self.assertIsNotNone(plan);old=copy.deepcopy(plan)
        annotated=p.annotate(plan,paper_state(),186660.,m.REF_BALANCE)
        self.assertEqual({k:annotated[k] for k in old},old)
        self.assertEqual(plan,old)
        self.assertEqual(m.classify(prices),plan)
        for t in (0,120,3600):
            state={'day':'1970-01-01','count':1,'last_time':t,'last_side':'숏'}
            self.assertEqual(m.alert_gate(4000,plan['side'],copy.deepcopy(state),plan),
                             m.alert_gate(4000,plan['side'],copy.deepcopy(state),annotated))
        with patch.object(p,'analyze',side_effect=RuntimeError('feed down')):
            self.assertEqual(p.annotate(plan,None,186660.,m.REF_BALANCE),plan)

    def test_seed_exposure_is_not_exchange_leverage_and_loss_percentage_is_explicit(self):
        plan=dict(side='롱',direction=1,strategy='trend_retest',price=100.,stop=99.,target1=102.,target2=104.,
                  margin=50.,notional=1500.,net_loss=5.,net_gain1=8.,net_gain2=14.)
        note=format_compact_entry_alert(plan,'2026-10-19T00:00:00Z',reference_balance=1000.,leverage=30.,max_margin_fraction=.15)
        self.assertIn('레버리지 30배',note);self.assertIn('기준시드 노출 1.50배',note)
        self.assertIn('기준시드 0.50%',note)

    def test_paper_watch_restart_dedup_costs_and_failure_do_not_call_live_state(self):
        state=paper_state();now=state['minute_bars'][-1]['start']+60
        event=p.analyze(state,now)['setups'][0]
        ctx=dict(ready=True,setups=[event],hourly_ready=False)
        plan=dict(direction=1,price=100.5,stop=99.,target1=103.,target2=105.,quantity_btc=1.,net_loss=2.,strategy=event['strategy'],runner_enabled=False)
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'study.sqlite3')
            study=p.TechniqueStudy(path,1000.,fee=.0007,slippage=.0003,funding=.0002)
            with patch.object(p,'analyze',return_value=ctx),patch.object(p,'confirmation',return_value=True):
                a=study.step(now,100.5,state,lambda *a,**k:plan,lambda *a:(True,'ok',{}),baseline_reason='no_pattern')
                self.assertEqual(a['decisions'][0]['reason'],'paper_watch')
                study.book.db.close()
                study=p.TechniqueStudy(path,1000.,fee=.0007,slippage=.0003,funding=.0002)
                b=study.step(now+60,100.5,state,lambda *a,**k:plan,lambda *a:(True,'ok',{}),baseline_reason='no_pattern')
                self.assertEqual(b['decisions'][0]['reason'],'paper_opened')
                study.step(now+120,100.5,state,lambda *a,**k:plan,lambda *a:(True,'ok',{}),baseline_reason='qualified')
                self.assertEqual(study.summary()[event['strategy']]['entries'],1)
                frozen=json.loads(study.book.db.execute("SELECT plan FROM signals WHERE policy='levels'").fetchone()[0])
                self.assertEqual(frozen['fee'],.0007);self.assertEqual(frozen['slippage'],.0003)
                self.assertEqual(frozen['public_techniques']['baseline_reason'],'no_pattern')
                study.step(now+400,100.5,state,lambda *a,**k:None,lambda *a:(True,'ok',{}),baseline_reason='no_pattern')
                self.assertEqual(study.summary()[event['strategy']]['censored'],1)
            study.book.db.close()

    def test_wider_read_keeps_original_250_bar_rsi_identical(self):
        with tempfile.TemporaryDirectory() as folder:
            path=str(Path(folder)/'native.sqlite3');store=NativeCandleStore(path)
            start=180000; total=1500
            with store.db:
                for i in range(total):
                    t=start+i*60;close=80000+i+(20 if i%2 else 0)
                    store.db.execute('INSERT INTO native_bars VALUES(?,?,?,?,?,?,?,?,?,?)',
                        (60,t,close-2,close+5,close-5,close,1.,float(close),t+59,'stream_closed'))
                for t in range(start,start+total*60,300):store._aggregate(t)
            state,diag=NativeEntryFeed(path).snapshot(start+total*60+20,81500.)
            self.assertTrue(diag['entry_source_switched'])
            self.assertEqual(len(state['five_bars']),250)
            self.assertEqual(len(state['research_five_bars']),300)
            original=[r[0] for r in store.db.execute('SELECT close FROM native_bars WHERE period=300 ORDER BY start DESC LIMIT 250')][::-1]
            self.assertEqual(state['rsi5']['rsi'],wilder_rsi_series(original))
            store.db.close()


if __name__=='__main__':unittest.main()
