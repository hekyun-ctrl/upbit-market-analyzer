"""Finite keyless documented PUBLIC endpoint checks; no collector loop."""
import urllib.request,json,hashlib,datetime,pathlib,concurrent.futures
base='https://lbkperp.lbank.com';paths=['/cfd/openApi/v1/pub/instrument?productGroup=SwapU','/cfd/openApi/v1/pub/marketOrder?symbol=BTCUSDT&depth=5']
def probe(path):
 row={'url':base+path,'requested_at':datetime.datetime.now(datetime.timezone.utc).isoformat()}
 try:
  with urllib.request.urlopen(base+path,timeout=10) as r:raw=r.read(2000000);row.update(http_status=r.status,bytes=len(raw),SHA256=hashlib.sha256(raw).hexdigest())
  row['response']=json.loads(raw)
 except Exception as e:row.update(error=type(e).__name__,detail=str(e)[:200])
 return row
if __name__=='__main__':
 with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:rows=list(pool.map(probe,paths))
 p=pathlib.Path(__file__).with_name('public_probe.json');p.write_text(json.dumps(rows,indent=2)+'\n');print(json.dumps([{k:v for k,v in r.items() if k!='response'} for r in rows]))
