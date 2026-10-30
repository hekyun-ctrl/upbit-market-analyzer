"""Read-only reproduction of absence diagnosis, not a PnL backtest.

Never expand 5m candles into fictitious 1m bars. Quote replay is separately
labelled and is not used to validate native strategy profitability.
"""
import json
from pathlib import Path
import re
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lbank_futures_monitor as m

raw = json.loads(Path(__file__).with_name('no-alerts-evidence-20261017.json').read_text())
counts = {}
for row in raw['diagnostics']:
    for key, value in re.findall(r'(\w+)=(\d+)', row['message']):
        counts[key] = counts.get(key, 0) + int(value)
bars = raw['native_five']
quote = bars[-1]['close']
g = m.native_entry_geometry({'quote': quote, 'five_bars': bars})
old_loss = -m.net_pnl_per_btc(1, quote, quote*.9975)
old_target = m.minimum_net_rr_target(1, quote, old_loss, m.MIN_NET_RR)
windows = []
for n in range(21, len(bars)+1):
    b = bars[:n]
    price = b[-1]['close']
    geo = m.native_entry_geometry({'quote': price, 'five_bars': b})
    width = (max(x['high'] for x in b[-12:])-min(x['low'] for x in b[-12:]))/price
    # Use the EASIER of the two sides for this necessary-only test.
    distances = []
    for direction in (1, -1):
        stop = price*(1-direction*geo['stop_floor_fraction'])
        loss = -m.net_pnl_per_btc(direction, price, stop)
        target = m.minimum_net_rr_target(direction, price, loss, m.MIN_NET_RR)
        distances.append(abs(target-price)/price)
    minimum = min(distances)
    windows.append({'start':b[-1]['start'], 'hour_range_pct':width*100,
        'minimum_tp1_pct':minimum*100,
        'passes_necessary_range_check':minimum<=1.5*width})
result = {'window_kst':raw['window_kst'], 'diagnostic_records':len(raw['diagnostics']),
    'evaluation_counts_not_distinct_trades':counts, 'native_bars':len(bars),
    'native_total_range_pct':(max(b['high'] for b in bars)-min(b['low'] for b in bars))/quote*100,
    'latest':{'quote':quote,'atr5':g['atr5'],'old_breakout_stop_floor_pct':.25,
              'new_stop_floor_pct':g['stop_floor_fraction']*100,
              'old_minimum_breakout_tp1_pct':(old_target/quote-1)*100,
              'new_minimum_tp1_pct':g['minimum_target_fraction']*100,
              'new_minimum_range_width_pct':g['min_range_fraction']*100},
    'necessary_cost_range_check':{'closed_5m_checks':len(windows),
        'passed':sum(w['passes_necessary_range_check'] for w in windows),
        'latest':windows[-1]},
    'native_minute_history_available_for_replay':False,
    'profitability_improvement_verified':False}
print(json.dumps(result,indent=2,ensure_ascii=False))
