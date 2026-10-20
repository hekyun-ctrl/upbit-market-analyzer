"""Independent stdlib/Decimal, Git and archive checks of retained evidence."""
import argparse,csv,hashlib,io,json,subprocess,zipfile
from decimal import Decimal as D
from pathlib import Path

def main(a):
    out=Path(a.output);report=json.loads((out/'report.json').read_text());cases=json.loads((out/'synthetic_broker_cases.json').read_text())
    checks=0;maxerr=D(0);campaign_differences=[]
    def check(v):
        nonlocal checks
        assert v
        checks+=1
    def close(x,y):
        nonlocal maxerr
        e=abs(D(str(x))-D(str(y)));maxerr=max(maxerr,e);check(e<D('1e-8'))
    for c in cases:
        cash=D(str(c['initial_cash']));pos=D(0);fills=[x for x in c['orders'] if x['status']=='Completed']
        for f in fills:
            qty,price,fee=D(str(f['size'])),D(str(f['price'])),D(str(f['commission']))
            close(fee,abs(qty)*price*D(str(c['commission_rate'])))
            cash-=qty*price+fee;pos+=qty
        close(c['final_cash'],cash);close(c['final_position'],pos);close(c['final_value'],cash+pos*D(str(c['bars'][-1][3])))
        if c['closed']:
            entry,exit=fills[:2];direction=D(1) if entry['size']>0 else D(-1)
            gross=direction*(D(str(exit['price']))-D(str(entry['price'])))
            net=gross-D(str(entry['commission']))-D(str(exit['commission']))
            close(c['closed'][0]['gross'],gross);close(c['closed'][0]['net'],net)
            check(c['original_analyzer'][0]['size']==0)
            campaign_differences.append(dict(name=c['name'],closed_campaign_net=str(net),account_marked_delta=str(D(str(c['final_value']))-D(str(c['initial_cash']))),same_account_result=abs(net-(D(str(c['final_value']))-D(str(c['initial_cash']))))<D('1e-8')))
    for obj in report['reviewed_source_files']:
        b=subprocess.check_output(['git','-C',a.repo,'show',report['source_commit']+':'+obj['path']])
        check(hashlib.sha256(b).hexdigest()==obj['sha256'])
        blob=hashlib.sha1(('blob '+str(len(b))+'\0').encode()+b).hexdigest();check(blob==obj['git_blob_sha'])
    m=report['actual_price_prefix'];p=Path(a.data_root)/'klines/1m/BTCUSDT-1m-2024-01.zip';rawzip=p.read_bytes()
    check(hashlib.sha256(rawzip).hexdigest()==m['source']['sha256'])
    with zipfile.ZipFile(io.BytesIO(rawzip)) as z:raw=z.read(z.namelist()[0])
    check(hashlib.sha256(raw).hexdigest()==m['source']['csv_sha256'])
    rows=list(csv.reader(io.StringIO(raw.decode())))
    if rows[0][0]=='open_time':rows=rows[1:]
    check(len(rows)==44640)
    for left,right in zip(rows,rows[1:]):check(int(right[0])-int(left[0])==60000)
    comparisons=list(csv.DictReader((out/'actual_prefix_comparison.csv').open()))
    check(len(comparisons)==44)
    for row in comparisons:check(int(row['compared_rows'])==1920 and int(row['past_changes'])==0)
    signals=list(csv.DictReader((out/'actual_BTC_signal_diagnostics.csv').open()))
    check(len(signals)==27);check(sum(x['long_signal']=='True' for x in signals)==16);check(sum(x['short_signal']=='True' for x in signals)==11)
    check(len({x['bar_start_utc'][:10] for x in signals})==17)
    check(hashlib.sha256((out/'actual_BTC_signal_diagnostics.csv').read_bytes()).hexdigest()==m['signals_csv_sha256'])
    check(hashlib.sha256((out/'actual_prefix_comparison.csv').read_bytes()).hexdigest()==m['prefix_csv_sha256'])
    check(not report['valid_market_profitability_backtest'] and not report['eligible'] and not report['forward_started'])
    check(D(str(report['zero_closed_nonflat_equity_summary']['max_drawdown_pct']))==0)
    result=dict(checks_passed=checks,maximum_cash_absolute_error=str(maxerr),synthetic_cases=len(cases),
        separate_campaign_and_account_totals=campaign_differences,actual_minute_continuity_steps=44639,
        actual_output_cells_compared_by_source_audit=44*1920,all_prefix_changes=0,market_profitability_verified=False,
        verifier_sha256=hashlib.sha256(Path(__file__).read_bytes()).hexdigest())
    (out/'independent_verification.json').write_text(json.dumps(result,indent=2)+'\n');print(json.dumps(result,indent=2))

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--data-root',required=True);p.add_argument('--output',required=True);main(p.parse_args())
