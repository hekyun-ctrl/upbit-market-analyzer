"""Separate arithmetic/artifact verification, without executing source code."""
import argparse
import csv
import hashlib
import json
import subprocess
from datetime import datetime
from decimal import Decimal as D
from pathlib import Path


def verify(repo, results):
    repo, results = Path(repo), Path(results)
    report=json.loads((results/'report.json').read_text())
    checks=[]
    def check(name, passed):
        checks.append({'name':name,'passed':bool(passed)})
        if not passed: raise AssertionError(name)
    for f in report['source_tree']:
        b=(repo/f['path']).read_bytes()
        gitblob=hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()
        pinned=subprocess.check_output(['git','-C',str(repo),'rev-parse',report['source_commit']+':'+f['path']],text=True).strip()
        check('source_blob:'+f['path'],gitblob==pinned==f['git_blob_sha'] and hashlib.sha256(b).hexdigest()==f['sha256'])
    for s in report['btc_source_summary_arithmetic']:
        family=s['family']; copied=results/f'source_strategy_{family}_metrics.csv'
        check(f'original_summary_bytes:{family}',copied.read_bytes()==(repo/s['path']).read_bytes())
        with copied.open() as f: row=next(r for r in csv.DictReader(f) if r['symbol']=='BTCUSDT')
        n=int(row['n_trades']); w=int((D(row['win_rate'])*n).to_integral_value())
        gp=w*D(row['avg_win_pct']);gl=-(n-w)*D(row['avg_loss_pct'])
        # Published means have6 decimal places or fewer, total4, PF4.
        check(f'summary_rounding_net:{family}',abs(gp-gl-D(row['total_return_pct']))<=D('.00005')+n*D('.0000005'))
        pf_error=abs(gp/gl-D(row['profit_factor']))
        check(f'summary_rounding_pf:{family}',pf_error<D('.001'))
        check(f'BTC_negative_PFbelow1:{family}',D(row['total_return_pct'])<0 and D(row['profit_factor'])<1)
        check(f'summary_not_ledger:{family}',s['actual_trade_ledger_reconciled'] is False and s['actual_frequency_per_day'] is None)
    r=report['fixtures']
    fee=(D('110')-D('100')-D('.0004')*(D('100')+D('110')))/D('100')
    check('separate_actual_notionals_cash',str(fee)==r['fee_notionals']['actual_notionals_net'])
    entry=D('100')*D('1.0001');target=entry*D('1.02');exit_=target*D('.9999')
    correct=(exit_-entry-D('.0004')*(entry+exit_))/entry
    check('separate_target_cash',str(correct)==r['target_exit_slippage']['actual_notionals_fee_and_slip_net'])
    check('separate_stop_price',abs(float(entry*D('.98'))-r['stop_gap']['stop_fill'])<1e-10)
    check('separate_tail_pnl',abs(float((D('120')*D('.9999')-entry)/entry-D('.0008'))-r['end_truncation']['pnl_fraction'])<1e-12)
    f=r['funding_rate_sign_changed_pnl'];parse=lambda x:datetime.fromisoformat(x.replace('Z','+00:00'))
    check('settlement_inside_probe_holding',parse(f['entry'])<parse(f['synthetic_settlement'])<parse(f['exit']))
    check('inventory_only3summaryCSV',len(report['public_csv_paths'])==3 and all(p.endswith('metrics_per_symbol.csv') for p in report['public_csv_paths']))
    check('no_raw_or_tradeledger',not report['public_raw_parquet_paths'] and not report['public_trade_ledger_paths'])
    check('no_invented_market_metrics',report['net_expectancy_95_lower'] is None and report['fullcost_double_profit'] is None and not report['market_profitability_backtest_performed'])
    check('no_promotion_or_production',not report['replacement_certified'] and not report['production_changed'])
    result={'passed':True,'checks_passed':len(checks),'checks':checks,'market_result_validated':False}
    (results/'independent_audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({'passed':True,'checks_passed':len(checks)}))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--results',required=True)
    a=p.parse_args();verify(a.repo,a.results)
