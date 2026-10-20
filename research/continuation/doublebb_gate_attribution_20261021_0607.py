"""Exact six-event gate attribution, no new entries or policy changes."""
import sys,json,inspect,linecache,hashlib
from pathlib import Path
from collections import Counter
import lbank_public_techniques as pt
import baseline_replay as replay
import lbank_futures_monitor as m
EXPECTED={
'public_techniques_v1:double_bb_5m:1:1736300400',
'public_techniques_v1:double_bb_5m:-1:1736341200',
'public_techniques_v1:double_bb_5m:1:1736442900',
'public_techniques_v1:double_bb_5m:-1:1736468400',
'public_techniques_v1:double_bb_5m:-1:1736606100',
'public_techniques_v1:double_bb_5m:-1:1736812500'}
records=[]
def traced_plan(state,e):
 captured={}
 def trace(frame,event,arg):
  if frame.f_code is m.calculate_plan.__code__:
   if event=='return':
    captured['return_line']=frame.f_lineno
    captured['return_source']=linecache.getline(frame.f_code.co_filename,frame.f_lineno).strip()
    captured['previous_lines']=[linecache.getline(frame.f_code.co_filename,n).strip() for n in range(frame.f_lineno-3,frame.f_lineno)]
    captured['values']={k:v for k,v in frame.f_locals.items() if k in (
      'curr','geometry','ranked','minimum_stop','stop','stop_distance','unit_loss','t1','t2',
      'observed_range','barrier','native_target_room','raw_qty','margin','qty','net_loss','net_gain1')}
   return trace
  return None
 sys.settrace(trace)
 try:p=m.calculate_plan(state['prices'],e['direction'],e,native_state=state)
 finally:sys.settrace(None)
 captured['plan_returned']=p is not None
 return p,captured
class Attribution(pt.TechniqueStudy):
 def step(self,now,price,state,calculate_plan,context_gate,*,baseline_reason):
  ctx=pt.analyze(state,now)
  for e in ctx['setups']:
   if not e['strategy'].startswith('double_bb'):continue
   assert e['id'] in EXPECTED
   assert all(b['start']+60<=now for b in state['minute_bars'])
   d=e['direction'];r=state['rsi5']['rsi'];delta=r[-1]-r[-2]
   cg=context_gate(state,d,'trend_retest');dev=abs(price/state['prices'][-1]-1)
   p,trace=traced_plan(state,e)
   selected=('price_deviation' if dev>.001 else 'context' if not cg[0] else
             'rsi_direction' if d*delta<=0 else 'risk_or_target' if p is None else 'qualified')
   records.append(dict(id=e['id'],at=now,available_at=e['available_at'],expires_at=e['expires_at'],
    direction=d,price=price,event=e,closed1m_reaction=pt.confirmation(state,e,now),
    price_deviation=dev,context=list(cg),rsi_previous=r[-2],rsi_current=r[-1],
    signed_rsi_delta=d*delta,selected_timing_bypass_reason=selected,
    plan_diagnostic=trace,diagnostic_plan=p,
    diagnostic_plan_not_an_accepted_entry=True))
  return dict(ready=ctx['ready'],decisions=[],hourly_ready=ctx.get('hourly_ready'))
replay.TechniqueStudy=Attribution
sys.argv=[sys.argv[0],'--data','data','--output','unused_empty_book.json']
replay.main()
assert {r['id'] for r in records}==EXPECTED
assert not any(r['selected_timing_bypass_reason']=='qualified' for r in records)
events={}
for id in sorted(EXPECTED):
 rs=[r for r in records if r['id']==id]
 events[id]=dict(observations=len(rs),first=rs[0]['at'],last=rs[-1]['at'],
  selected_reasons=dict(Counter(r['selected_timing_bypass_reason'] for r in rs)),
  context_reasons=dict(Counter(r['context'][1] for r in rs)),
  plan_return_lines=dict(Counter(str(r['plan_diagnostic']['return_line']) for r in rs)),
  rsi_signed_min=min(r['signed_rsi_delta'] for r in rs),
  rsi_signed_max=max(r['signed_rsi_delta'] for r in rs))
sources={p:hashlib.sha256(Path(p).read_bytes()).hexdigest() for p in
 ['lbank_futures_monitor.py','lbank_public_techniques.py','lbank_native_entry.py',
  'lbank_sampled_context.py','baseline_replay.py','attribution.py']}
out=dict(protocol='doubleBB_six_event_gate_attribution_v1',source_commit='25388b431c9c9d38df6a434670586c65f382d604',
 data_bars_SHA256=hashlib.sha256(Path('data/bars.zip').read_bytes()).hexdigest(),
 data_funding_SHA256=hashlib.sha256(Path('data/funding.zip').read_bytes()).hexdigest(),
 source_SHA256=sources,events=events,observations=records,
 orders=0,messages=0,operations_changed=False,new_profitability_backtest=False,
 native_LBank=False,new_independent_OOS=False,
 original_trader_replica=False,promotion=False,
 warning_rate=None,manual_fills=None,expectancy=None,PF=None,
 reason='Diagnostic of the six previously frozen events. All-gate plan calls expose counterfactual rejecting branch only; never an entry.')
Path('attribution_result.json').write_text(json.dumps(out,separators=(',',':'))+'\n')
print(json.dumps(events))
