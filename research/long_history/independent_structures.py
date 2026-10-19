"""Two frozen alternative entry generators, independent of the old candidates."""
import argparse,json
from pathlib import Path
import numpy as np
import pandas as pd
from research.long_history.evaluate import (verify_manifests,load_archives,
    aggregate_minutes,feature_frame,wilder_atr,unit_net,score,digest)
from research.long_history.exit_intervention import follow

NAMES=('15m_channel_expansion','sell_buy_shock_reclaim')


def events_for_segment(one):
    one=one.reset_index(drop=True);five=aggregate_minutes(one);f=feature_frame(five)
    five_context=f[['start','atr','vwap']].copy();five_context['available']=five_context.start+300
    quarter=(one.start//900*900).astype('int64')
    q=one.groupby(quarter).agg(start=('start','first'),last=('start','last'),count=('start','count'),
        open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last'))
    q=q[(q['count']==15)&(q.start==q.index)&(q['last']==q.index+840)].reset_index(drop=True)
    q['atr15']=wilder_atr(q);q['channel_high']=q.high.rolling(20).max();q['channel_low']=q.low.rolling(20).min();q['available']=q.start+900
    left=pd.merge_asof(one,five_context.drop(columns='start'),left_on='start',right_on='available',direction='backward')
    left=pd.merge_asof(left,q[['available','atr15','channel_high','channel_low']].rename(columns={'available':'available15'}),left_on='start',right_on='available15',direction='backward')
    # The contexts are available BEFORE the signal minute starts; its close
    # and aggressive volume become known only when that minute ends.
    left['prior20low']=one.low.shift().rolling(20).min();left['prior20high']=one.high.shift().rolling(20).max()
    left['prior5move']=one.close.shift()-one.close.shift(6)
    left['flow']=2*one.buy_volume/one.volume.replace(0,np.nan)-1
    left['relative_minute_volume']=one.volume/one.volume.shift().rolling(60).median().replace(0,np.nan)
    left['low3']=one.low.rolling(3).min();left['high3']=one.high.rolling(3).max();left['previous_close']=one.close.shift()
    output=[]
    for r in left.to_dict('records'):
        a=r['atr'];a15=r['atr15'];c=r['close']
        if not np.isfinite(a) or a<=0:continue
        for d in (1,-1):
            boundary=r['channel_high']+.1*a if d==1 else r['channel_low']-.1*a
            breakout=(np.isfinite(a15) and a15>0 and d*(c-boundary)>0 and d*(r['previous_close']-boundary)<=0)
            key=r['prior20low'] if d==1 else r['prior20high'];extreme=r['low'] if d==1 else r['high']
            shock=(d*r['prior5move'] < -2*a and d*(extreme-key)<0 and d*(c-key)>0
                and d*(c-r['open'])>0 and d*r['flow']>=.2 and r['relative_minute_volume']>=2)
            for family,passed in ((0,breakout),(1,shock)):
                if passed:output.append(dict(family=family,side=d,signal_at=int(r['start']+60),entry_at=int(r['start']+120),
                    atr=a,atr15=a15,channel_width=r['channel_high']-r['channel_low'],
                    vwap=r['vwap'],low3=r['low3'],high3=r['high3']))
    return output


def run(folder,integrity,output):
    folder=Path(folder);out=Path(output);out.mkdir(parents=True,exist_ok=True)
    sources=verify_manifests(folder);one=load_archives(folder,'1m');original_count=len(one)
    if ((one.buy_volume<0)|(one.buy_volume>one.volume+1e-6)).any():raise ValueError('invalid aggressive volume')
    bad=json.loads(Path(integrity).read_text())['quarantined_starts'];one=one[~(one.start//300*300).isin(bad)].reset_index(drop=True)
    fund=load_archives(folder,'5m','fundingRate').sort_values('calc_time');ft=(fund.calc_time.to_numpy()/1000).astype('int64');fr=fund.last_funding_rate.to_numpy()
    events=[]
    for _,s in one.groupby((one.start.diff()!=60).cumsum()):
        if len(s)>=900:events.extend(events_for_segment(s))
    events.sort(key=lambda x:(x['entry_at'],x['family'],x['side']))
    m=tuple(one[k].to_numpy() for k in ('start','open','high','low','close'));starts=m[0];op=m[1]
    blocked={0:0,1:0};counts={0:{},1:{}};rows=[];rejected={}
    def reject(k):rejected[k]=rejected.get(k,0)+1
    for e in events:
        t=e['entry_at'];family=e['family'];d=e['side'];day=t//86400
        if t<blocked[family] or counts[family].get(day,0)>=8:continue
        i=int(np.searchsorted(starts,t))
        if i>=len(starts) or starts[i]!=t:reject('entry_gap');continue
        entry=op[i]
        if family==0:stop=entry-d*2*e['atr15'];target=entry+d*e['channel_width']
        else:
            stop=min(entry-2*e['atr'],e['low3']-.25*e['atr']) if d==1 else max(entry+2*e['atr'],e['high3']+.25*e['atr'])
            target=e['vwap']
        if d*(entry-stop)<=0 or abs(entry-stop)/entry>.006:reject('stop_cap');continue
        if not np.isfinite(target) or d*(target-entry)<=0 or unit_net(d,entry,target)<=0:reject('no_positive_target_room');continue
        p=dict(e,entry=entry,stop=stop,target=target,unit_net_risk=-unit_net(d,entry,stop))
        r=follow(p,m,ft,fr,1,max_minutes=360);rows.append(dict(p,**r))
        blocked[family]=max(r['exit_at'],t+2700);counts[family][day]=counts[family].get(day,0)+1
    ledger=pd.DataFrame(rows);ledger.to_csv(out/'independent_structure_trades.csv.gz',index=False,compression={'method':'gzip','mtime':0})
    summaries={};start_all=int(one.start.iloc[0]);end_all=int(one.start.iloc[-1]+60)
    if len(ledger):
        for year in sorted(pd.to_datetime(ledger.entry_at,unit='s',utc=True).dt.year.unique()):
            start=max(start_all,int(pd.Timestamp(f'{year}-01-01',tz='UTC').timestamp()));end=min(end_all,int(pd.Timestamp(f'{year+1}-01-01',tz='UTC').timestamp()))
            for family,name in enumerate(NAMES):
                s=ledger[(ledger.family==family)&(ledger.entry_at>=start)&(ledger.entry_at<end)].copy();s.loc[s.exit_at>end,['net_r','stress_r']]=np.nan
                summaries[f'{year}:{name}']=score(s,start,end)
    result=dict(protocol_sha256=digest(Path(__file__).with_name('independent_structure_protocol_20261020.json')),source_manifest=sources,
        original_minutes=original_count,usable_minutes=len(one),raw_events=len(events),rejected=rejected,summaries=summaries,
        ledger_sha256=digest(out/'independent_structure_trades.csv.gz'),production_changed=False,
        native_lbank_fills_or_warnings_verified=False,scope='Static historical check. No temporal forward or production certification.')
    (out/'independent_structure_report.json').write_text(json.dumps(result,indent=2,allow_nan=False)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='source_manifest'},indent=2,allow_nan=False),flush=True)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',required=True);p.add_argument('--integrity',required=True);p.add_argument('--output',required=True);a=p.parse_args();run(a.folder,a.integrity,a.output)
