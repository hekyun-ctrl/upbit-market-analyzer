"""Audit the shared inputs for an offline LBank BTCUSDT strategy comparison.

One-minute data comes from the recorded candle/arrival archive. Native 5m
records come from the independent exchange close audit. A five-minute bar
aggregated by the recorder is useful for checking the native bar, but never
serves as its own independent evidence.
"""

import argparse
import csv
import hashlib
import json
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path

from lbank_zb.candle_evidence import FIELDS, PERIOD, REFERENCE_ROUTES, compare


def read_archive(path):
    raw = path.read_bytes()
    data = json.loads(raw, parse_float=Decimal)
    if data['version'] != 'native_context_window_v1' or data['actual_account_data']:
        raise ValueError(f'Unsupported public candle archive: {path.name}')
    return data, hashlib.sha256(raw).hexdigest()


def sealed_minutes(archives):
    minutes = {}
    conflicts = set()
    for archive in archives:
        arrivals = {(period, start): received for period, start, received, _ in archive['arrivals']}
        for raw in archive['native_rows']:
            row = dict(zip(archive['bar_columns'], raw, strict=True))
            start = row['start']
            if row['period'] != 60 or row['quality'] != 'stream_closed':
                continue
            received = arrivals.get((60, start))
            if received is None or not start + 60 <= received <= archive['captured_at']:
                continue
            previous = minutes.setdefault(start, row)
            if previous != row:
                conflicts.add(start)
    return {start: row for start, row in minutes.items() if start not in conflicts}, conflicts


def aggregate_five(minutes, start):
    bars = [minutes.get(t) for t in range(start, start + PERIOD, 60)]
    if any(bar is None for bar in bars):
        return None
    return {'open': bars[0]['open'], 'close': bars[-1]['close'],
            'high': max(bar['high'] for bar in bars), 'low': min(bar['low'] for bar in bars),
            'volume': sum(bar['volume'] for bar in bars), 'turnover': sum(bar['turnover'] for bar in bars)}


def independent_native(row):
    ref = row['reference']
    routes = {route for route, _ in REFERENCE_ROUTES}
    if (ref is None or row['start'] != ref['start'] or ref['source'] not in routes
            or not routes.issubset(ref.get('corroborated_routes', ()))
            or ref['received_ms'] < (row['start'] + PERIOD) * 1000):
        return None
    return {**ref, **{field: Decimal(ref[field]) for field in FIELDS}}


def timestamp(value):
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError('Use an explicit UTC offset')
    return int(parsed.timestamp())


def funding_rows(path):
    with path.open(newline='', encoding='utf-8') as stream:
        records = list(csv.DictReader(stream))
    moments = [timestamp(row['scheduled_at_utc']) for row in records]
    if len(set(moments)) != len(moments) or any(row['product'] != 'BTCUSDT-PERP' for row in records):
        raise ValueError('Duplicate funding period or wrong product')
    for row in records:
        if (timestamp(row['scheduled_at_kst']) != timestamp(row['scheduled_at_utc'])
                or int(row['interval_hours']) <= 0
                or Decimal(row['rate']) != Decimal(row['rate_percent_displayed'].removesuffix('%')) / 100):
            raise ValueError('Invalid funding timestamp, interval, or rate')
        if row['settlement_mark_price']:
            mark = Decimal(row['settlement_mark_price'])
            if not mark.is_finite() or mark <= 0:
                raise ValueError('Invalid settlement mark price')
    return list(zip(moments, records, strict=True))


def audit(archives, raw_fives, funding, start, end):
    if not start < end or start % PERIOD or end % PERIOD:
        raise ValueError('Start and end must be ordered, aligned five-minute boundaries')
    minutes, conflicts = sealed_minutes(archives)
    checks = {row['start']: row for row in raw_fives}
    if len(checks) != len(raw_fives):
        raise ValueError('Ambiguous duplicate independent candle checks')
    native = {t: independent_native(row) for t, row in checks.items()}
    results = Counter()
    for t in range(start, end, PERIOD):
        if native.get(t) is None:
            results['independent_reference_unavailable'] += 1
            continue
        match, volume_ok = compare(native[t], aggregate_five(minutes, t))
        if match == 'matched' and not volume_ok:
            match = 'volume_unit_unverified'
        results[match] += 1

    # ZB's EMA history is 150 completed 15m intervals. This count concerns
    # availability for trend calculation, independent of each close's match.
    warmup_bars = 150 * 3
    warmup_starts = range(start - warmup_bars * PERIOD, start, PERIOD)
    in_window_rates = [row for t, row in funding if start <= t <= end]
    quotes = {tuple(quote) for archive in archives for quote in archive['samples'] if start <= quote[0] < end}
    return {
        'window_unix': [start, end],
        'recorded_quotes': len(quotes),
        'sealed_minute_candles': sum(t in minutes for t in range(start, end, 60)),
        'expected_minute_candles': (end - start) // 60,
        'conflicting_minutes': sorted(t for t in conflicts if start <= t < end),
        'native_five_received': sum(native.get(t) is not None for t in range(start, end, PERIOD)),
        'expected_native_five': (end - start) // PERIOD,
        'recomputed_close_checks': dict(results),
        'warmup_native_fives_in_export': sum(native.get(t) is not None for t in warmup_starts),
        'warmup_native_fives_needed': warmup_bars,
        'published_funding_rates_collected': len(funding),
        'funding_rates_in_window': len(in_window_rates),
        'settlement_prices_collected': sum(bool(row['settlement_mark_price']) for _, row in funding),
        'funding_coverage_certified': False,
        'comparison': None,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', action='append', required=True, type=Path)
    parser.add_argument('--native-five', required=True, type=Path)
    parser.add_argument('--funding-rates', required=True, type=Path)
    parser.add_argument('--start', required=True, type=timestamp)
    parser.add_argument('--end', required=True, type=timestamp)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    archives, checksums = zip(*(read_archive(path) for path in args.archive), strict=True)
    raw_fives = json.loads(args.native_five.read_bytes())
    result = audit(archives, raw_fives, funding_rows(args.funding_rates), args.start, args.end)
    result['archive_sha256'] = list(checksums)
    result['native_five_sha256'] = hashlib.sha256(args.native_five.read_bytes()).hexdigest()
    result['funding_rates_sha256'] = hashlib.sha256(args.funding_rates.read_bytes()).hexdigest()
    args.output.write_text(json.dumps(result, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')


if __name__ == '__main__':
    main()
