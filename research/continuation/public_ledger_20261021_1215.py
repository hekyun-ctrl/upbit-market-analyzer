"""Frozen descriptive audit, not a strategy backtest. Python standard library only."""
import csv,json,hashlib,pathlib,collections,statistics,urllib.request
from decimal import Decimal as D
ROOT=pathlib.Path(__file__).parent
PIN='27d2d50fc0def523d417cc84c42cc16ee3432d86'
files=['position_episodes.csv','post_fill_summary.csv','tradetrace.py']
for name in files:
 p=ROOT/name
 if not p.exists():
  rel=name if name.endswith('.py') else 'reports/tables/'+name
  p.write_bytes(urllib.request.urlopen('https://raw.githubusercontent.com/AaronL725/TradeTrace/'+PIN+'/'+rel,timeout=60).read())
rows=list(csv.DictReader((ROOT/files[0]).open()))
closed=[r for r in rows if r['status'] in ('closed','reversed')]
net=[D(r['calculated_pnl_xbt'])-D(r['fees_xbt']) for r in closed]
fee=sum((D(r['fees_xbt']) for r in closed),D(0))
wins=sum((v for v in net if v>0),D(0));loss=-sum((v for v in net if v<0),D(0))
summary=list(csv.DictReader((ROOT/files[1]).open()))
checks=[]
for r in summary:
 n=int(r['sample_5m']); share=D(r['positive_share_5m']); k=int((share*n).to_integral_value())
 assert abs(share-D(k)/n)<D('1e-12')
 checks.append({'dimension':r['dimension'],'segment':r['segment'],'scope':r['scope'],'n':n,'positive_5m_count':k})
report={'version':'public_discretionary_ledger_descriptive_v1','pin':PIN,'source_ledger_latest_pin':'9ff0d562cffd943d79916c9ce05adae627c8d9a2','same_underlying_trader_not_independent_sources':True,'hashes':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in files},'episodes':len(rows),'status':dict(collections.Counter(r['status'] for r in rows)),'closed_or_reversed':len(closed),'calculated_gross_XBT':str(sum((D(r['calculated_pnl_xbt']) for r in closed),D(0))),'signed_execution_fees_XBT':str(fee),'calculated_after_execution_fee_XBT':str(sum(net,D(0))),'positive_episode_count_after_fee':sum(v>0 for v in net),'negative_episode_count_after_fee':sum(v<0 for v in net),'descriptive_PF_excluding_funding':str(wins/loss),'median_duration_hours':statistics.median(float(r['duration_hours']) for r in closed),'all_zero_ledger_pnl_field':all(D(r['ledger_pnl_xbt'])==0 for r in rows),'summary_integer_checks':checks,'full_cost_net':None,'full_double_cost':None,'TP1_Wilson':None,'actual_5m_emergency':None,'native_LBank_validation':False,'rules_replicable':False,'adopt':False,'limits':['Derived episode table; raw execution reconciliation not performed in this run.','Calculated inverse XBT PnL less signed execution fee only; funding not allocated. Not fiat/USDT PnL.','Closed/reversed episodes include dynamic additions/reductions; not fixed alert trades.','Postfill side-return includes reductions/closes and order completion hindsight; not independent initial entries or TP1/emergency outcomes.','Same trader selected retrospectively; no causal maker alpha or transferable policy.','Latest source ledger and analysis submodule snapshots differ; do not join without exact submodule pin.']}
(ROOT/'report.json').write_text(json.dumps(report,indent=2)+'\n')
print(json.dumps({k:v for k,v in report.items() if k not in ('summary_integer_checks','limits','hashes')},indent=2))
