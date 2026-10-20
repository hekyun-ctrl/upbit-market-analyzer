"""Observe frozen entry evidence with no fitted pressure classifier."""
import json
from pathlib import Path
import attribution as a
from lbank_resistance_pressure import resistance_pressure
rows=[]
for en in a.saved['entries']:
 t=en['at'];p=en['plan'];state=a.prior.native_at(a.bars,a.five,a.lookup[t],{});state['quote']=p['price']
 x=resistance_pressure(state,p['direction'],t)
 assert x['ready'] and not x['live_gate'] and not x['entry_override'] and x['strength_comparison'] is None
 rows.append(dict(reference=en['reference'],original_target_room=p['native_target_room'],observed=x))
Path('pressure_fixed22.json').write_text(json.dumps(dict(rows=rows,not_a_profitability_test=True,weights_fitted=False,source_commit='25388b431c9c9d38df6a434670586c65f382d604'),separators=(',',':'))+'\n')
print('22/22 source-ready descriptive observations, no prediction or entry permission')
