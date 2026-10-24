"""No-send historical entry comparison; first-event diagnostic, not live PnL."""
import argparse, bisect, csv, hashlib, io, json, logging, math, zipfile
from collections import Counter
from datetime import datetime, timezone, timedelta
from pathlib import Path
import lbank_futures_monitor as m
from lbank_native_entry import SOURCE, wilder_rsi_series
from lbank_signal_risk import SignalRiskTracker


def read_zip(path):
    with zipfile.ZipFile(path) as z:
        return list(csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]))))


def load(data, month):
    root=Path(data); mp=root/'manifest-1m.json'
    manifest=json.loads(mp.read_text())['archives']
    objects=[]
    for kind in ('klines','fundingRate'):
        path=root/kind/('1m' if kind=='klines' else '5m')/('BTCUSDT-'+('1m' if kind=='klines' else kind)+'-'+month+'.zip')
        sha=hashlib.sha256(path.read_bytes()).hexdigest()
        matches=[a for a in manifest if a['month']==month and a['kind']==kind]
        if not matches:
            other=json.loads((root/'manifest-5m.json').read_text())['archives']
            matches=[a for a in other if a['month']==month and a['kind']==kind]
        assert len(matches)==1 and matches[0]['sha256']==sha
        objects.append(dict(kind=kind,sha256=sha,url=matches[0]['url']))
    raw=read_zip(root/('klines/1m/BTCUSDT-1m-'+month+'.zip'))
    bars=[dict(period=60,start=int(r['open_time'])//1000,open=float(r['open']),
        high=float(r['high']),low=float(r['low']),close=float(r['close']),volume=float(r['volume']),
        turnover=float(r['quote_volume']),updated=int(r['open_time'])//1000+59,quality='stream_closed') for r in raw]
    assert len(bars)==44640 and all(b['start']-a['start']==60 for a,b in zip(bars,bars[1:]))
    assert all(b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high'] for b in bars)
    five=[]
    for i in range(0,len(bars),5):
        g=bars[i:i+5];assert len(g)==5 and g[0]['start']%300==0
        five.append(dict(period=300,start=g[0]['start'],open=g[0]['open'],close=g[-1]['close'],
            high=max(b['high'] for b in g),low=min(b['low'] for b in g),
            volume=sum(b['volume'] for b in g),turnover=sum(b['turnover'] for b in g),
            updated=g[-1]['updated'],quality='stream_closed'))
    rates=[(int(r['calc_time'])//1000,float(r['last_funding_rate'])) for r in read_zip(root/('fundingRate/5m/BTCUSDT-fundingRate-'+month+'.zip'))]
    return bars,five,rates,objects


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--data',required=True);ap.add_argument('--output',required=True)
    ap.add_argument('--month',required=True);ap.add_argument('--start',required=True);ap.add_argument('--end',required=True)
    args=ap.parse_args();logging.disable(logging.CRITICAL)
    bars,five,rates,objects=load(args.data,args.month);by_start={b['start']:b for b in bars}
    start=int(datetime.fromisoformat(args.start.replace('Z','+00:00')).timestamp());end=int(datetime.fromisoformat(args.end.replace('Z','+00:00')).timestamp())
    assert start-bars[0]['start']>=7*86400 and end<=bars[-1]['start']+60 and end>start
    days=(end-start)/86400
    variants=[]
    for strict in (False,True):
        tracker=SignalRiskTracker();watch=None;state_alert={'day':'','count':0,'last_time':None,'last_side':None}
        trades={};closed=[];counts=Counter();last_j=None;cache=None;overlap=0;opportunity=set()
        def allowed(p):
            return p is not None and (not strict or p['strategy']!='trend_retest' or
                p.get('entry_decision_priority',{}).get('higher_direction_intact') is True)
        def finish(number,at,price,reason):
            trade=trades.pop(number);trade.update(exit_at=at,exit=price,reason=reason)
            d=trade['direction'];entry=trade['entry'];stop=trade['stop']
            denom=-m.net_pnl_per_btc(d,entry,stop)
            for multiple in (1,2):
                ei=entry*(1+d*.0002*multiple);xo=price*(1-d*.0002*multiple)
                fees=.0006*multiple*(ei+xo)
                funding=0.
                for ft,rate in rates:
                    if trade['entry_at']<ft<=at:
                        mark=by_start[ft]['open'];cost=d*rate*mark
                        funding+=cost*multiple if cost>0 else cost
                trade['net_R' if multiple==1 else 'double_cost_R']=(d*(xo-ei)-fees-funding)/denom
                if multiple==1:trade.update(gross_R=d*(price-entry)/denom,funding_per_btc=funding)
            closed.append(trade);tracker.signals=[s for s in tracker.signals if s.number!=number]
        for i,b in enumerate(bars):
            now=b['start'];quote=b['open']
            if not start<=now<end:continue
            j=i//5
            if j!=last_j:
                fb=five[max(0,j-250):j];last_j=j
                c=[x['close'] for x in fb];mp={x['start']:x for x in fb};final15=now//900*900-900
                c15=[mp[t+600]['close'] for t in range(final15-3600,final15+1,900)]
                cache={'five_bars':fb,'source':SOURCE,'last5_start':fb[-1]['start'],'closes15':c15,
                       'rsi5':{'closes':c,'rsi':wilder_rsi_series(c)}}
            native=dict(cache,minute_bars=bars[i-360:i],prices=[x['close'] for x in bars[i-360:i]],quote=quote)
            for sig,reason,price in tracker.check(native['prices']+[quote],now,native_state=native):
                if sig.number not in trades:continue
                if reason in ('stop_breached','early_invalidation'):
                    trades[sig.number]['urgent_at']=now;finish(sig.number,now,price,reason)
                elif reason=='risk_caution':
                    trades[sig.number].setdefault('caution_at',now);tracker.acknowledge(sig,reason=reason,now=now)
                elif reason=='target1_reached':finish(sig.number,now,price,'target1')
                elif reason=='tracking_expired':
                    cens=trades.pop(sig.number);cens['censored_reason']=reason;counts['tracking_censored']+=1
                    tracker.acknowledge(sig,reason=reason,now=now)
                elif reason=='time_review':tracker.acknowledge(sig,reason=reason,now=now)
            if watch is not None:
                if now-watch['time']>m.ENTRY_CONFIRM_MAX_AGE_SECONDS:watch=None
                else:
                    diag={};p=m.confirm_watch(native['prices'],watch['plan'],now-watch['time'],native_state=native,diagnostics=diag)
                    if p is not None and not allowed(p):counts['higher_confirmation_rejected']+=1;p=None
                    if p is not None:
                        if m.alert_gate(now,p['side'],state_alert,plan=p)=='ok':
                            p=m.attach_thesis(p,native,now)
                            # Fixed initial-stop/TP1 diagnostic, intentionally excludes pre-TP guard and partial runner.
                            sig=tracker.add_sent_signal(p,now);m.record_alert(now,p['side'],state_alert,plan=p)
                            trades[sig.number]=dict(reference=sig.number,entry_at=now,entry=p['price'],stop=p['stop'],
                                target1=p['target1'],direction=p['direction'],strategy=p['strategy'],mae5_fraction=0.)
                            counts['signals']+=1;opportunity.add(datetime.fromtimestamp(now,timezone(timedelta(hours=9))).date().isoformat())
                            overlap=max(overlap,len(trades))
                        watch=None
                    elif diag.get('reason')==m.BREAKOUT_CONFIRMATION_REJECT:watch=None
            if watch is None:
                diag={};p=m.classify(native['prices'],native_state=native,diagnostics=diag)
                if p is not None and not allowed(p):counts['higher_setup_rejected']+=1;p=None
                if p is not None and m.alert_gate(now,p['side'],state_alert,plan=p)=='ok':watch={'plan':p,'time':now}
            for number,trade in list(trades.items()):
                d=trade['direction'];adverse=b['low'] if d==1 else b['high']
                if now-trade['entry_at']<300:
                    trade['mae5_fraction']=max(trade['mae5_fraction'],-d*(adverse/trade['entry']-1))
                stop_hit=d*(adverse-trade['stop'])<=0
                favorable=b['high'] if d==1 else b['low'];tp_hit=d*(favorable-trade['target1'])>=0
                if stop_hit or tp_hit:
                    price=trade['stop'] if stop_hit else trade['target1']
                    if stop_hit and d*(quote-price)<0:price=quote
                    trade['same_minute_ambiguous']=stop_hit and tp_hit
                    finish(number,now+60,price,'initial_stop' if stop_hit else 'target1')
        nr=[t['net_R'] for t in closed];win=sum(t['reason']=='target1' for t in closed);n=len(closed)
        gain=sum(x for x in nr if x>0);loss=-sum(x for x in nr if x<0)
        equity=peak=dd=0.
        for x in nr:equity+=x;peak=max(peak,equity);dd=max(dd,peak-equity)
        p=win/n if n else 0;z=1.96;wl=((p+z*z/(2*n)-z*math.sqrt(p*(1-p)/n+z*z/(4*n*n)))/(1+z*z/n)) if n else None
        variants.append(dict(variant='higher_retest' if strict else 'baseline',counts=dict(counts),closed=n,
            end_censored=len(trades),net_R=sum(nr),mean_net_R=sum(nr)/n if n else None,
            PF=gain/loss if loss else None,double_cost_R=sum(t['double_cost_R'] for t in closed),
            target1_first=win,TP1_Wilson95_lower=wl,closed_realized_R_drawdown=dd,
            opportunity_days=sorted(opportunity),maximum_overlapping_references=overlap,
            urgent_within5m=sum('urgent_at' in t and t['urgent_at']-t['entry_at']<=300 for t in closed),
            mean_first5m_adverse_pct=sum(t['mae5_fraction'] for t in closed)/n*100 if n else None,
            signals_per_UTC_day=counts['signals']/days,closed_trades=closed,open_censored=list(trades.values())))
    out=dict(protocol='current_native_first_event_regime_extension_v1',start_utc=args.start,end_exclusive_utc=args.end,elapsed_days=days,inputs=objects,rows=len(bars),
        history_already_observed=True,native_LBank_performance=False,actual_alerts_sent=0,orders=0,
        initial_stop_TP1_diagnostic_excludes_preTP_guard_and_partial_runner=True,
        source_commit='5c977a7f51cea67048f95791d1dcc4230634939a',unchanged_gate_source='862339db2c4f69facc9ba6626e2826d475504174',variants=variants,
        deployment_permitted=False,forward_candidate_started=False)
    Path(args.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps([{k:v for k,v in r.items() if k not in ('closed_trades','open_censored')} for r in variants]))


if __name__=='__main__':main()
