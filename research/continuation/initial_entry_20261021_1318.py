"""Fixed full-set initial-entry diagnostic; descriptive proxy, not a tradable strategy."""
import csv,json,pathlib,hashlib,urllib.request,statistics,collections,datetime
from decimal import Decimal as D
P=pathlib.Path(__file__).parent;PIN='27d2d50fc0def523d417cc84c42cc16ee3432d86'
for n in ['order_fill_summary.csv','position_actions.csv']:
 if not (P/n).exists():(P/n).write_bytes(urllib.request.urlopen('https://raw.githubusercontent.com/AaronL725/TradeTrace/'+PIN+'/reports/tables/'+n,timeout=60).read())
orders=list(csv.DictReader((P/'order_fill_summary.csv').open()));actions=list(csv.DictReader((P/'position_actions.csv').open()))
assert len({r['orderID'] for r in orders})==len(orders)
assert len({r['execID'] for r in actions})==len(actions)
by=collections.defaultdict(list)
for a in actions:by[a['orderID']].append(a)
def ts(s):return datetime.datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()
def stats(rows):
 vals=[float(r['signed_return_5m_bps']) for r in rows if r['signed_return_5m_bps']!=''];lags=[ts(r['last_timestamp'])-ts(r['first_timestamp']) for r in rows]
 fee=[];missing_join=0
 for r in rows:
  aa=by[r['orderID']]
  if not aa:missing_join+=1;continue
  notional=sum((D(a['qty'])/D(a['price']) for a in aa),D(0));cost=sum((D(a['fee']) for a in aa),D(0));fee.append(float(cost/notional*10000))
 return {'orders':len(rows),'price_samples':len(vals),'missing_price':len(rows)-len(vals),'positive_5m':sum(v>0 for v in vals),'median_5m_bps':statistics.median(vals) if vals else None,'mean_5m_bps':statistics.mean(vals) if vals else None,'last_first_seconds_median':statistics.median(lags) if lags else None,'last_first_seconds_max':max(lags,default=None),'orders_fill_span_over_5min':sum(x>300 for x in lags),'missing_action_join':missing_join,'signed_entry_fee_bps_median':statistics.median(fee) if fee else None,'conditional_fixed_total_roundtrip_costs_bps':{str(c):{'positive':sum(v-c>0 for v in vals),'median_net_bps':statistics.median(vals)-c if vals else None,'mean_net_bps':statistics.mean(vals)-c if vals else None} for c in [10,20]},'note':'10/20bp are declared sensitivity only, not observed LBank fees/funding/fills; mean arithmetic directional return not inverse realized PnL.'}
pure=[r for r in orders if r['action']=='open'];initial=[r for r in orders if any(a['action']=='open' for a in by[r['orderID']])]
assert len(pure)==238
out={'version':'initial_entry_fullset_v1','source_pin':PIN,'source_hashes':{n:hashlib.sha256((P/n).read_bytes()).hexdigest() for n in ['order_fill_summary.csv','position_actions.csv']},'order_rows':len(orders),'action_rows':len(actions),'action_counts':dict(collections.Counter(a['action'] for a in actions)),'pure_open':stats(pure),'orders_containing_first_open_fill':stats(initial),'initial_orders_excluded_by_pure_label':sum(r['action']!='open' for r in initial),'groups':{liq:stats([r for r in initial if r['liquidity']==liq]) for liq in ['maker','taker','mixed']},'initial_order_records':[{'orderID':r['orderID'],'label':r['action'],'first':r['first_timestamp'],'last':r['last_timestamp'],'signed_return_5m_bps':r['signed_return_5m_bps'],'liquidity':r['liquidity']} for r in initial],'causal_entry_policy':False,'LBank_actual_performance':False,'TP1_first':None,'actual_emergency_5m':None,'funding_adjusted_expectancy':None,'correlated_CI':None,'full_double_cost':None,'promotion':False,'limitations':['Same selected trader retrospective public ledger, not independent forward sample.','An order can mix first open and subsequent adds; pure-open label excludes initial entries.','Source future price is Binance candle close at floor(last fill minute)+5min; whole-order VWAP known after fills. Not exactly five minutes after first fill.','Postfill direction return cannot establish TP1-first or emergency warning.','No rule-based counterfactual/unfilled orders/native quotes; no causal maker advantage.','10/20bp sensitivity is not full funding/slippage doublecost.','All source rows retained, missing paths not liquidated; dynamic overlapping inventory means orders are not independent trades.']}
(P/'report.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps({k:v for k,v in out.items() if k not in ['initial_order_records','limitations']},indent=2))
