"""Independent official raw market-trade audit; never inspect strategy returns."""
import argparse,concurrent.futures,hashlib,io,json,re,urllib.request,zipfile
from pathlib import Path
from datetime import datetime,timezone
import numpy as np
import pandas as pd
from research.long_history.evaluate import digest,COLUMNS

BASE='https://data.binance.vision/data/futures/um/daily'
TARGETS={'2024-10-28':[1730149800],'2025-01-14':[1736861400,1736866800],'2025-01-29':[1738114500],'2025-08-29':[1756449300]}
CORE=('open','high','low','close','volume')


def download_verified(folder,url):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);path=folder/url.rsplit('/',1)[-1]
    with urllib.request.urlopen(url+'.CHECKSUM',timeout=30) as r:sha=r.read().decode().split()[0]
    if not re.fullmatch('[0-9a-fA-F]{64}',sha):raise ValueError('invalid published checksum')
    if not path.exists() or digest(path)!=sha:
        temp=path.with_suffix('.downloading');h=hashlib.sha256()
        with urllib.request.urlopen(url,timeout=60) as r,temp.open('wb') as f:
            while True:
                b=r.read(1024*1024)
                if not b:break
                h.update(b);f.write(b)
        if h.hexdigest()!=sha:raise ValueError('raw archive checksum mismatch')
        temp.replace(path)
    return path,dict(url=url,checksum_url=url+'.CHECKSUM',sha256=sha,bytes=path.stat().st_size,retrieved_utc=datetime.now(timezone.utc).isoformat())


def load_audit_inputs(folder):
    # A fresh clone needs only these six immutable-month inputs, not any old scratch manifest.
    source_dir=Path(__file__).resolve().parents[1]/'long_history'/'results'
    months={day[:7] for day in TARGETS};frames={};provenance=[];manifests={}
    for interval in ('1m','5m'):
        manifest=source_dir/f'source_manifest_2024_2026_{interval}.json'
        manifests[str(manifest.relative_to(Path(__file__).resolve().parents[2]))]=digest(manifest)
        entries=[r for r in json.loads(manifest.read_text())['archives'] if r['kind']=='klines' and r['interval']==interval and r['month'] in months]
        if len(entries)!=len(months):raise ValueError('frozen target-month manifest incomplete')
        parts=[]
        for record in entries:
            p=Path(folder)/'klines'/interval/record['url'].rsplit('/',1)[-1]
            if not p.exists():p,_=download_verified(p.parent,record['url'])
            if digest(p)!=record['sha256']:raise ValueError('monthly input differs from frozen source SHA: '+str(p))
            with zipfile.ZipFile(p) as z:
                if len(z.namelist())!=1:raise ValueError('unexpected monthly ZIP members')
                raw=z.read(z.namelist()[0])
            f=pd.read_csv(io.BytesIO(raw),header=0 if not raw[:1].isdigit() else None)
            f.columns=COLUMNS;f=f.astype(float);f['start']=(f.start/1000).astype('int64');parts.append(f)
            provenance.append({k:record[k] for k in ('month','kind','interval','url','checksum_url','sha256')})
        frames[interval]=pd.concat(parts,ignore_index=True).sort_values('start').reset_index(drop=True)
        if frames[interval].start.duplicated().any():raise ValueError('duplicate original source minute')
    return frames['1m'],frames['5m'],dict(frozen_manifest_sha256=manifests,archives=provenance)


def read_trade_windows(path,targets):
    parts=[];previous_id=previous_time=None;rows=0;id_gaps=0
    with zipfile.ZipFile(path) as z:
        names=z.namelist()
        if len(names)!=1 or not names[0].endswith('.csv'):raise ValueError('unexpected raw trade ZIP')
        if z.getinfo(names[0]).file_size>2_000_000_000:raise ValueError('uncompressed archive exceeds audit bound')
        with z.open(names[0]) as f:first=f.readline().decode().strip()
        header=not first[:1].isdigit()
        with z.open(names[0]) as f:
            for g in pd.read_csv(f,header=0 if header else None,chunksize=200000):
                if not header:g.columns=['id','price','qty','quote_qty','time','is_buyer_maker']
                g.columns=[str(k).lower() for k in g.columns]
                required={'id','price','qty','quote_qty','time','is_buyer_maker'}
                if not required.issubset(g.columns):raise ValueError('unsupported trade columns:'+str(list(g.columns)))
                ids=g.id.to_numpy(dtype='int64');ts=g.time.to_numpy(dtype='int64')
                if (np.diff(ids)<=0).any() or (np.diff(ts)<0).any():raise ValueError('raw trade order invalid')
                if previous_id is not None and (ids[0]<=previous_id or ts[0]<previous_time):raise ValueError('raw chunk order invalid')
                if (g.price<=0).any() or (g.qty<0).any() or not np.isfinite(g[['price','qty','quote_qty']].to_numpy()).all():raise ValueError('invalid trade values')
                if ts.min()<1_000_000_000_000 or ts.max()>10_000_000_000_000:raise ValueError('unexpected futures timestamp units')
                id_gaps+=int((np.diff(ids)!=1).sum())+int(previous_id is not None and ids[0]!=previous_id+1)
                previous_id=int(ids[-1]);previous_time=int(ts[-1]);rows+=len(g)
                keep=np.zeros(len(g),dtype=bool)
                for t in targets:keep|=(ts>=(t-60)*1000)&(ts<(t+360)*1000)
                if keep.any():parts.append(g.loc[keep].copy())
    if not parts:raise ValueError('no target trades')
    return pd.concat(parts,ignore_index=True),dict(archive_rows=rows,archive_id_gap_count=id_gaps)


def aggregate_window(trades,start):
    # Neighboring actual records are required to establish coverage at both edges.
    before=trades[trades.time<start*1000].tail(1);after=trades[trades.time>=(start+300)*1000].head(1)
    inside=trades[(trades.time>=start*1000)&(trades.time<(start+300)*1000)].copy()
    if len(before)!=1 or len(after)!=1 or inside.empty:raise ValueError('target window lacks edge coverage')
    context=pd.concat([before,inside,after]);ids=context.id.to_numpy(dtype='int64')
    if (np.diff(ids)!=1).any():raise ValueError('target trade IDs incomplete or duplicated')
    if (np.diff(context.time.to_numpy())<0).any():raise ValueError('target timestamps unordered')
    inside['start']=(inside.time//60000*60).astype('int64')
    maker=inside.is_buyer_maker.astype(str).str.lower().map({'true':True,'false':False})
    if maker.isna().any():raise ValueError('unsupported maker flag')
    buy=~maker.astype(bool)
    inside['buy_volume']=np.where(buy,inside.qty,0.)
    inside['buy_quote_volume']=np.where(buy,inside.quote_qty,0.)
    g=inside.groupby('start',sort=True).agg(open=('price','first'),high=('price','max'),low=('price','min'),close=('price','last'),volume=('qty','sum'),
        quote_volume=('quote_qty','sum'),trades=('id','count'),buy_volume=('buy_volume','sum'),buy_quote_volume=('buy_quote_volume','sum'))
    if list(g.index)!=list(range(start,start+300,60)):raise ValueError('one or more target minutes absent; no interpolation')
    return g,dict(first_id=int(inside.id.iloc[0]),last_id=int(inside.id.iloc[-1]),target_rows=len(inside),before_id=int(before.id.iloc[0]),after_id=int(after.id.iloc[0]))


def compare_original(rebuilt,original):
    differences={k:float(np.max(np.abs(rebuilt[k].to_numpy()-original[k].to_numpy()))) for k in CORE}
    return all(v<=1e-6 for v in differences.values()),differences


def coverage_diagnostics(trades,start):
    """Report why evidence failed without weakening the preregistered release rule."""
    neighborhood=trades[(trades.time>=(start-60)*1000)&(trades.time<(start+360)*1000)]
    before=neighborhood[neighborhood.time<start*1000].tail(1)
    after=neighborhood[neighborhood.time>=(start+300)*1000].head(1)
    inside=neighborhood[(neighborhood.time>=start*1000)&(neighborhood.time<(start+300)*1000)]
    context=pd.concat([before,inside,after]);ids=context.id.to_numpy(dtype='int64');times=context.time.to_numpy(dtype='int64')
    differences=np.diff(ids);where=np.flatnonzero(differences!=1)
    gaps=[dict(before_id=int(ids[i]),after_id=int(ids[i+1]),missing_ids=int(differences[i]-1),before_time_ms=int(times[i]),after_time_ms=int(times[i+1])) for i in where[:20]]
    return dict(window_start_utc=pd.Timestamp(start,unit='s',tz='UTC').isoformat(),target_rows=len(inside),before_present=len(before)==1,after_present=len(after)==1,
        target_minute_counts={str(int(t)):int(n) for t,n in inside.groupby(inside.time//60000*60).size().items()},
        first_target_time_ms=None if inside.empty else int(inside.time.iloc[0]),last_target_time_ms=None if inside.empty else int(inside.time.iloc[-1]),
        context_id_discontinuities=int(len(where)),missing_context_ids=int(np.maximum(differences-1,0).sum()),id_gap_examples=gaps,
        examples_truncated=len(where)>20)


def audit_day(day,targets,raw_folder,one,five):
    url=f'{BASE}/trades/BTCUSDT/BTCUSDT-trades-{day}.zip'
    try:
        path,source=download_verified(raw_folder,url);trades,stats=read_trade_windows(path,targets);windows=[]
        for start in targets:
            row=dict(start=start,day=day,released=False,coverage_diagnostics=coverage_diagnostics(trades,start))
            try:
                g,ids=aggregate_window(trades,start);original=one.set_index('start').loc[g.index]
                match,difference=compare_original(g,original)
                agg=dict(open=float(g.open.iloc[0]),high=float(g.high.max()),low=float(g.low.min()),close=float(g.close.iloc[-1]),volume=float(g.volume.sum()))
                oldfive=five.set_index('start').loc[start]
                row.update(raw_trade_coverage=ids,minute_ohlcv_matches=match,minute_max_abs_diff=difference,
                    reconstructed_minutes=g.reset_index().to_dict('records'),original_minutes=original.reset_index()[['start',*CORE]].to_dict('records'),
                    reconstructed_five=agg,original_five={k:float(oldfive[k]) for k in CORE},
                    five_max_abs_diff={k:abs(agg[k]-float(oldfive[k])) for k in CORE},released=match,
                    verdict='release_original_minute_series_supported_by_raw_trades' if match else 'retain_quarantine_raw_does_not_match_all_original_minutes')
            except Exception as exc:row.update(verdict='retain_quarantine_incomplete_raw_evidence',error=str(exc))
            windows.append(row)
        return dict(day=day,source=source,raw_statistics=stats,windows=windows)
    except Exception as exc:return dict(day=day,source_url=url,error=str(exc),windows=[dict(start=t,released=False,verdict='retain_quarantine_source_access_or_integrity_error') for t in targets])


def boundary_quote(raw_folder):
    url=f'{BASE}/klines/BTCUSDT/1m/BTCUSDT-1m-2026-10-01.zip';p,source=download_verified(raw_folder,url)
    with zipfile.ZipFile(p) as z:raw=z.read(z.namelist()[0])
    f=pd.read_csv(io.BytesIO(raw),header=0 if not raw[:1].isdigit() else None);f.columns=COLUMNS;f=f.astype(float);f['start']=(f.start/1000).astype('int64')
    expected=int(pd.Timestamp('2026-10-01',tz='UTC').timestamp())
    if len(f)!=1440 or int(f.start.iloc[0])!=expected or (f.start.diff().dropna()!=60).any():raise ValueError('boundary original day incomplete')
    first=f.iloc[0]
    if min(first.open,first.high,first.low,first.close)<=0 or first.low>min(first.open,first.close) or first.high<max(first.open,first.close):raise ValueError('invalid boundary minute')
    return dict(source=source,used_rows=1,purpose='September monthly closing boundary only, no October strategy returns',minute=first.to_dict())


def main(output,raw_folder,data_folder=None):
    root=Path(__file__).resolve().parents[3];data=Path(data_folder) if data_folder else root/'binance-futures-verified'
    one,five,inputs=load_audit_inputs(data)
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        tasks={pool.submit(audit_day,d,t,raw_folder,one,five):d for d,t in TARGETS.items()};results=[]
        for f in concurrent.futures.as_completed(tasks):
            x=f.result();results.append(x);print(json.dumps(dict(day=x['day'],error=x.get('error'),verdicts=[(r['start'],r['verdict']) for r in x['windows']])),flush=True)
    results.sort(key=lambda x:x['day']);released=sorted(r['start'] for d in results for r in d['windows'] if r['released']);unresolved=sorted(set(t for ts in TARGETS.values() for t in ts)-set(released))
    try:boundary=boundary_quote(raw_folder)
    except Exception as exc:boundary=dict(error=str(exc))
    report=dict(started_or_retrieved_utc=min(d.get('source',{}).get('retrieved_utc',datetime.now(timezone.utc).isoformat()) for d in results),completed_utc=datetime.now(timezone.utc).isoformat(),
        protocol_sha256=digest(Path(__file__).with_name('source_reconciliation_protocol_20261020.json')),code_sha256=digest(__file__),inputs=inputs,
        target_dates=results,released_starts=released,unresolved_starts=unresolved,boundary=boundary,repair_scope='Only validation-mask release of unchanged original1m bars when every core field has complete raw market-trade support. No synthetic price repairs.',
        strategy_performance_examined=False,production_changed=False,native_lbank_execution_verified=False)
    p=Path(output);p.parent.mkdir(parents=True,exist_ok=True);p.write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps(dict(released_starts=released,unresolved_starts=unresolved,boundary_available='minute' in boundary)),flush=True)

if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--output',required=True);p.add_argument('--raw-folder',required=True);p.add_argument('--data-folder');a=p.parse_args();main(a.output,a.raw_folder,a.data_folder)
