"""Resolve archive inconsistencies before inspecting strategy outcomes."""
import concurrent.futures,hashlib,io,json,urllib.request,zipfile
from datetime import datetime,timezone
from pathlib import Path
import numpy as np
import pandas as pd
from research.long_history.evaluate import load_archives,aggregate_minutes,COLUMNS


def run(folder):
    root=Path(folder);monthly_minutes=load_archives(root,'1m');a=aggregate_minutes(monthly_minutes);b=load_archives(root,'5m')
    j=a.merge(b,on='start',suffixes=('_minute','_five'));bad=np.zeros(len(j),dtype=bool)
    for k in ('open','high','low','close','volume'):bad|=np.abs(j[k+'_minute']-j[k+'_five'])>1e-6
    cols=['start']+[k+s for k in ('open','high','low','close','volume') for s in ('_minute','_five')]
    records=j.loc[bad,cols].to_dict('records');days=sorted({datetime.fromtimestamp(r['start'],timezone.utc).strftime('%Y-%m-%d') for r in records})
    def get(day,interval):
        name=f'BTCUSDT-{interval}-{day}.zip';url=f'https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/{interval}/{name}'
        with urllib.request.urlopen(url+'.CHECKSUM',timeout=25) as r:sha=r.read().decode().split()[0]
        path=root/'daily_audit'/name;path.parent.mkdir(exist_ok=True)
        data=path.read_bytes() if path.exists() else None
        if data is None or hashlib.sha256(data).hexdigest()!=sha:
            with urllib.request.urlopen(url,timeout=40) as r:data=r.read()
            if hashlib.sha256(data).hexdigest()!=sha:raise ValueError('daily checksum mismatch')
            path.write_bytes(data)
        with zipfile.ZipFile(io.BytesIO(data)) as z:raw=z.read(z.namelist()[0])
        f=pd.read_csv(io.BytesIO(raw),header=0 if not raw[:1].isdigit() else None);f.columns=COLUMNS
        f=f.astype(float);f['start']=(f['start']/1000).astype('int64')
        return (day,interval),dict(url=url,sha256=sha,bytes=len(data)),f
    sources=[];frames={}
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        for key,source,f in pool.map(lambda x:get(*x),[(d,i) for d in days for i in ('1m','5m')]):sources.append(source);frames[key]=f
    checks=[]
    for day in days:
        daily=aggregate_minutes(frames[(day,'1m')]);five=frames[(day,'5m')];joined=daily.merge(five,on='start',suffixes=('_minute','_five'))
        diff={k:int((np.abs(joined[k+'_minute']-joined[k+'_five'])>1e-6).sum()) for k in ('open','high','low','close','volume')}
        monthly=monthly_minutes[(monthly_minutes['start']>=int(pd.Timestamp(day,tz='UTC').timestamp()))&(monthly_minutes['start']<int(pd.Timestamp(day,tz='UTC').timestamp())+86400)]
        jm=monthly.merge(frames[(day,'1m')],on='start',suffixes=('_monthly','_daily'))
        mdiff={k:int((np.abs(jm[k+'_monthly']-jm[k+'_daily'])>1e-6).sum()) for k in ('open','high','low','close','volume')}
        bad_min=np.zeros(len(jm),dtype=bool)
        for key in ('open','high','low','close','volume'):bad_min|=np.abs(jm[key+'_monthly']-jm[key+'_daily'])>1e-6
        checks.append(dict(day=day,daily_minute_vs_daily_five=diff,monthly_minute_vs_daily_minute=mdiff,discrepant_original_minute_starts=jm.loc[bad_min,'start'].astype(int).tolist()))
    out=dict(monthly_disagreements=records,daily_crosschecks=checks,daily_sources=sources,
             price_results_inspected=False,decision='quarantine inconsistent intervals; no price interpolation or performance-dependent correction')
    out['quarantined_starts']=sorted({int(r['start']) for r in records}|{int(t//300*300) for c in checks for t in c['discrepant_original_minute_starts']})
    out['audit_revision']='raw-minute comparisons; earlier draft accidentally compared 5m aggregates for monthly/daily minute agreement'
    (root/'integrity_audit.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))


if __name__=='__main__':
    import sys
    run(sys.argv[1])
