"""Reproduce a native-data audit. This is NOT a strategy/PnL backtest."""
import json
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from lbank_trend_runner import atr_wilder
from lbank_native_entry import wilder_rsi_series

path = Path(__file__).with_name('native-5m-audit-20261017.json')
bars = json.loads(path.read_text())['bars']
assert bars and all(b['start'] % 300 == 0 and b['quality'] == 'stream_closed'
                    and b['low'] <= min(b['open'], b['close'])
                    and b['high'] >= max(b['open'], b['close'])
                    and b['volume'] >= 0 for b in bars)
gaps = sum(b['start']-a['start'] != 300 for a,b in zip(bars,bars[1:]))
result = dict(bars=len(bars), gaps=gaps, first_start=bars[0]['start'],
              last_start=bars[-1]['start'], span_hours=(bars[-1]['start']-bars[0]['start']+300)/3600,
              total_high=max(b['high'] for b in bars), total_low=min(b['low'] for b in bars),
              atr14=atr_wilder(bars), rsi14=wilder_rsi_series([b['close'] for b in bars])[-1],
              chart_parity_verified=False, trading_profit_verified=False,
              note='Only data geometry/continuity audited; no minute history or fills reconstructed.')
print(json.dumps(result, indent=2))
