"""Fixed post-entry diagnostics from archived real minute ranges, not warning delivery."""
import csv,io,zipfile,json,hashlib,math
from pathlib import Path
r=json.loads(Path('rsi_result.json').read_text())
b=Path('data/bars.zip').read_bytes()
assert hashlib.sha256(b).hexdigest()=='b69ca5b2c2d1ae00d68d2790d09138cb2a44085ff19e0da5dc98b569ea7e489e'
z=zipfile.ZipFile(io.BytesIO(b));bars={int(x['open_time'])//1000:{k:float(x[k]) for k in ('open','high','low','close')} for x in csv.DictReader(io.TextIOWrapper(z.open(z.namelist()[0])))}
events=[]
for row in r['rows']:
 if row['policy']!='levels':continue
 p=row['plan'];d=p['direction'];t=int(row['opened']);g=[bars.get(t+i*60) for i in range(5)]
 assert all(x is not None for x in g)
 adverse=max(max(0,d*(p['price']-(x['low'] if d==1 else x['high']))) for x in g)
 first='neither_in_5min'
 for i,x in enumerate(g):
  stop=x['low']<=p['stop'] if d==1 else x['high']>=p['stop']
  tp=x['high']>=p['target1'] if d==1 else x['low']<=p['target1']
  if stop or tp:
   first='ambiguous_same_minute' if stop and tp else 'stop_first' if stop else 'TP1_first'
   break
 events.append(dict(id=row['id'],strategy=p['strategy'],opened=t,
  adverse_first5min_fraction=adverse/p['price'],
  adverse_first5min_initial_price_stop_units=adverse/abs(p['price']-p['stop']),
  range_first_touch=first,real_LBank_urgent_warning=None,manual_fill=None))
def wilson(k,n):
 if not n:return None
 z=1.959963984540054;ph=k/n
 return (ph+z*z/(2*n)-z*math.sqrt(ph*(1-ph)/n+z*z/(4*n*n)))/(1+z*z/n)
n=len(events);k=sum(x['stage']>0 for x in r['rows'] if x['policy']=='levels')
out=dict(protocol='RSI_postentry_archive_diagnostics_v1',unique_entries=n,TP1_states=k,
 TP1_wilson_lower=wilson(k,n),events=events,urgent_warning_rate=None,manual_fills=None,
 expectancy_95_correlated_lower=None,
 uncertainty_limit='One reused 7day development window; no robust correlated lower bound established, no promotion.',
 notes='Real minute high/low after entry used for evaluation only, never signal generation. Same-minute stop/TP1 unresolved. Five-minute adverse move is not delivered emergency-warning incidence.')
Path('rsi_diagnostics.json').write_text(json.dumps(out,indent=2)+'\n')
print(json.dumps(out))
