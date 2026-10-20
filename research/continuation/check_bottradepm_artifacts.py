"""Separate source-object, Decimal cash and raw-price feature verification.
Does not execute any original source definitions.
"""
import argparse,hashlib,io,json,zipfile
from decimal import Decimal as D
from pathlib import Path
import numpy as np
import pandas as pd

def verify(repo,folder,results):
    repo,folder,out=Path(repo),Path(folder),Path(results)
    r=json.loads((out/'report.json').read_text());checks=[]
    def check(name,ok):
        checks.append(dict(name=name,passed=bool(ok)))
        if not ok:raise AssertionError(name)
    for f in r['source_tree']:
        b=(repo/f['path']).read_bytes()
        actual=hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
        check('source_object:'+f['path'],actual==f['git_blob_sha'] and hashlib.sha256(b).hexdigest()==f['sha256'])
    portions=[]
    for s in r['real_data']['sources']:
        path=folder/'klines'/'1m'/('BTCUSDT-1m-'+s['month']+'.zip')
        check('ZIP_SHA:'+s['month'],hashlib.sha256(path.read_bytes()).hexdigest()==s['zip_sha256'])
        with zipfile.ZipFile(path) as z:b=z.read(z.namelist()[0])
        check('CSV_SHA:'+s['month'],hashlib.sha256(b).hexdigest()==s['csv_sha256'])
        f=pd.read_csv(io.BytesIO(b));check('row_count:'+s['month'],len(f)==s['rows'])
        f.index=pd.to_datetime(f.open_time,unit='ms',utc=True);portions.append(f)
    m=pd.concat(portions)
    check('all_raw_minutes_unique_contiguous',len(m)==131040 and (m.open_time.diff().iloc[1:]==60000).all())
    bad=json.loads(Path('research/long_history/integrity_final_2024_2026.json').read_text())['quarantined_starts']
    check('no_archived_quarantine_in_Q1',not any(int(m.open_time.iloc[0]/1000)<=x<=int(m.open_time.iloc[-1]/1000) for x in bad))
    close=m.close.resample('4h').last()
    vol=close.pct_change().rolling(20).std();full=vol.quantile(.6)
    cut=pd.Timestamp('2024-03-11',tz='UTC');pre=vol[vol.index<cut];threshold=pre.quantile(.6)
    expected=(pre>=threshold).fillna(False)!=(pre>=full).fillna(False)
    observed=pd.read_csv(out/'real_fullsample_quantile_prefix.csv')
    check('all420_quantile_flags_independently_equal',np.array_equal(expected.to_numpy(),observed.changed.to_numpy()))
    check('quantile_change_count26',int(expected.sum())==26==r['real_data']['fullsample_quantile']['past_flags_changed'])
    # Independent direct daily close reduction and scalarEMA recursion.
    q=pd.read_csv(out/'real_intraday_prefix.csv');full_day=m.close.resample('1d').last()
    def ema50(series):
        value=float(series.iloc[0]);alpha=2/51
        for x in series.iloc[1:]:value=(1-alpha)*value+alpha*float(x)
        return value
    for i,row in q.iterrows():
        cutoff=pd.Timestamp(row.decision_after_close);day=cutoff.normalize()
        actual_prefix=m.close[m.index<cutoff].resample('1d').last()
        expected_prefix=ema50(actual_prefix)
        expected_full=ema50(full_day[full_day.index<=day])
        check(f'independent_daily_prefix_EMA:{i}',abs(expected_prefix-row.source_prefix_d_ema200)<1e-7)
        check(f'independent_full_day_EMA:{i}',abs(expected_full-row.full_day_d_ema200)<1e-7)
    check('all31queries_fixed_and_no_bearflagchange',len(q)==31 and (q.prefix_bear==q.full_day_bear).all())
    check('lagged_bull_control31unchanged',(q.prefix_lagged_bull==q.full_day_lagged_bull).all())
    f=r['synthetic_contract']['partial_event_statistics'];events=f['events']
    entry=D('100')*D('1.0002');tp=(entry+D('1.8')*D('1.2'))*D('.9998');stop=(entry-D('1.8'))*D('.9998')
    gross=(tp-entry)*D('.4')+(stop-entry)*D('.6')
    correct=gross-D('.0004')*(entry+tp*D('.4')+stop*D('.6'))
    source=correct-entry*D('.0004')
    check('independent_unitcash_correct',abs(float(correct)-f['actual_notional_cash_net'])<1e-10)
    check('independent_source_extra_entryfee',abs(float(source)-f['source_cash_net'])<1e-9)
    check('independent_entry_fee',str(entry*D('.0004'))=='0.04000800')
    check('source_synthetic_ledger_reconciles',abs(sum(e['realized_pnl'] for e in events)-f['source_cash_net'])<1e-9)
    check('source_returns_loss_but_eventwin50',correct<0 and f['source_report']['win_rate_pct']==50.)
    check('real_markets_profitability_not_claimed',not r['strategy_profitability_backtest_performed'] and r['profit_factor'] is None and r['fullcost_double_profit'] is None)
    check('no_promotion_or_production',not r['replacement_certified'] and not r['forward_started'] and not r['production_changed'])
    for name,sha in r['real_data']['fixed_query_files_sha256'].items():
        check('query_artifact:'+name,hashlib.sha256((out/name).read_bytes()).hexdigest()==sha)
    result=dict(passed=True,checks_passed=len(checks),checks=checks,raw_minutes=131040,
        Decimal_correct_unitcash=str(correct),Decimal_source_unitcash=str(source),
        original_source_executed=False,market_profitability_validated=False)
    (out/'independent_audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='checks'}))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--folder',required=True);p.add_argument('--results',required=True)
    a=p.parse_args();verify(a.repo,a.folder,a.results)
