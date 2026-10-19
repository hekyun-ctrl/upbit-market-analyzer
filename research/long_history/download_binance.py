"""First-party USD-M perpetual archives; verified bytes, no exchange orders."""
import argparse,concurrent.futures,csv,hashlib,io,json,time,urllib.request,zipfile
from pathlib import Path
from datetime import datetime,timezone

BASE='https://data.binance.vision/data/futures/um/monthly'


def download(folder,month,kind='klines',interval='5m'):
    name=f'BTCUSDT-{interval}-{month}.zip' if kind=='klines' else f'BTCUSDT-fundingRate-{month}.zip'
    url=f'{BASE}/klines/BTCUSDT/{interval}/{name}' if kind=='klines' else f'{BASE}/fundingRate/BTCUSDT/{name}'
    folder=Path(folder)/kind/interval;folder.mkdir(parents=True,exist_ok=True);path=folder/name
    for attempt in range(3):
        try:
            with urllib.request.urlopen(url+'.CHECKSUM',timeout=30) as r:checksum=r.read().decode().strip().split()[0]
            data=path.read_bytes() if path.exists() else None
            if data is None or hashlib.sha256(data).hexdigest()!=checksum:
                with urllib.request.urlopen(url,timeout=45) as r:data=r.read()
                if hashlib.sha256(data).hexdigest()!=checksum:raise ValueError('checksum mismatch')
                path.write_bytes(data)
            with zipfile.ZipFile(io.BytesIO(data)) as z:
                names=z.namelist()
                if len(names)!=1 or not names[0].endswith('.csv'):raise ValueError('unexpected archive')
                lines=z.read(names[0]).decode().splitlines()
            return dict(month=month,kind=kind,interval=interval,url=url,checksum_url=url+'.CHECKSUM',sha256=checksum,
                        zip_bytes=len(data),csv_rows=len(lines)-int(not lines[0][0].isdigit()),
                        first=lines[0] if lines[0][0].isdigit() else lines[1],last=lines[-1],path=str(path))
        except Exception:
            if attempt==2:raise
            time.sleep(1+attempt)


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--folder',required=True);p.add_argument('--start',default='2024-01');p.add_argument('--end',default='2026-09');p.add_argument('--interval',default='5m');p.add_argument('--funding',action='store_true');a=p.parse_args()
    y,m=map(int,a.start.split('-'));end=tuple(map(int,a.end.split('-')));months=[]
    while (y,m)<=end:
        months.append(f'{y:04}-{m:02}');m+=1
        if m==13:y+=1;m=1
    jobs=[(month,'klines') for month in months]+([(month,'fundingRate') for month in months] if a.funding else [])
    records=[]
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures={pool.submit(download,a.folder,month,kind,a.interval):(month,kind) for month,kind in jobs}
        for f in concurrent.futures.as_completed(futures):
            r=f.result();records.append(r);print(json.dumps({k:r[k] for k in ('month','kind','csv_rows','zip_bytes')}),flush=True)
    out=Path(a.folder)/f'manifest-{a.interval}.json';out.write_text(json.dumps(dict(source='Binance USD-M BTCUSDT perpetual, not LBank',retrieved_utc=datetime.now(timezone.utc).isoformat(),archives=sorted(records,key=lambda r:(r['kind'],r['month']))),indent=2)+'\n')
    print('MANIFEST',str(out),flush=True)
