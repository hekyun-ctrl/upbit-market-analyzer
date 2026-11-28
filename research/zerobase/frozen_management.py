"""Frozen-entry full existing management replay. No messages, orders or live edits."""
import argparse,copy,hashlib,importlib.util,json,logging,math,sys
from collections import Counter,defaultdict
from datetime import datetime,timezone
from pathlib import Path
import lbank_futures_monitor as m
from lbank_performance import PerformanceBook
from lbank_signal_risk import SignalRiskTracker
from lbank_profit_protection import new_policy
from lbank_native_entry import SOURCE,wilder_rsi_series
from test_lbank_signal_thesis import fixture,append_bar

class AuditBook(PerformanceBook):
    def __init__(self):
        super().__init__(':memory:',m.REF_BALANCE);self.legs=[]
    def _close(self,row,ts,price,reason,p):
        result=super()._close(row,ts,price,reason,p)
        self.legs.append(dict(id=row['id'],policy=row['policy'],at=ts,price=price,
            fraction=.5 if row['stage'] else 1.,kind='exit',reason=result['status']))
        return result
    def observe(self,ts,price,native_state=None):
        before={(r['id'],r['policy']):r['stage'] for r in self.db.execute("SELECT * FROM signals WHERE status='open'")}
        result=super().observe(ts,price,native_state)
        for row in self.db.execute('SELECT * FROM signals'):
            if before.get((row['id'],row['policy']))==0 and row['stage']==1:
                p=json.loads(row['plan']);self.legs.append(dict(id=row['id'],policy=row['policy'],at=ts,price=p['target1'],fraction=.5,kind='partial',reason='TP1'))
        return result

class Engine:
    def __init__(self):self.book=AuditBook();self.tracker=SignalRiskTracker();self.events=[];self.quotes=[]
    def add(self,plan,now,reference=None):
        if reference is not None:assert self.tracker.next_number==reference
        sig=self.tracker.add_sent_signal(plan,now)
        self.book.add(sig.number,plan,now,fee=m.TAKER_FEE,slippage=m.STOP_SLIPPAGE,funding=m.FUNDING_BUFFER)
        return sig
    def step(self,now,price,state,ack=True):
        # Exact production order: observation accounting BEFORE risk notices.
        outcomes=self.book.observe(now,price,native_state=state)
        current=[]
        for sig,reason,observed in self.tracker.check(self.quotes[-5:]+[price],now,native_state=state):
            current.append(reason);self.events.append(dict(reference=sig.number,sent_at=sig.sent_at,at=now,reason=reason,price=observed,ack=ack))
            if ack:
                if reason=='risk_caution':self.book.record_caution(sig.number,sig.sent_at,now,observed,sig.priority_evidence)
                if reason=='early_invalidation':self.book.early_warning(sig.number,sig.sent_at,now,observed)
                self.tracker.acknowledge(sig,reason=reason,now=now)
        self.quotes.append(price)
        return current
    def close(self):self.book.db.close()

def profit_plan(p):
    p=copy.deepcopy(p);p['profit_policy']=new_policy(p['direction'],p['price'],p['stop'],m.TAKER_FEE,m.STOP_SLIPPAGE,m.FUNDING_BUFFER)
    return p

def row(engine,policy='trend_runner'):return dict(engine.book.db.execute('SELECT * FROM signals WHERE policy=?',(policy,)).fetchone())

def golden_cases():
    results=[]
    for d in (1,-1):
        state,plan=fixture(d);p=profit_plan(plan)
        e=Engine();sig=e.add(p,9000)
        reasons=e.step(9015,p['target1'],state)
        assert 'target1_reached' in reasons and row(e)['stage']==1 and sig.target1_notified
        assert d*(sig.stop-p['price'])>=0
        results.append(dict(case='TP1_partial_then_monotonic_remaining_stop',direction=d,reasons=reasons,stage=row(e)['stage']));e.close()
        e=Engine();e.add(p,9000)
        assert e.step(9015,p['price']+d*100,state)==[]
        assert e.step(9030,p['price']+d*50,state)==[]
        assert row(e)['status']=='open'
        results.append(dict(case='small_retracement_not_reversal',direction=d,status='open'));e.close()
        e=Engine();sig=e.add(p,9000)
        assert 'profit_guard_armed' in e.step(9015,p['price']+d*400,state)
        guard=sig.profit_state['guard'];quote=guard-d*5
        assert 'profit_guard_touched' in e.step(9030,quote,state,ack=False)
        assert row(e)['status']=='profit_guard_exit'
        assert 'profit_guard_touched' in e.step(9045,p['price']+d*400,state,ack=True)
        assert not e.tracker.signals
        results.append(dict(case='guard_worse_quote_book_exit_and_unacked_latch',direction=d,guard=guard,exit_quote=quote,status='profit_guard_exit'));e.close()
        e=Engine();e.add(p,9000);e.step(9180,p['price'],None)
        assert all(r['status']=='censored_gap' and r['net'] is None for r in e.book.db.execute('SELECT * FROM signals'))
        results.append(dict(case='quote_gap_censors_all_policies',direction=d));e.close()
        e=Engine();e.add(p,9000);s=copy.deepcopy(state)
        s['minute_bars'].append(dict(start=9000,open=p['price'],close=p['price'],high=max(p['target1'],p['stop'])+1,low=min(p['target1'],p['stop'])-1,volume=1.,quality='stream_closed'))
        reasons=e.step(9060,p['price'],s)
        assert 'stop_breached' in reasons and row(e)['status']=='censored_intrabar_stop' and row(e)['net'] is None
        results.append(dict(case='same_bar_target_stop_touch_no_optimistic_fill',direction=d,reasons=reasons,status=row(e)['status']));e.close()
    for d in (1,-1):
        state,p=fixture(d);p=profit_plan(p);e=Engine();sig=e.add(p,9000)
        assert 'target1_reached' in e.step(9015,p['target1'],state)
        old_stop=sig.stop;old_target=sig.target2
        for at in (9075,9135,9195,9255):e.step(at,p['target1']+d*50,None)
        append_bar(state,9000,p['price']+d*1000)
        state['five_bars'][-1]['volume']=2*sum(b['volume'] for b in state['five_bars'][-21:-1])/20
        reasons=e.step(9305,p['target2']+d*10,state)
        assert 'target_extended' in reasons and d*(sig.stop-old_stop)>=0 and d*(sig.target2-old_target)>0
        assert row(e)['status']=='open'
        results.append(dict(case='post_TP1_native_runner_extension_is_monotonic',direction=d,reasons=reasons,new_stop=sig.stop,new_target=sig.target2));e.close()
    state,p=fixture();p.update(price=78000.,stop=77700.,target1=80000.,target2=81000.)
    p['followup_thesis'].update(entry=78000.,initial_stop=77700.,anchor=None);p=profit_plan(p)
    e=Engine();e.add(p,9000)
    for at in range(9060,11701,60):e.step(at,78000.,None)
    bars=[]
    for i in range(30):
        c=82000-i*100;bars.append(dict(start=2700+i*300,open=c+20,high=c+30,low=c-30,close=c,volume=1.,quality='stream_closed'))
    state.update(five_bars=bars,last5_start=11400,closes15=[80000.,79700.,79400.])
    reasons=e.step(11715,79100.,state)
    assert 'early_invalidation' in reasons and row(e)['status']=='structure_exit' and row(e)['net']>0
    assert row(e,'warning_exit')['status']=='early_warning'
    results.append(dict(case='profitable_confirmed_reversal_book_first_then_warning_ack',reasons=reasons,runner_status=row(e)['status'],warning_status=row(e,'warning_exit')['status']));e.close()
    return results


def load_trial(path):
    spec=importlib.util.spec_from_file_location('fixed_entry_trial',path);mod=importlib.util.module_from_spec(spec);spec.loader.exec_module(mod);return mod


def reconstruct_entries(trial,data,prior):
    expected=next(v for v in prior['variants'] if v['variant']=='baseline')['closed_trades']
    captured=[];original=m.attach_thesis
    class BaselineDone(BaseException):pass
    def capture(plan,state,now):
        if captured and now<captured[-1]['at']:raise BaselineDone()
        result=original(plan,state,now);captured.append(dict(at=now,plan=profit_plan(result)));return result
    saved=sys.argv;m.attach_thesis=capture
    try:
        sys.argv=['reconstruct','--data',str(data),'--month','2025-01','--start','2025-01-08T00:00:00Z','--end','2025-01-15T00:00:00Z','--output','/unused-not-written.json']
        try:trial.main()
        except BaselineDone:pass
    finally:m.attach_thesis=original;sys.argv=saved
    assert len(captured)==len(expected)==22
    for n,(entry,old) in enumerate(zip(captured,expected),1):
        p=entry['plan'];assert entry['at']==old['entry_at'] and p['direction']==old['direction'] and p['strategy']==old['strategy']
        assert all(abs(p[new]-old[oldkey])<1e-8 for new,oldkey in [('price','entry'),('stop','stop'),('target1','target1')])
        entry['reference']=old['reference'];assert entry['reference']==n
    return captured


def native_at(bars,five,index,cache):
    now=bars[index]['start'];j=index//5
    if cache.get('j')!=j:
        fb=five[max(0,j-250):j];c=[b['close'] for b in fb];mp={b['start']:b for b in fb};final=now//900*900-900
        cache.update(j=j,base=dict(source=SOURCE,five_bars=fb,last5_start=fb[-1]['start'],
            closes15=[mp[t+600]['close'] for t in range(final-3600,final+1,900)],rsi5={'closes':c,'rsi':wilder_rsi_series(c)}))
    return dict(cache['base'],minute_bars=bars[index-360:index],prices=[b['close'] for b in bars[index-360:index]],quote=bars[index]['open'])


def run(args):
    logging.disable(logging.CRITICAL)
    gold=golden_cases()
    prior=json.loads(Path(args.prior).read_text());trial=load_trial(args.trial)
    entries=reconstruct_entries(trial,args.data,prior)
    bars,five,rates,objects=trial.load(args.data,'2025-01');bytime={b['start']:b for b in bars}
    start=int(datetime(2025,1,8,tzinfo=timezone.utc).timestamp());end=int(datetime(2025,1,16,tzinfo=timezone.utc).timestamp())
    entrymap={e['at']:e for e in entries};e=Engine();cache={};marks=[]
    for i,b in enumerate(bars):
        now=b['start']
        if not start<=now<end:continue
        state=native_at(bars,five,i,cache)
        e.step(now,b['open'],state)
        if now in entrymap:
            en=entrymap[now];e.add(en['plan'],now,en['reference'])
    rows=[dict(r) for r in e.book.db.execute('SELECT * FROM signals ORDER BY opened,id,policy')]
    legs=sorted(e.book.legs,key=lambda l:(l['at'],0 if l['kind']=='partial' else 1,l['id'],l['policy']))
    for r in rows:
        p=json.loads(r['plan']);r['plan']=p
        selected=[l for l in legs if l['id']==r['id'] and l['policy']==r['policy']]
        r['leg_fraction_total']=sum(l['fraction'] for l in selected)
        denom=-m.net_pnl_per_btc(p['direction'],p['price'],p['stop'])
        for k in (1,2):
            net=0.
            for leg in selected:
                d=p['direction'];entry=p['price']*(1+d*.0002*k);exit=leg['price']*(1-d*.0002*k)
                cash=d*(exit-entry)-.0006*k*(entry+exit)
                for ft,rate in rates:
                    if r['opened']<ft<=leg['at']:
                        charge=d*rate*bytime[ft]['open'];cash-=charge*k if charge>0 else charge
                net+=leg['fraction']*cash
            r['known_legs_R' if k==1 else 'known_legs_doublecost_R']=net/denom
            complete=r['status']!='open' and not r['status'].startswith('censored_')
            r['complete_net_R' if k==1 else 'complete_doublecost_R']=net/denom if complete else None
        if r['status']!='open':assert abs(r['leg_fraction_total']-1.)<1e-12
    summaries={}
    for policy in ('levels','warning_exit','trend_runner'):
        selected=[r for r in rows if r['policy']==policy];done=[r for r in selected if r['complete_net_R'] is not None]
        values=[r['complete_net_R'] for r in sorted(done,key=lambda r:r['closed'])]
        gain=sum(x for x in values if x>0);loss=-sum(x for x in values if x<0);equity=peak=dd=0.
        for x in values:equity+=x;peak=max(peak,equity);dd=max(dd,peak-equity)
        tp=sum(r['stage'] for r in selected);n=len(selected);ph=tp/n;z=1.96
        wilson=(ph+z*z/(2*n)-z*math.sqrt(ph*(1-ph)/n+z*z/(4*n*n)))/(1+z*z/n)
        summaries[policy]=dict(signals=n,complete=len(done),censored=sum(r['status'].startswith('censored_') for r in selected),open_end=sum(r['status']=='open' for r in selected),statuses=dict(Counter(r['status'] for r in selected)),TP1_states=tp,TP1_Wilson_lower=wilson,conditional_complete_sum_R=sum(values),conditional_complete_mean_R=sum(values)/len(values) if values else None,conditional_complete_PF=gain/loss if loss else None,conditional_complete_doublecost_R=sum(r['complete_doublecost_R'] for r in done),conditional_closed_R_DD=dd)
    out=dict(protocol='frozen_entries_existing_management_v1',source_commit='5c977a7f51cea67048f95791d1dcc4230634939a',entry_window=['2025-01-08T00:00:00Z','2025-01-15T00:00:00Z'],followup_end_exclusive='2025-01-16T00:00:00Z',entry_reconstruction_exact_matches=22,existing_source_functions_unmodified=True,
        golden_cases=gold,inputs=objects,prior_report_sha256=hashlib.sha256(Path(args.prior).read_bytes()).hexdigest(),entries=entries,rows=rows,legs=legs,events=e.events,summaries=summaries,
        native_LBank_performance=False,already_seen_development=True,orders=0,messages_sent=0,acknowledgment_assumption='all synthetic event notifications acknowledged; no real Telegram call',risk_policy_changed=False,full_live15s30s_replay=False,all_source_censored_rows_excluded_from_complete_returns=True,promotion=False)
    e.close();Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n');print(json.dumps(summaries))

if __name__=='__main__':
    ap=argparse.ArgumentParser();ap.add_argument('--data',required=True);ap.add_argument('--prior',required=True);ap.add_argument('--trial',required=True);ap.add_argument('--output',required=True);run(ap.parse_args())
