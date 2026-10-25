"""One predeclared paired location experiment; immutable original inputs, no sends."""
import hashlib
import json
import logging
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research import compare_entry_refinement as replay

OUT = ROOT / 'research/zerobase/entry_location_20261025'


def main():
    logging.disable(logging.CRITICAL)
    bundles, bars = replay.load_archives(replay.DATA/'archive_chunks.json')
    points = json.loads((replay.DATA/'recorded_quotes.json').read_text())
    costs = bundles[0]['model_config']
    outcomes = replay.load_outcome_bars(replay.DATA/'outcome_candle_logs.jsonl')
    attrs = {replay.CONFIG_MAP[k]:v for k,v in costs.items()}
    attrs.update(COOLDOWN=2700, REVERSAL_COOLDOWN=5400, MAX_DAILY_ALERTS=8,
                 is_quiet_new_entry_time=lambda _:False)
    result = dict(baseline_commit='b4c7d94efb305c19cbe44f050c806440e19351a0',
        model_config=costs, quote_observations=len(points),
        sends=0, actual_fills=False, profitability_validated=False,
        limitations=['Retained signal-adjacent windows, not all market hours.',
                     'Nominal closed-bar availability assumptions, not verified receipt times.',
                     'Fixed half/half exit model; not complete live trailing execution.',
                     'Same-candle ambiguity and missing future bars remain unresolved.',
                     'No claim that more candidates imply better returns.'],
        input_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
                      for p in [replay.DATA/'archive_chunks.json',replay.DATA/'recorded_quotes.json',
                                replay.DATA/'outcome_candle_logs.jsonl',OUT/'PREDECLARED.md']},
        experiments={})
    compact = replay.compact_plan
    def extended(plan):
        return dict(compact(plan), entry_location=plan.get('entry_location'),
                    location_evidence=plan.get('location_evidence'))
    with tempfile.TemporaryDirectory(prefix='location-replay-') as directory:
        feed = replay.make_feed(directory,bars)
        with patch.multiple(replay.monitor,**attrs), patch.object(replay.monitor,'telegram',
                side_effect=AssertionError('offline sends forbidden')), patch.object(replay,'compact_plan',extended):
            for delay in (0,15):
                for mode in ('capture','location'):
                    with patch.object(replay.monitor,'ENTRY_REFINEMENT_MODE',mode):
                        windows={}
                        for window in sorted({p['window'] for p in points}):
                            selected=sorted((p for p in points if p['window']==window),key=lambda p:p['logged_at'])
                            row=replay.replay_window(selected,feed,delay)
                            for signal in row['signals']:
                                signal['benchmark']=replay.outcome(signal,outcomes)
                            windows[window]=row
                        key=f'{mode}_delay{delay}'
                        result['experiments'][key]=dict(windows=windows,aggregate=replay.aggregate(windows))
                        print(key,json.dumps(result['experiments'][key]['aggregate'],ensure_ascii=False),flush=True)
    (OUT/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2,allow_nan=False)+'\n')


if __name__ == '__main__':
    main()
