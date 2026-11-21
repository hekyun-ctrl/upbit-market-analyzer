"""P0 inventory of previously committed native evidence; never treats metadata as verified."""
import json,hashlib,pathlib,datetime
ROOT=pathlib.Path(__file__).resolve().parents[2]
names=['native-5m-audit-20261017.json','signal-coherence-native-20261019.json','four-traders-native-20261019.json','rsi-divergence-native-20261018.json']
records=[];unique={}
for name in names:
 p=ROOT/'research'/name
 if not p.exists():continue
 x=json.loads(p.read_text());bars=x.get('bars',[])
 rows=[b for b in bars if isinstance(b,dict) and b.get('period')==300 and b.get('quality')=='stream_closed']
 for b in rows:unique[(b['period'],b['start'])]=b
 records.append({'path':str(p.relative_to(ROOT)),'SHA256':hashlib.sha256(p.read_bytes()).hexdigest(),'rows':len(rows),'closure':x.get('closure'),'source':x.get('source')})
times=sorted(t for _,t in unique);runs=[];run=[]
for t in times:
 if run and t!=run[-1]+300:runs.append(run);run=[]
 run.append(t)
if run:runs.append(run)
out={'spec_version':'ZB-1.0','baseline_commit':'25388b431c9c9d38df6a434670586c65f382d604','files':records,'deduplicated_5m':len(times),'max_consecutive_5m':max(map(len,runs),default=0),'earliest_UTC':datetime.datetime.fromtimestamp(times[0],datetime.timezone.utc).isoformat() if times else None,'latest_end_UTC':datetime.datetime.fromtimestamp(times[-1]+300,datetime.timezone.utc).isoformat() if times else None,'raw_1m_in_this_inventory':0,'verified_original5m_not_aggregate':0,'ninety_day_native_dataset_available':False,'tick':None,'quantity_step':None,'volume_unit':None,'contract_multiplier':None,'fee_entry':None,'fee_exit':None,'funding_schedule':None,'historical_displayed_funding':None,'classification':'EVIDENCE_INSUFFICIENT','P2_profitability_comparison_possible':False,'reason':'Archived inferred5m aggregates are not a complete raw1m+original5m+cost dataset. No exchange/spot substitution or invented zero cost.'}
p=ROOT/'research/zerobase/data_manifest.json';p.write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out,indent=2))
