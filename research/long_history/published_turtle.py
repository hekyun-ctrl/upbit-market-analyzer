"""Published System-2 price mechanics; explicit BTC/risk/execution adaptations.
Research only. No production imports, signals, orders, or parameter search.
"""
import json
from pathlib import Path
import numpy as np
import pandas as pd
from research.long_history.evaluate import load_archives, verify_manifests, digest

TICK=.1

def daily_context(one):
    g=one.groupby(one.start//86400*86400)
    d=g.agg(first=('start','first'),last=('start','last'),count=('start','count'),high=('high','max'),low=('low','min'),close=('close','last'))
    d=d.reindex(np.arange(d.index.min(),d.index.max()+86400,86400))
    complete=(d['count']==1440)&(d['first']==d.index)&(d['last']==d.index+86340)
    d.loc[~complete,['high','low','close']]=np.nan
    tr=pd.concat([d.high-d.low,(d.high-d.close.shift()).abs(),(d.low-d.close.shift()).abs()],axis=1).max(axis=1)
    n=np.full(len(d),np.nan);v=None;history=[]
    for i,x in enumerate(tr):
        if not complete.iloc[i] or not np.isfinite(x):v=None;history=[];continue
        history.append(x)
        if v is None and len(history)>=20:v=float(np.mean(history[-20:]))
        elif v is not None:v=(19*v+x)/20
        if v is not None:n[i]=v
    ctx=pd.DataFrame({'day':d.index+86400,'N':n,
        'upper':d.high.rolling(55,min_periods=55).max()+TICK,
        'lower':d.low.rolling(55,min_periods=55).min()-TICK,
        'exitlong':d.low.rolling(20,min_periods=20).min()-TICK,
        'exitshort':d.high.rolling(20,min_periods=20).max()+TICK})
    return ctx.set_index('day')


def replay(one,funding,ctx,path='LH',stress=1.):
    fee=.0006*stress;slip=.0002*stress
    # Equity is normalized to one. Each unit has 0.25% of INITIAL equity/N.
    cash=1.;units=[];campaigns=[];fills=[];daily=[];d=0;N=0.;qty=0.;last_fill=0.;campaign=None
    day=-1;context=None;prev=None;blocked_day=-1;fund_i=0
    ft=(funding.calc_time.to_numpy()/1000).astype('int64');fr=funding.last_funding_rate.to_numpy()
    a=one[['start','open','high','low','close']].to_numpy()
    def equity(p):return cash+sum(u['qty']*d*(p-u['entry']) for u in units)
    def close_unit(u,p,t,reason):
        nonlocal cash
        fill=p*(1-d*slip);net=u['qty']*(d*(fill-u['entry'])-fee*fill)
        cash+=net;campaign['realized']+=net
        fills.append(dict(time=t,event='exit',side=d,price=fill,qty=u['qty'],campaign=campaign['id'],reason=reason))
    def finish(t):
        nonlocal d,campaign
        campaign['exit_at']=t;campaign['net']=campaign['realized'];campaign['holding_days']=(t-campaign['entry_at'])/86400
        campaigns.append(campaign);campaign=None;d=0
    def add(p,t):
        nonlocal cash,last_fill
        fill=p*(1+d*slip)
        for u in units:u['stop']+=d*.5*N
        u=dict(entry=fill,qty=qty,stop=fill-d*2*N);units.append(u);last_fill=fill
        cost=qty*fee*fill;cash-=cost;campaign['realized']-=cost;campaign['units']+=1
        fills.append(dict(time=t,event='entry' if campaign['units']==1 else 'add',side=d,price=fill,qty=qty,campaign=campaign['id'],reason='55day' if campaign['units']==1 else '0.5N'))
    for index,(raw_t,o,h,l,c) in enumerate(a):
        t=int(raw_t);this_day=t//86400*86400;closed_this_minute=False
        if this_day!=day:
            if day>=0:daily.append(dict(time=prev+60,equity=equity(last_close),units=len(units)))
            day=this_day;context=ctx.loc[day].to_dict() if day in ctx.index else None
        gap=prev is not None and t!=prev+60
        if gap:blocked_day=this_day
        if gap and units:
            for u in units:close_unit(u,o,t,'data_gap_next_observed_open')
            units=[];campaign['data_gap']=True;finish(t);closed_this_minute=True
        # Funding at minute open BEFORE any changes, adverse cashflows only.
        while fund_i<len(ft) and ft[fund_i]<=t:
            if units and ft[fund_i]==t:
                debit=sum(u['qty'] for u in units)*max(d*fr[fund_i],0)*o
                cash-=debit;campaign['realized']-=debit;campaign['funding']+=debit
            fund_i+=1
        ready=this_day!=blocked_day and context is not None and all(np.isfinite(context[k]) for k in ('N','upper','lower','exitlong','exitshort'))
        if ready:
            # Opening gaps: individual stops/channel first, then favorable add.
            if units:
                ex=context['exitlong'] if d==1 else context['exitshort']
                survivors=[]
                for u in units:
                    if d*(o-u['stop'])<=0 or d*(o-ex)<=0:close_unit(u,o,t,'opening_exit')
                    else:survivors.append(u)
                units=survivors
                if not units:finish(t);closed_this_minute=True
            if not units and not closed_this_minute:
                side=1 if o>=context['upper'] else -1 if o<=context['lower'] else 0
                if side:
                    d=side;N=context['N'];qty=.0025/N
                    campaign=dict(id=len(campaigns),entry_at=t,side=d,N=N,realized=0.,funding=0.,units=0,data_gap=False)
                    add(o,t)
            if units and len(units)<4 and d*(o-last_fill)>=.5*N:add(o,t)
            points=(o,l,h,c) if path=='LH' else (o,h,l,c)
            for begin,end in zip(points[:-1],points[1:]):
                current=begin
                for _ in range(12):
                    if end==current:break
                    upward=end>current;events=[]
                    if units:
                        ex=context['exitlong'] if d==1 else context['exitshort']
                        if upward==(d==-1):
                            for j,u in enumerate(units):
                                level=max(u['stop'],ex) if d==1 else min(u['stop'],ex)
                                if min(current,end)<=level<=max(current,end):events.append((level,'exit',j))
                        elif len(units)<4:
                            level=last_fill+d*.5*N
                            if min(current,end)<=level<=max(current,end):events.append((level,'add',None))
                    elif not closed_this_minute:
                        level=context['upper'] if upward else context['lower']
                        if min(current,end)<=level<=max(current,end):events.append((level,'enter',1 if upward else -1))
                    if not events:break
                    level,kind,j=min(events,key=lambda z:abs(z[0]-current));current=level
                    if kind=='exit':
                        close_unit(units.pop(j),level,t,'stop_or_20day_exit')
                        if not units:finish(t);closed_this_minute=True
                    elif kind=='enter':
                        d=j;N=context['N'];qty=.0025/N
                        campaign=dict(id=len(campaigns),entry_at=t,side=d,N=N,realized=0.,funding=0.,units=0,data_gap=False)
                        add(level,t)
                    else:add(level,t)
        # Missing daily context blocks new entries; existing positions retain hard stops.
        elif units:
            survivors=[]
            for u in units:
                if d*(o-u['stop'])<=0:close_unit(u,o,t,'context_gap_open_stop')
                elif (l<=u['stop'] if d==1 else h>=u['stop']):close_unit(u,u['stop'],t,'context_gap_hard_stop')
                else:survivors.append(u)
            units=survivors
            if not units:finish(t);closed_this_minute=True
        prev=t;last_close=c
    daily.append(dict(time=int(a[-1,0]+60),equity=equity(a[-1,4]),units=len(units)))
    open_campaign=dict(campaign) if campaign else None
    if open_campaign:open_campaign['marked_net']=campaign['realized']+sum(u['qty']*d*(a[-1,4]-u['entry']) for u in units)
    return pd.DataFrame(daily),pd.DataFrame(campaigns),pd.DataFrame(fills),open_campaign


def metrics(eq,trades):
    records=[];prior=1.
    for year,g in eq.groupby(pd.to_datetime(eq.time-1,unit='s',utc=True).dt.year):
        values=np.r_[prior,g.equity.to_numpy()];dd=(values/np.maximum.accumulate(values)-1).min()
        end=g.equity.iloc[-1]
        s=trades[pd.to_datetime(trades.exit_at,unit='s',utc=True).dt.year==year] if len(trades) else trades
        records.append(dict(year=int(year),return_pct=float((end/prior-1)*100),daily_marked_drawdown_pct=float(-dd*100),
            closed_campaigns=len(s),win_fraction=float((s.net>0).mean()) if len(s) else None,
            mean_holding_days=float(s.holding_days.mean()) if len(s) else None,
            gap_campaigns=int(s.data_gap.sum()) if len(s) else 0))
        prior=end
    x=eq.equity.to_numpy();x=np.r_[1.,x];dd=float(-(x/np.maximum.accumulate(x)-1).min()*100)
    return dict(years=records,total_return_pct=float((x[-1]-1)*100),daily_marked_drawdown_pct=dd,
        campaigns=len(trades),closed_net=float(trades.net.sum()) if len(trades) else 0,
        largest_closed_win=float(trades.net.max()) if len(trades) else None,
        win_fraction=float((trades.net>0).mean()) if len(trades) else None)


def main(single_2022=False):
    root=Path(__file__).resolve().parents[3];out=root/('published-turtle-2022' if single_2022 else 'published-turtle-evaluation');out.mkdir(exist_ok=True)
    folders=[root/'binance-futures-blind-2023',root/'binance-futures-verified'];parts=[];fund=[];sources=[]
    audits=[Path(__file__).with_name('integrity_final_2023.json'),Path(__file__).with_name('integrity_final_2024_2026.json')]
    if single_2022:
        folders=[root/'binance-futures-blind-2022'];audits=[folders[0]/'integrity_audit.json']
    for folder,audit in zip(folders,audits):
        sources+=verify_manifests(folder);m=load_archives(folder,'1m');bad=json.loads(audit.read_text())['quarantined_starts']
        parts.append(m[~(m.start//300*300).isin(bad)]);fund.append(load_archives(folder,'5m','fundingRate'))
    one=pd.concat(parts).sort_values('start').reset_index(drop=True);fund=pd.concat(fund).sort_values('calc_time');ctx=daily_context(one)
    result=dict(protocol_sha256=digest(Path(__file__).with_name('published_turtle_protocol_20261020.json')),code_sha256=digest(__file__),minutes=len(one),scenarios={},sources=sources,production_changed=False,profitable_strategy_certified=False)
    for path in ('LH','HL'):
        for stress in (1.,2.):
            eq,trades,fills,opened=replay(one,fund,ctx,path,stress);name=f'{path}_cost{int(stress)}'
            for suffix,df in [('daily_equity',eq),('campaigns',trades),('fills',fills)]:df.to_csv(out/f'{name}_{suffix}.csv.gz',index=False,compression={'method':'gzip','mtime':0})
            result['scenarios'][name]=dict(metrics(eq,trades),open_campaign=opened)
            print(name,json.dumps(result['scenarios'][name]),flush=True)
    result['ledgers']={p.name:digest(p) for p in sorted(out.glob('*.csv.gz'))}
    (out/'report.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')

if __name__=='__main__':
    import argparse
    p=argparse.ArgumentParser();p.add_argument('--single-2022',action='store_true');main(p.parse_args().single_2022)
