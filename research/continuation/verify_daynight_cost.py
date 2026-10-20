"""Independent standard-library Decimal verification of saved table arithmetic.

No import of the audit implementation, numpy, pandas or third-party source.
"""
import argparse,csv,json,hashlib,subprocess
from decimal import Decimal as D,localcontext
from pathlib import Path

def main(a):
    repo,out=Path(a.repo),Path(a.output)
    report=json.loads((out/'report.json').read_text());checks=0;maxerr=D(0)
    def check(ok):
        nonlocal checks
        assert ok
        checks+=1
    def close(x,y):
        nonlocal maxerr
        err=abs(D(str(x))-D(str(y)))/max(D(1),abs(D(str(y))))
        maxerr=max(maxerr,err);check(err<D('1e-10'))
    read=lambda p:list(csv.DictReader(p.open()))
    normal=lambda x:x.lower().replace('momentum','trend')
    original=read(repo/'paper_outputs/tables/cutoff_strategy_full_results.csv')
    btc=[r for r in original if r['Asset']=='BTC']
    idx={(int(r['Cutoff_Hour']),normal(r['Night_Strategy']),normal(r['Day_Strategy']),int(r['Transaction_Cost'])):r for r in btc}
    matrix=read(out/'all300_BTC_conditional_costs.csv')
    check(len(idx)==900);check(len(matrix)==300)
    with localcontext() as ctx:
        ctx.prec=50
        for row in matrix:
            key=(int(row['cutoff']),row['night'],row['day']);base=idx[key+(0,)]
            n=int(base['Number_of_Trades']);t=int(D(base['Total_Turnover']))
            n1,n2=2*n-t,t-n
            check(n1==int(row['n1']) and n2==int(row['n2']) and n1>=0 and n2>=0)
            for b in [0,1,2,8,16]:
                cost=D(b)/10000
                expected=D(base['Terminal_Wealth'])*(1-cost)**n1*(1-2*cost)**n2
                close(row[f'conditional_wealth_{b}bps'],expected)
                if b in [0,1,2]:close(idx[key+(b,)]['Terminal_Wealth'],expected)
        core=[r for r in read(repo/'paper_outputs/tables/btc_eth_all_metrics.csv') if r['coin']=='BTC']
        check(len(core)==525)
        for r in core:
            key=(int(r['cutoff']),normal(r['night_strategy']),normal(r['day_strategy']),int(r['transaction_cost_bps']))
            close(r['final_wealth'],idx[key]['Terminal_Wealth'])
        for r in report['fixtures']['cash_cases']:
            close(r['original'],r['Decimal']);close(r['repriced'],r['Decimal'])
        check(report['fixtures']['past_position_changes']==0)
        for item in report['source_tree']:
            p=repo/item['path']
            check(hashlib.sha256(p.read_bytes()).hexdigest()==item['sha256'])
            blob=subprocess.check_output(['git','-C',str(repo),'hash-object',item['path']],text=True).strip()
            check(blob==item['git_blob_sha'])
        check(not report['original_price_replayed'] and not report['eligible'] and not report['forward_started'])
        positives={}
        for b in [0,1,2,8,16]:
            active=[r for r in matrix if (r['night'],r['day'])!=('cash','cash')]
            pos=[r for r in active if D(r[f'conditional_wealth_{b}bps'])>D(100)+D('1e-8')]
            check(len(pos)==report['unique_active_BTC_cells_wealth_above100_by_bps'][str(b)]['positive'])
            positives[str(b)]=[dict(cutoff=int(r['cutoff']),night=r['night'],day=r['day'],change_events=int(r['source_change_events'])) for r in pos]
    result=dict(checks_passed=checks,maximum_relative_error=str(maxerr),BTC_published_cost_rows=900,
        independent_Decimal_repriced_cells=1500,core_overlap_rows=525,synthetic_cash_cases=12,
        positive_cell_identities=positives,market_price_replayed=False,source_tables_are_conditional_inputs=True,
        verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out/'independent_verification.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps({k:v for k,v in result.items() if k!='positive_cell_identities'},indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--output',required=True);main(p.parse_args())
