"""Causal completed-5m context replay ONLY. Not a 1m entry or PnL backtest."""
import json
from collections import Counter
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from lbank_rsi_divergence import analyze
from lbank_native_entry import SOURCE

bars = json.loads(Path(__file__).with_name('rsi-divergence-native-20261018.json').read_text())['bars']
events, reasons, candidates = {}, Counter(), {}
ready = 0
for n in range(1,len(bars)+1):
    state = {'source':SOURCE,'five_bars':bars[max(0,n-250):n]}
    result = analyze(state)
    if not result['ready']:
        reasons[result['reason']] += 1
        continue
    ready += 1
    for event in result['divergences']:
        events.setdefault(event['id'],dict(event,first_seen_at=bars[n-1]['start']+300))
    for setup in result['setups']:
        candidates.setdefault(setup['id'],dict(setup,first_seen_at=bars[n-1]['start']+300))
result = {'bars':len(bars),'ready_evaluations':ready,'unavailable':dict(reasons),
          'unique_divergence_contexts':len(events),
          'unique_candidate_contexts_by_strategy':dict(Counter(s['strategy'] for s in candidates.values())),
          'events':list(events.values()),'candidate_contexts':list(candidates.values()),
          'native_1m_replay_available':False,'confirmed_entries':None,'profitability_verified':False,
          'existing_live_entry_veto_added':False,
          'notes':'Counts are contexts, not tradable or profitable opportunities. No future bars, no fabricated 1m candles.'}
print(json.dumps(result,indent=2,ensure_ascii=False))
