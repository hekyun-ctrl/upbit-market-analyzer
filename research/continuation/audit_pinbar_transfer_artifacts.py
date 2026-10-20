"""Separate Decimal ledger reconciliation; no strategy fitting or parameter search."""
import argparse
from decimal import Decimal,localcontext
import hashlib
import io
import json
from pathlib import Path
import zipfile
import numpy as np
import pandas as pd
from evaluate_pinbar_transfer import replay,COLS

def main():
    p=argparse.ArgumentParser();p.add_argument('--results',type=Path,required=True)
    p.add_argument('--folder',type=Path,required=True);a=p.parse_args()
    r=json.loads((a.results/'report.json').read_text());root=Path(__file__).resolve().parents[2]
    sources=json.loads((a.results/'sources.json').read_text())
    expected={}
    for name in ('source_manifest_2024_2026_1m.json','source_manifest_2024_2026_5m.json'):
        for x in json.loads((root/'research/long_history/results'/name).read_text())['archives']:
            expected[x['url']]=x['sha256']
    assert all(expected[x['url']]==x['sha256'] for x in sources)
    output=dict(archive_SHA_reconciled_to_persisted_manifests=len(sources),scenarios={})
    for key,scenario in r['scenarios'].items():
        raw=(a.results/scenario['ledger_file']).read_bytes()
        assert hashlib.sha256(raw).hexdigest()==scenario['ledger_sha256']
        rows=[json.loads(x) for x in raw.splitlines()];closed=[x for x in rows if x['status']=='closed']
        assert len(closed)==scenario['all']['closed']
        assert sum(x['all']['closed'] if 'all' in x else x['closed'] for x in scenario['years'].values())==len(closed)
        assert all(x['entry_at']-x['signal_at']==60*x['delay_minutes'] for x in rows)
        maximum=0.;funds=0;end=-1;values=[]
        for row in rows:
            assert row['entry_at']>=end
            end=row.get('exit_at',row['entry_at']+1500)
            if row['status']!='closed':
                assert 'base' not in row and 'exit_reference' not in row
                continue
            assert row['entry_at']<row['exit_at']<=row['entry_at']+1500
            assert all(row['entry_at']<x['time']<=row['exit_at'] and x['debit']>=0 for x in row['funding_events'])
            assert abs(sum(x['debit'] for x in row['funding_events'])-row['funding_debit_unit'])<1e-12
            funds+=len(row['funding_events'])
            for multiple,label in [(1,'base'),(2,'double')]:
                with localcontext() as ctx:
                    ctx.prec=45
                    d=Decimal(row['side']);e=Decimal(str(row['entry_reference']));x=Decimal(str(row['exit_reference']))
                    m=Decimal(multiple);fee=Decimal('.0006')*m;slip=Decimal('.0002')*m
                    ef=e*(1+d*slip);xf=x*(1-d*slip)
                    funding=Decimal(str(row['funding_debit_unit']))*m
                    net=(d*(xf-ef)-fee*(ef+xf)-funding)/(e*Decimal('.0035'))
                    maximum=max(maximum,abs(float(net)-row[label]['net_R']))
                assert row['double']['net_R']<row['base']['net_R']
            values.append(row['base']['net_R'])
        assert maximum<1e-9
        assert abs(sum(values)-scenario['all']['base_sum_R'])<1e-8
        positives=sum(x for x in values if x>0);negatives=-sum(x for x in values if x<0)
        assert abs(positives/negatives-scenario['all']['PF'])<1e-10
        # Real January prefix with future months absent: closed rows must match.
        path=a.folder/'klines/1m/BTCUSDT-1m-2024-01.zip'
        with zipfile.ZipFile(path) as z:minute=pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
        minute.columns=COLS;b=minute[COLS[:6]].to_numpy(float);b[:,0]/=1000
        fp=a.folder/'fundingRate/5m/BTCUSDT-fundingRate-2024-01.zip'
        with zipfile.ZipFile(fp) as z:f=pd.read_csv(io.BytesIO(z.read(z.namelist()[0])))
        funding=np.c_[f.calc_time.to_numpy()/1000,f.last_funding_rate.to_numpy()]
        delay=int(key.split('_')[1][0]);prefix,_=replay(b,np.ones(len(b),bool),funding,delay)
        before={x['entry_at']:x for x in prefix if x['status']=='closed'}
        cutoff=int(b[-1,0])+60
        after={x['entry_at']:x for x in rows if x['status']=='closed' and x['exit_at']<=cutoff}
        assert before==after
        output['scenarios'][key]=dict(rows=len(rows),closed=len(closed),
            independent_Decimal_cash_checks=2*len(closed),max_abs_net_R_error=maximum,
            funding_event_rows=funds,actual_January_closed_prefix_rows=len(before),
            future_append_closed_prefix_changes=0,unknown_rows_excluded_from_cash_not_zero_profit=True)
    output['passed']=True
    (a.results/'independent_audit.json').write_text(json.dumps(output,indent=2)+'\n')
    print(json.dumps(output,indent=2))

if __name__=='__main__':main()
