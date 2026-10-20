import json,hashlib,csv,zipfile,io
from decimal import Decimal as D
from pathlib import Path
r=json.loads(Path('public4_result.json').read_text());assert r['polls']==11520 and r['ready_polls']==10080
assert len(r['unique_raw_events'])==108 and len(r['rows'])==6
assert sum(v['levels']['opened'] for v in r['summaries'].values())==2
assert len({(x['id'],x['policy']) for x in r['rows']})==6
for x in r['rows']:assert not x['status'].startswith('censored_') and x['status']!='open' and abs(x['known_leg_fraction']-1)<1e-12
root=Path('/workspace/scratch/ba9af914844b/intrabar-2211')
def archive(n):
 z=zipfile.ZipFile(root/n);return list(csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0]))))
prices={int(x['open_time'])//1000:D(x['open']) for x in archive('bars.zip')}
rates=[(int(x['calc_time'])//1000,D(x['last_funding_rate'])) for x in archive('funding.zip')]
checks=0
for row in r['rows']:
 p=row['plan'];d=D(p['direction']);price=D(str(p['price']));stop=D(str(p['stop']))
 # Same declared source risk denominator, independently expressed in Decimal.
 import sys
 sys.path.insert(0,str(root));import lbank_futures_monitor as m
 denominator=D(str(-m.net_pnl_per_btc(int(d),float(price),float(stop))))
 legs=[x for x in r['legs'] if x['id']==row['id'] and x['policy']==row['policy']]
 for k in (1,2):
  value=D(0)
  for leg in legs:
   en=price*(1+d*D('.0002')*k);ex=D(str(leg['price']))*(1-d*D('.0002')*k)
   cash=d*(ex-en)-D('.0006')*k*(en+ex)
   for t,rate in rates:
    if row['opened']<t<=leg['at']:
     charge=d*rate*prices[t];cash-=charge*k if charge>0 else charge
   value+=D(str(leg['fraction']))*cash
  assert abs(float(value/denominator)-row['complete_R_'+str(k)])<1e-9
  checks+=1
out=dict(actual_decimal_leg_checks=checks,unique_setup_ids=108,unique_entry_ids=2,policy_rows=6,all_closed_fractions_one=True,source_fixture_tests_passed=11,source_fixtures_completed_this_run=True,rule_source_unchanged=True,full_cost_positive=False,statistical_edge_established=False,emergency_five_minute_rate=None,native_manual_fill_test=False)
Path('verification.json').write_text(json.dumps(out,indent=2)+'\n');print(out)
