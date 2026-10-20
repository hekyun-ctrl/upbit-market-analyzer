"""Independent Decimal cash reconstruction from originals, no audit imports."""
import argparse,csv,hashlib,json,subprocess
from pathlib import Path
from decimal import Decimal as D
import pandas as pd

def main(a):
    repo,out=Path(a.repo),Path(a.output);r=json.loads((out/'report.json').read_text());checks=0;maxerr=D(0);notes=[]
    def check(v):
        nonlocal checks
        assert v
        checks+=1
    def close(x,y):
        nonlocal maxerr
        diff=abs(D(str(x))-D(str(y)));maxerr=max(maxerr,diff);check(diff<D('1e-7'))
    for obj in r['source_inputs']:
        b=subprocess.check_output(['git','-C',str(repo),'show',r['source_commit']+':'+obj['path']])
        check(hashlib.sha256(b).hexdigest()==obj['sha256']);check(hashlib.sha1(('blob '+str(len(b))+'\0').encode()+b).hexdigest()==obj['git_blob_sha'])
        if (repo/obj['path']).exists():check((repo/obj['path']).read_bytes()==b)
    for s in r['periods']:
        name=s['name'];events=pd.read_parquet(repo/'results'/name/'trades.parquet');pnl=pd.read_parquet(repo/'results'/name/'pnl.parquet');eq=pd.read_parquet(repo/'results'/name/'equity.parquet').iloc[:,0]
        campaigns=list(csv.DictReader((out/f'{name}_campaigns.csv').open()))
        check(len(campaigns)*2==len(events));cash=D(10000);cash2=D(10000);pos=D(0);net=[];net2=[];fundallocated=D(0)
        for j,row in enumerate(events.itertuples()):
            q,p,fee=D(str(row.qty)),D(str(row.price)),D(str(row.fee_usd));sign=D(1 if row.side=='buy' else -1)
            close(fee,q*p*D('.0006'));cash-=sign*q*p+fee;pos+=sign*q
            c=campaigns[j//2];forced=c['source_forced_end']=='True' and j%2==1
            raw=p if forced else p/(1+sign*D('.0001'));p2=raw if forced else raw*(1+sign*D('.0002'));fee2=q*p2*D('.0012')
            cash2-=sign*q*p2+fee2
            if j%2==0:entry=(q,p,fee,p2,fee2,sign,pd.Timestamp(row.timestamp))
            else:
                q0,p0,fee0,p20,fee20,direction,ts=entry;check(q==q0);check(sign==-direction);close(pos,0)
                f=pnl.loc[(pnl.index>=ts)&(pnl.index<pd.Timestamp(row.timestamp)),'funding_pnl']
                fund=sum((D(str(x)) for x in f),D(0));fd=sum((D(str(x))*(2 if x<0 else 1) for x in f),D(0));fundallocated+=fund
                n=direction*q*(p-p0)-fee-fee0+fund;n2=direction*q*(p2-p20)-fee2-fee20+fd
                close(c['net_USD'],n);close(c['double_cost_net_USD'],n2)
                close(c['source_extract_minus_cash_USD'],-D(c['reported_slippage_USD'])-fund)
                if c['known_outcome']=='True':net.append(n);net2.append(n2)
        allfund=sum((D(str(x)) for x in pnl.funding_pnl),D(0));doublefund=sum((D(str(x))*(2 if x<0 else 1) for x in pnl.funding_pnl),D(0))
        close(fundallocated,allfund);close(cash+allfund-10000,s['source_published_terminal_change_USD']);close(cash+allfund,eq.iloc[-1]);close(cash2+doublefund-10000,s['conditional_double_cost_terminal_change_USD'])
        for values,key in [(net,'known_base'),(net2,'known_double_cost')]:
            close(sum(values),s[key]['sum_USD']);close(sum(values)/len(values),s[key]['mean_USD']);close(sum(x for x in values if x>0)/-sum(x for x in values if x<0),s[key]['PF'])
        check(s['maximum_source_equity_replay_abs_error']<1e-7);check(s['maximum_fee_booking_abs_error']<1e-7);check(s['nextopen_exec_price_raw_open_max_abs_error']<1e-7)
        close(s['entries_per_calendar_day'],len(campaigns)/s['calendar_days']);check(s['entry_days']==len({c['entry_time'][:10] for c in campaigns}))
        check(s['forced_end_campaigns']==0 and s['raw_gap_campaigns']==0)
        notes.append(dict(period=name,orders=len(events),campaigns=len(campaigns),cash_base_USD=str(cash+allfund-10000),cash_double_USD=str(cash2+doublefund-10000)))
    check(not r['eligible'] and not r['forward_started'] and not r['production_changed']);check(not r['new_market_backtest_performed'])
    result=dict(checks_passed=checks,maximum_cash_absolute_error=str(maxerr),periods=notes,
        source_inputs_hash_verified=len(r['source_inputs']),independent_method='Decimal direct cashflows and campaign matching from originalParquets,not audit functions',
        bootstrap_not_independently_reimplemented=True,market_native_profitability_certified=False,
        verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out/'independent_verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--output',required=True);main(p.parse_args())
