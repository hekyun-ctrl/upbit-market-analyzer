"""Known-at-entry attribution; descriptive fixed22, no new trading rule."""
import json,copy,hashlib
from pathlib import Path
from collections import Counter
import intrabar,prior
from lbank_entry_priority import ranked_entry_evidence
from lbank_entry_quality import observed_target_room,confirmed_pivot_barriers
from lbank_trend_runner import valid_state
root=Path(__file__).parent
raw=intrabar.archive('bars','b69ca5b2c2d1ae00d68d2790d09138cb2a44085ff19e0da5dc98b569ea7e489e')
bars=[dict(start=int(r['open_time'])//1000,quality='stream_closed',**{k:float(r[k]) for k in ('open','high','low','close','volume')}) for r in raw]
five=[]
for i in range(0,len(bars),5):
 g=bars[i:i+5];five.append(dict(start=g[0]['start'],quality='stream_closed',open=g[0]['open'],high=max(b['high'] for b in g),low=min(b['low'] for b in g),close=g[-1]['close'],volume=sum(b['volume'] for b in g)))
saved=json.loads((root/'prior_report.json').read_text());lookup={b['start']:i for i,b in enumerate(bars)}
rows=[];matches=0;prefix_checks=0
for entry in saved['entries']:
 t=entry['at'];p=entry['plan'];i=lookup[t];state=prior.native_at(bars,five,i,{})
 assert valid_state(state,t)
 evidence=ranked_entry_evidence(state,p['direction'],p['price'],p['target1'],t)
 original=p['entry_decision_priority']
 assert evidence['ready']
 # Original stored override list belongs to caller, recomputed pure function starts empty.
 for key in ('higher_direction_intact','location_ok','repeated_barrier','may_override_single_pivots'):
  assert evidence[key]==original[key],(entry['reference'],key)
 for key in ('atr5','ema9','extension_atr','target_distance_atr'):
  assert abs(evidence[key]-original[key])<1e-8,(entry['reference'],key,evidence[key],original[key])
 matches+=1
 room=observed_target_room(state['five_bars'],p['direction'],p['price'],p['target1'])
 assert room==p['native_target_room']
 # Mutate forming/future bars, rebuild only prefix state, compare actual evidence.
 changed=copy.deepcopy(bars)
 for b in changed[i:i+10]:b.update(high=b['high']*2,low=b['low']/2,close=b['close']*1.2)
 altered=prior.native_at(changed,five,i,{})
 assert ranked_entry_evidence(altered,p['direction'],p['price'],p['target1'],t)==evidence
 prefix_checks+=1
 d=p['direction'];last=state['five_bars'][-1]['close']
 pivots=confirmed_pivot_barriers(state['five_bars'],d,0 if d==1 else 1e100)
 # Crossed by quote, not yet crossed by latest completed5m close; diagnostic only.
 crossed=[b for b in pivots if d*(p['price']-b['price'])>0 and d*(last-b['price'])<=0]
 rows.append(dict(reference=entry['reference'],at=t,strategy=p['strategy'],direction=d,price=p['price'],stop=p['stop'],target1=p['target1'],room=room,ranked=evidence,original_overrides=original.get('overrides'),original_confirmation=p.get('entry_quality'),last_completed5_close=last,crossed_quote_unconfirmed_close_pivots=crossed,entry_price_minus_ema9_ATR=evidence['extension_atr']))
summary=dict(n=len(rows),room=dict(Counter(r['room']['target_room'] for r in rows)),higher=sum(r['ranked']['higher_direction_intact'] for r in rows),swing=sum(r['ranked']['upper_evidence']['swing_structure'] for r in rows),location_ok=sum(r['ranked']['location_ok'] for r in rows),quote_crossed_not_completed5=sum(bool(r['crossed_quote_unconfirmed_close_pivots']) for r in rows),confirmation_trigger_override=sum(bool((r['original_confirmation'] or {}).get('breakout_confirmation',{}).get('priority_override_used')) for r in rows),strategies=dict(Counter(r['strategy'] for r in rows)))
out=dict(source_commit=intrabar.SOURCE,archive_sha256='b69ca5b2c2d1ae00d68d2790d09138cb2a44085ff19e0da5dc98b569ea7e489e',matched_original_entries=matches,prefix_mutation_checks=prefix_checks,summary=summary,rows=rows,performance_test=False,promotion=False,production_changed=False,native_LBank=False,already_seen_development=True,previous_intrabar_report_invalid_due_to_missing_quality=True)
(root/'attribution_result.json').write_text(json.dumps(out,separators=(',',':'))+'\n');print(json.dumps(summary))
