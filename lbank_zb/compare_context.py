"""Offline paired replay of reference annotations, never an alert sender.

Compare unchanged ZB rules with context OFF vs RECORD. Cohort outcomes describe
the original trades; they are not filtered portfolios or proof of causality.
"""
import argparse
import hashlib
import json
from dataclasses import asdict
from pathlib import Path
from .core import D, ZERO, Costs, config, digest, encode
from .context import VERSION, PERIODS, WARMUP, HISTORY
from .engine import Engine
from .store import bar_from, costs_from


class NavMetrics:
    def __init__(self):
        self.peak = D(1000)
        self.max_drawdown = ZERO
        self.max_drawdown_fraction = ZERO

    def observe(self, nav):
        self.peak = max(self.peak, nav)
        self.max_drawdown = max(self.max_drawdown, self.peak - nav)
        if self.peak > 0:
            self.max_drawdown_fraction = max(self.max_drawdown_fraction, (self.peak - nav) / self.peak)


class AvailableMark:
    """A delayed older candle cannot overwrite the latest available close."""
    def __init__(self):
        self.end = None
        self.period = None
        self.price = None

    def observe(self, bar):
        if self.end is None or bar.end > self.end or (bar.end == self.end and bar.period < self.period):
            self.end, self.period, self.price = bar.end, bar.period, bar.c


def economic_events(engine):
    # Human-readable annotations may change. Prices, decisions and events must not.
    rows = []
    for event in engine.events:
        row = {k: v for k, v in event.items() if k not in ('message', 'features')}
        rows.append(row)
    return rows


def metrics(engine, nav, days, mark):
    return {'model_net_nav': engine.nav(mark.price) - D(1000),
            'model_max_sampled_nav_drawdown': nav.max_drawdown,
            'model_max_sampled_nav_drawdown_fraction': nav.max_drawdown_fraction,
            'candidates': engine.counts.get('DETECTED', 0),
            'ready_signals': engine.counts.get('READY', 0),
            'paper_entries': engine.counts.get('PAPER_OPEN', 0),
            'closed_trades': len(engine.closed),
            'unresolved_closed_trades': sum(bool(p.health) for p in engine.closed),
            'open_at_cutoff': int(engine.position is not None),
            'calendar_days': days,
            'ready_per_calendar_day': D(engine.counts.get('READY', 0)) / days if days else None,
            'entries_per_calendar_day': D(engine.counts.get('PAPER_OPEN', 0)) / days if days else None,
            'verified_net': None, 'health': sorted(engine.health)}


def cohorts(engine):
    groups = {}
    for timeframe in ('1h', '4h', '1h_and_4h'):
        buckets = {}
        def key(signal_id):
            frames = engine.signal_contexts.get(signal_id, {}).get('frames', {})
            if timeframe == '1h_and_4h':
                return '|'.join(frames.get(t, {}).get('alignment', 'UNKNOWN') for t in ('1h', '4h'))
            return frames.get(timeframe, {}).get('alignment', 'UNKNOWN')
        def bucket(signal_id):
            label = key(signal_id)
            return buckets.setdefault(label, {'candidates': 0, 'ready_signals': 0,
                'paper_entries': 0, 'closed_trades': 0, 'unresolved_closed_trades': 0,
                'open_at_cutoff': 0, 'model_closed_net': ZERO, 'model_sum_net_r': ZERO,
                'portfolio_drawdown': None, 'verified_net': None})
        for event in engine.events:
            field = {'DETECTED': 'candidates', 'READY': 'ready_signals', 'PAPER_OPEN': 'paper_entries'}.get(event['kind'])
            if field:
                bucket(event['id'])[field] += 1
        for position in engine.closed:
            row = bucket(position.plan.id)
            row['closed_trades'] += 1
            row['unresolved_closed_trades'] += int(bool(position.health))
            row['model_closed_net'] += position.cash
            row['model_sum_net_r'] += position.cash / position.plan.risk
        if engine.position:
            bucket(engine.position.plan.id)['open_at_cutoff'] += 1
        for row in buckets.values():
            row['model_mean_net_r'] = row['model_sum_net_r'] / row['closed_trades'] if row['closed_trades'] else None
        groups[timeframe] = buckets
    return groups


def compare_rows(rows, costs, latency=60):
    """Both runs consume the same ordered Bars; no acquisition or delivery occurs."""
    engines = {mode: Engine(costs, latency, context_mode=mode) for mode in ('off', 'record')}
    nav = {mode: NavMetrics() for mode in engines}
    mark = AvailableMark()
    first_start = last_end = previous_received = None
    source_labels = set()
    bar_digests = []
    for bar in rows:
        if previous_received is not None and bar.received < previous_received:
            raise ValueError('ARRIVAL_ORDER')
        previous_received = bar.received
        first_start = bar.start if first_start is None else min(first_start, bar.start)
        last_end = bar.end if last_end is None else max(last_end, bar.end)
        source_labels.add(bar.source)
        bar_digests.append(digest(bar))
        mark.observe(bar)
        for mode, engine in engines.items():
            engine.ingest(bar, bar.received)
            nav[mode].observe(engine.nav(mark.price))
    days = ((last_end - 1 + 9 * 3600) // 86400 - (first_start + 9 * 3600) // 86400 + 1) if first_start is not None else 0
    results = {mode: metrics(engine, nav[mode], days, mark) for mode, engine in engines.items()}
    a, b = results['off'], results['record']
    fields = ('model_net_nav', 'model_max_sampled_nav_drawdown',
              'model_max_sampled_nav_drawdown_fraction', 'candidates', 'ready_signals',
              'paper_entries', 'closed_trades', 'open_at_cutoff', 'ready_per_calendar_day')
    delta = {key: b[key] - a[key] if a[key] is not None and b[key] is not None else None for key in fields}
    hashes = {mode: digest(economic_events(engine)) for mode, engine in engines.items()}
    unchanged = hashes['off'] == hashes['record'] and all(v in (ZERO, None) for v in delta.values())
    parameters = {'cost_scenario': asdict(costs), 'latency_seconds': latency,
                  'strategies': sorted(engines['record'].strategies), 'config': config(),
                  'context': {'version': VERSION, 'period_seconds': PERIODS,
                              'warmup_bars': WARMUP, 'history_bars': HISTORY,
                              'pivot_lookback_bars': 120, 'pivot_left_right_bars': 2,
                              'completion_grace_seconds': 15}}
    code_hashes = {name: hashlib.sha256(Path(__file__).with_name(name).read_bytes()).hexdigest()
                   for name in ('core.py', 'engine.py', 'context.py', 'compare_context.py')}
    return {'context_version': VERSION, 'comparison': 'SAME_ZB_RULES_OFF_VS_REFERENCE_RECORD',
            'parameters': parameters, 'parameter_hash': digest(parameters),
            'code_SHA256': code_hashes,
            'economic_events_identical': unchanged, 'economic_event_hashes': hashes,
            'results': results, 'delta_record_minus_off': delta,
            'cohorts': cohorts(engines['record']),
            'signal_contexts': engines['record'].signal_contexts,
            'input_event_hash': digest(bar_digests), 'source_labels': sorted(source_labels),
            'cutoff_mark': {'bar_end': mark.end, 'period_seconds': mark.period, 'price': mark.price},
            'interval': {'start': first_start, 'end_exclusive': last_end,
                         'calendar_days': days, 'includes_supplied_warmup': True},
            'strategy_changed': False, 'performance_claim': False, 'verified_net': None,
            'limitations': [
                'Reference annotations cannot change entries, exits, size, stops or targets.',
                'Cohorts describe existing trades; do not sum them into hypothetical filtered portfolios.',
                'Cohort portfolio drawdown is undefined; a changed policy requires a separately registered full replay.',
                'NAV drawdown is sampled after received bar events, not tick/intrabar maximum drawdown.',
                'Fees/slippage follow the supplied scenario; actual funding coverage remains unintegrated.',
                'This compares context off/on within ZB, not the old production strategy against ZB.',
                'Unknown context and unresolved trades stay in the report. Zero trades do not establish performance.',
                'The observation span includes supplied warmup and no-trade days; absent data are not manufactured.'
            ]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', required=True)
    parser.add_argument('--output', required=True)
    parser.add_argument('--scenario')
    parser.add_argument('--latency', type=int, choices=[60, 120], default=60)
    args = parser.parse_args()
    raw = [json.loads(line) for line in Path(args.input).read_text().splitlines() if line.strip()]
    if any(a['received'] > b['received'] for a, b in zip(raw, raw[1:])):
        raise ValueError('ARRIVAL_ORDER')
    raw.sort(key=lambda row: (row['received'], row['start'] + row['period'], row['period']))
    costs = costs_from(json.loads(Path(args.scenario).read_text())) if args.scenario else Costs(None, None, D(2), None, None, None, None)
    report = compare_rows([bar_from(row) for row in raw], costs, args.latency)
    Path(args.output).write_text(json.dumps(report, default=encode, indent=2, ensure_ascii=False) + '\n')
    if not report['economic_events_identical']:
        raise SystemExit('REFERENCE_CONTEXT_CHANGED_ECONOMIC_EVENTS')


if __name__ == '__main__':
    main()
