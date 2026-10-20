"""Independent Decimal cash/funding reconciliation for a frozen public4 report."""
import argparse,csv,io,json,zipfile,hashlib
from decimal import Decimal as D
from pathlib import Path
import lbank_futures_monitor as m
ap=argparse.ArgumentParser();ap.add_argument('--data',required=True);ap.add_argument('--report',required=True);ap.add_argument('--output',required=True);args=ap.parse_args()
root=Path(args.data);r=json.loads(Path(args.report).read_text())
def load(name):
 z=zipfile.ZipFile(root/name);return list(csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]))))
prices={int(x['open_time'])//1000:D(x['open']) for x in load('bars.zip')}
rates=[(int(x['calc_time'])//1000,D(x['last_funding_rate'])) for x in load('funding.zip')]
checks=0;complete=0
assert r['polls']==11520 and r['ready_polls']==10080
assert len({(x['id'],x['policy']) for x in r['rows']})==len(r['rows'])
for row in r['rows']:
 if row['complete_R_1'] is None:continue
 complete+=1;p=row['plan'];d=D(p['direction']);price=D(str(p['price']))
 denominator=D(str(-m.net_pnl_per_btc(int(d),p['price'],p['stop'])))
 legs=[x for x in r['legs'] if x['id']==row['id'] and x['policy']==row['policy']]
 assert abs(sum(x['fraction'] for x in legs)-1)<1e-12
 for k in (1,2):
  value=D(0)
  for leg in legs:
   en=price*(1+d*D('.0002')*k);ex=D(str(leg['price']))*(1-d*D('.0002')*k)
   cash=d*(ex-en)-D('.0006')*k*(en+ex)
   for t,rate in rates:
    if row['opened']<t<=leg['at']:
     q=d*rate*prices[t];cash-=q*k if q>0 else q
   value+=D(str(leg['fraction']))*cash
  assert abs(float(value/denominator)-row['complete_R_'+str(k)])<1e-9
  checks+=1
b=Path(args.report).read_bytes()
out=dict(decimal_checks=checks,complete_policy_rows=complete,unique_entry_ids=len({x['id'] for x in r['rows']}),raw_setup_ids=len(r['unique_raw_events']),report_sha256=hashlib.sha256(b).hexdigest(),report_git_blob=hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest(),source='25388b431c9c9d38df6a434670586c65f382d604',not_native_fill_test=True,promotion=False)
Path(args.output).write_text(json.dumps(out,sort_keys=True)+'\n');print(json.dumps(out))
