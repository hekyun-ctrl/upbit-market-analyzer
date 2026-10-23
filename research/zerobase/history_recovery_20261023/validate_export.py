"""Reproduce offline historical bars from captured public-candle log records.

Never imports into the live engine or promotes derived bars to independent bars.
"""
import csv
import hashlib
import json
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parent
PREFIX = 'INFO:lbank_native_candles:NATIVE_CANDLE_CLOSED '
FIELDS = ('open', 'high', 'low', 'close', 'volume', 'turnover')


def iso(seconds):
    return datetime.fromtimestamp(seconds, timezone.utc).isoformat()


def audit(rows, period):
    starts = sorted(r['start'] for r in rows)
    gaps, longest, run = [], 0, 0
    for i, start in enumerate(starts):
        if i and start != starts[i-1] + period:
            gaps.append({'start': iso(starts[i-1] + period), 'end_exclusive': iso(start),
                         'missing_bars': (start - starts[i-1]) // period - 1})
            run = 0
        run += 1
        longest = max(longest, run)
    return {'bars': len(starts), 'first_start_utc': iso(starts[0]) if starts else None,
            'last_end_utc': iso(starts[-1] + period) if starts else None,
            'longest_contiguous_bars': longest, 'trailing_contiguous_bars': run,
            'missing_bars_inside_span': sum(g['missing_bars'] for g in gaps), 'gaps': gaps}


def export(name, rows, period):
    path = ROOT / name
    columns = ['start_utc', 'end_utc', 'open', 'high', 'low', 'close',
               'volume_native_unverified', 'turnover_native_unverified', 'observed_log_at']
    with path.open('w', newline='') as f:
        writer = csv.writer(f, lineterminator='\n')
        writer.writerow(columns)
        for r in rows:
            writer.writerow([iso(r['start']), iso(r['start'] + period),
                             *(str(r[k]) for k in FIELDS), r['logged_at']])
    return {'file': name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest(),
            **audit(rows, period)}


def main():
    raw = ROOT / 'candle_logs.jsonl'
    rows, duplicate = {}, 0
    for line in raw.read_text().splitlines():
        entry = json.loads(line)
        if not entry['message'].startswith(PREFIX):
            raise ValueError('UNEXPECTED_LOG_TYPE')
        r = json.loads(entry['message'][len(PREFIX):], parse_float=Decimal)
        start = r['start']
        if type(start) is not int or start % 300 or r['period'] != 300 or r['quality'] != 'stream_closed':
            raise ValueError('WRONG_BAR_KIND')
        logged = datetime.fromisoformat(entry['logged_at'].replace('Z', '+00:00')).timestamp()
        if logged < start + 300 or not start <= r['updated'] < start + 300:
            raise ValueError('UNCONFIRMED_OR_INVALID_TIMESTAMP')
        for field in FIELDS:
            r[field] = Decimal(str(r[field]))
            if not r[field].is_finite():
                raise ValueError('NONFINITE_VALUE')
        if (min(r[k] for k in FIELDS[:4]) <= 0 or r['low'] > min(r['open'], r['close'])
                or r['high'] < max(r['open'], r['close']) or min(r['volume'], r['turnover']) < 0):
            raise ValueError('INVALID_OHLCV')
        r['logged_at'] = entry['logged_at']
        if start in rows:
            if any(r[k] != rows[start][k] for k in FIELDS):
                raise ValueError('CONFLICTING_DUPLICATE')
            duplicate += 1
            if logged >= datetime.fromisoformat(rows[start]['logged_at'].replace('Z', '+00:00')).timestamp():
                continue
        rows[start] = r
    ordered = [rows[k] for k in sorted(rows)]
    outputs = [export('BTCUSDT_5m_derived.csv', ordered, 300)]
    for period, label in [(900, '15m'), (3600, '1h'), (14400, '4h')]:
        derived = []
        for start in sorted({r['start'] // period * period for r in ordered}):
            members = [rows.get(t) for t in range(start, start + period, 300)]
            if any(r is None for r in members):
                continue
            derived.append({'start': start, 'open': members[0]['open'], 'close': members[-1]['close'],
                            'high': max(r['high'] for r in members), 'low': min(r['low'] for r in members),
                            'volume': sum(r['volume'] for r in members), 'turnover': sum(r['turnover'] for r in members),
                            'logged_at': max(r['logged_at'] for r in members)})
        outputs.append(export(f'BTCUSDT_{label}_derived.csv', derived, period))
    manifest = {'product': 'LBank BTCUSDT USDT linear perpetual', 'price_basis': 'LAST',
                'source': 'existing native WebSocket collector; recovered NATIVE_CANDLE_CLOSED Railway logs',
                'five_minute_origin': 'aggregation of five continuous stream_closed native 1m bars',
                'raw_1m_bars_in_this_export': 0, 'independent_exchange_5m_bars': 0,
                'volume_unit_verified': False, 'live_engine_imported': False,
                'profitability_validated': False, 'gap_fill_applied': False,
                'logged_at_semantics': 'original log observation; not a fabricated historical receipt time',
                'duplicates_identical': duplicate, 'conflicting_duplicates': 0,
                'raw_log_sha256': hashlib.sha256(raw.read_bytes()).hexdigest(), 'outputs': outputs}
    (ROOT / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(json.dumps({x['file']: {k: v for k, v in x.items() if k not in ('gaps', 'sha256', 'file')}
                      for x in outputs}, indent=2))


if __name__ == '__main__':
    main()
