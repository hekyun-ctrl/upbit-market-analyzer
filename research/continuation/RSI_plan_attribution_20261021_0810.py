"""Original RSI's twenty actual planning calls: numeric rejection and cost feasibility."""
import sys,json,linecache,hashlib
from pathlib import Path
from collections import Counter
import rsi_replay as replay
import lbank_futures_monitor as m
from lbank_rsi_divergence import confirmation
original=m.calculate_plan
calls=[]
def planning(prices,direction,setup=None,native_state=None):
 state=native_state;e=setup;now=state['minute_bars'][-1]['start']+60
 assert confirmation(state,e)
 assert m.native_context_gate(state,direction,'range_rebound' if e['strategy']=='rsi_divergence' else 'trend_retest')[0]
 assert direction*(state['rsi5']['rsi'][-1]-state['rsi5']['rsi'][-2])>0
 captured={}
 def tracer(frame,event,arg):
  if frame.f_code is original.__code__:
   if event=='return':
    captured['return_line']=frame.f_lineno
    captured['source_context']=[linecache.getline(frame.f_code.co_filename,n).strip() for n in range(frame.f_lineno-3,frame.f_lineno+1)]
    captured['values']={k:v for k,v in frame.f_locals.items() if k in
     ('curr','geometry','ranked','minimum_stop','stop','stop_distance','unit_loss','t1','t2','observed_range','barrier','native_target_room')}
   return tracer
  return None
 sys.settrace(tracer)
 try:p=original(prices,direction,e,native_state=state)
 finally:sys.settrace(None)
 v=captured['values'];price=state['quote'];stop=v.get('stop');barrier=e.get('barrier')
 base_loss=-m.net_pnl_per_btc(direction,price,stop) if stop is not None else None
 room=direction*(barrier-price) if barrier is not None else None
 sensitivity={}
 if base_loss and base_loss>0 and room is not None and room>0:
  for k in (1,2):
   effective_stop=stop*(1-direction*.0002*k)
   loss=-(direction*(effective_stop-price)-.0006*k*(price+effective_stop)-price*.0001*k)
   target_cash=direction*(barrier-price)-.0006*k*(price+barrier)-price*.0001*k
   sensitivity[str(k)]=dict(plan_model_net_at_barrier_per_BTC=target_cash,
     plan_model_stop_loss_per_BTC=loss,maximum_R_at_barrier=target_cash/loss)
 calls.append(dict(id=e['id'],at=now,setup=e,direction=direction,price=price,plan_returned=p is not None,
  trace=captured,barrier_price=barrier,directed_barrier_room=room,plan_cost_sensitivity=sensitivity,
  risk_criteria_changed=False,hypothetical_barrier_fill_not_verified=True))
 return p
m.calculate_plan=planning
sys.argv=[sys.argv[0],'--data','data','--output','rsi_retrace_result.json']
replay.main()
assert len(calls)==20 and not any(x['plan_returned'] for x in calls)
baseline=json.loads(Path('prior_RSI_result.json').read_text())
again=json.loads(Path('rsi_retrace_result.json').read_text())
assert baseline==again
counts=dict(Counter(str(x['trace']['return_line']) for x in calls))
out=dict(protocol='RSI_twenty_actual_plan_calls_v1',source='25388b431c9c9d38df6a434670586c65f382d604',
 calls=calls,actual_calls=len(calls),unique_setup_ids=len({x['id'] for x in calls}),return_lines=counts,
 exact_chronological_report_matches_previous=True,planning_model_only=True,new_backtest=False,
 data_bars_sha256=hashlib.sha256(Path('data/bars.zip').read_bytes()).hexdigest(),
 data_funding_sha256=hashlib.sha256(Path('data/funding.zip').read_bytes()).hexdigest(),
 cost_scope='Original planning-model taker fees+stop slippage+fixed adverse funding reserve; 2x sensitivity is not realized funding or archived execution PnL. Barrier fill optimistic and unverified.',
 native_LBank=False,independent_OOS=False,orders=0,messages=0,promotion=False)
Path('RSI_plan_result.json').write_text(json.dumps(out,separators=(',',':'))+'\n')
print(json.dumps(dict(actual_calls=len(calls),unique_ids=out['unique_setup_ids'],return_lines=counts)))
