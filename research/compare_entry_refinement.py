"""Paired OFFLINE replay of retained public candles/quotes, never a sender.

Run from the repository root: python research/compare_entry_refinement.py
See PREDECLARED.md for frozen hypotheses and sampling/availability limitations.
"""
import base64
import copy
import hashlib
import json
import logging
import math
import sqlite3
import sys
import tempfile
import zlib
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from statistics import median
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lbank_futures_monitor as monitor
from lbank_entry_refinement import MODES
from lbank_native_entry import NativeEntryFeed

DATA = ROOT / 'research/zerobase/entry_comparison_20261024'
CONFIG_MAP = dict(fee='TAKER_FEE', funding_buffer='FUNDING_BUFFER',
    leverage='LEVERAGE', max_margin_fraction='MAX_MARGIN_FRACTION',
    reference_balance='REF_BALANCE', risk_fraction='RISK_FRACTION', slippage='STOP_SLIPPAGE')


def timestamp(value):
    return datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


def iso(value):
    return datetime.fromtimestamp(value, timezone.utc).isoformat()


def load_archives(path):
    groups = {}
    for row in json.loads(path.read_text()):
        part = row['archive']
        key = part['sha256']
        group = groups.setdefault(key, {})
        if part['index'] in group and group[part['index']] != part:
            raise ValueError('conflicting archive chunk')
        group[part['index']] = part
    bundles, bars = [], {}
    for digest, group in sorted(groups.items()):
        count = next(iter(group.values()))['chunks']
        if set(group) != set(range(count)) or any(p['chunks'] != count for p in group.values()):
            raise ValueError('incomplete archive')
        raw = zlib.decompress(base64.b64decode(''.join(group[i]['payload'] for i in range(count))))
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError('archive checksum mismatch')
        bundle = json.loads(raw)
        bundles.append(bundle)
        for values in bundle['native_rows']:
            bar = dict(zip(bundle['bar_columns'], values))
            key = (bar['period'], bar['start'])
            if key in bars and bars[key] != bar:
                raise ValueError('conflicting candle revisions')
            bars[key] = bar
    if len({json.dumps(b['model_config'], sort_keys=True) for b in bundles}) != 1:
        raise ValueError('inconsistent cost/reference configuration')
    return bundles, bars


def make_feed(directory, bars):
    path = str(Path(directory) / 'native.sqlite3')
    db = sqlite3.connect(path)
    try:
        db.execute('CREATE TABLE native_bars(period INTEGER,start INTEGER,open REAL,high REAL,'
                   'low REAL,close REAL,volume REAL,turnover REAL,updated INTEGER,quality TEXT,'
                   'PRIMARY KEY(period,start))')
        columns = 'period,start,open,high,low,close,volume,turnover,updated,quality'.split(',')
        db.executemany('INSERT INTO native_bars VALUES(?,?,?,?,?,?,?,?,?,?)',
                       [[bar[k] for k in columns] for bar in bars.values()])
        db.commit()
    finally:
        db.close()
    return NativeEntryFeed(path)


def available_state(feed, ts, price, nominal_delay):
    # A sensitivity assumption, NOT fabricated historical publication evidence.
    # Do not silently use a stale bar in place of the production-required latest.
    if any(ts % period < nominal_delay for period in (60, 300)):
        return None, 'nominal_publication_delay'
    state, diag = feed.snapshot(ts, price)
    if state:
        assert all(b['start']+60 <= ts for b in state['minute_bars'])
        assert all(b['start']+300 <= ts for b in state['five_bars'])
    return state, diag['reason']


def compact_plan(plan):
    keys = ('strategy', 'direction', 'price', 'stop', 'target1', 'target2', 'quantity_btc',
            'net_loss', 'net_gain1', 'net_gain2', 'margin', 'rr1', 'stop_distance_pct',
            'native_entry_geometry', 'structure_anchor')
    result = {k: plan.get(k) for k in keys}
    result['target1_distance_pct'] = abs(plan['target1']/plan['price']-1)*100
    assert plan['net_loss'] <= monitor.REF_BALANCE*monitor.RISK_FRACTION+1e-7
    assert plan['margin'] <= monitor.REF_BALANCE*monitor.MAX_MARGIN_FRACTION+1e-7
    assert plan['rr1'] >= monitor.MIN_NET_RR-1e-7
    assert plan['stop_distance_pct'] <= .6+1e-7
    return result


def replay_window(points, feed, nominal_delay):
    evaluations, blocks, confirm_reasons, gate_reasons = Counter(), Counter(), Counter(), Counter()
    initial_bars, raw_bars, opportunities, signals = set(), set(), [], []
    alert = dict(day=None, count=0, last_time=None, last_side=None)
    watch = None
    ready = watched = expired = 0
    for point in points:
        ts, price = timestamp(point['logged_at']), point['price']
        state, reason = available_state(feed, ts, price, nominal_delay)
        if state is None:
            blocks[reason] += 1
            watch = None
            continue
        ready += 1
        diag = {}
        proposal = monitor.classify(state['prices'], native_state=state, diagnostics=diag)
        evaluations[diag['reason']] += 1
        for item in diag.get('candidates', []):
            raw_bars.add((state['minute_bars'][-1]['start'], item['strategy'], item['direction']))
        if proposal:
            initial_bars.add((state['minute_bars'][-1]['start'], proposal['strategy'], proposal['direction']))
            opportunities.append(dict(at=iso(ts), **compact_plan(proposal)))
        # Sampling is limited to the retained quote logs. No invented 15s ticks.
        if watch:
            age = ts-watch['time']
            if age > monitor.ENTRY_CONFIRM_MAX_AGE_SECONDS:
                expired += 1
                watch = None
            elif age >= 30:
                diagnostic = {}
                plan = monitor.confirm_watch(state['prices'], watch['plan'], age,
                    native_state=state, diagnostics=diagnostic)
                confirm_reasons[diagnostic.get('reason', 'unknown')] += 1
                if plan:
                    gate = monitor.alert_gate(ts, plan['side'], alert, plan=plan)
                    gate_reasons['confirm:'+gate] += 1
                    if gate == 'ok':
                        signals.append(dict(at=iso(ts), watch_at=iso(watch['time']), **compact_plan(plan)))
                        monitor.record_alert(ts, plan['side'], alert, plan=plan)
                    watch = None
                elif diagnostic.get('reason') == monitor.BREAKOUT_CONFIRMATION_REJECT:
                    watch = None
        if watch is None and proposal:
            gate = monitor.alert_gate(ts, proposal['side'], alert, plan=proposal)
            gate_reasons['discovery:'+gate] += 1
            if gate == 'ok':
                watched += 1
                watch = dict(plan=copy.deepcopy(proposal), time=ts)
    distances = [x['target1_distance_pct'] for x in opportunities]
    return dict(quote_observations=len(points), ready_observations=ready, unavailable=dict(blocks),
        raw_candidate_bar_families=len(raw_bars), qualified_candidate_bars=len(initial_bars),
        evaluations=dict(evaluations), watches=watched, expired=expired,
        pending_at_end=int(watch is not None), confirmation_reasons=dict(confirm_reasons),
        cooldown_reasons=dict(gate_reasons), modeled_alerts=len(signals),
        median_qualified_tp1_distance_pct=median(distances) if distances else None,
        opportunities=opportunities, signals=signals,
        initial_cooldown_assumption='empty; prior unobserved alerts unknown')


def load_outcome_bars(path):
    bars = {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        bar = json.loads(row['message'].split('NATIVE_CANDLE_CLOSED ', 1)[1])
        if bar['period'] != 300:
            raise ValueError('outcome source must be recorded 5m')
        if bar['start'] in bars and bars[bar['start']] != bar:
            raise ValueError('conflicting outcome candle')
        bars[bar['start']] = bar
    return bars


def outcome(plan, bars):
    """Fixed half/half benchmark. Branch intrabar uncertainty; never impute gaps."""
    ts = timestamp(plan['at'])
    first = int(ts//300)*300
    deadline = int((ts+21600)//300)*300
    direction, entry, stop = plan['direction'], plan['price'], plan['stop']
    targets, qty = (plan['target1'], plan['target2']), plan['quantity_btc']

    def value(price, fraction, stop_like=False):
        return qty*fraction*monitor.net_pnl_per_btc(direction, entry, price,
            exit_kind='stop' if stop_like else 'take_profit')

    def touches(bar, price, upward):
        return bar['high'] >= price if upward else bar['low'] <= price

    # (remaining fraction, net PnL, exit candle end); closed branches persist.
    paths = [(1., 0., None)]
    for start in range(first, deadline, 300):
        b = bars.get(start)
        if (not b or b['quality'] != 'stream_closed' or b['start'] != start
                or not all(math.isfinite(b[k]) and b[k] > 0 for k in ('open','high','low','close'))
                or b['low'] > min(b['open'], b['close']) or b['high'] < max(b['open'], b['close'])):
            return dict(status='unresolved', reason='missing_or_invalid_outcome_bar', at=iso(start))
        hit_stop = touches(b, stop, direction == -1)
        if start == first and ts > first:
            if hit_stop or touches(b, targets[0], direction == 1):
                return dict(status='unresolved', reason='entry_bar_before_after_unknown', at=iso(start))
            continue
        next_paths = []
        for remaining, net, exit_at in paths:
            if remaining == 0:
                next_paths.append((remaining, net, exit_at))
                continue
            target = targets[0] if remaining == 1 else targets[1]
            hit_target = touches(b, target, direction == 1)
            # An opening gap beyond an active trigger determines which was first.
            opens_stop = direction*(b['open']-stop) <= 0
            opens_target = direction*(b['open']-target) >= 0
            if opens_stop:
                next_paths.append((0., net+value(b['open'], remaining, True), start+300))
                continue
            if hit_stop and not opens_target:
                next_paths.append((0., net+value(stop, remaining, True), start+300))
            if hit_target:
                after_net, after_remaining = net, remaining
                if remaining == 1:
                    after_net += value(targets[0], .5)
                    after_remaining = .5
                hit_second = touches(b, targets[1], direction == 1)
                opens_second = direction*(b['open']-targets[1]) >= 0
                # If TP1 was already open-triggered, both TP2/stop orders remain
                # possible. Otherwise the stop-first branch above supplies the
                # lower bound, and reaching TP2 supplies the upper bound.
                if hit_second:
                    next_paths.append((0., after_net+value(targets[1], .5), start+300))
                if hit_stop and not opens_second and (not hit_second or opens_target):
                    next_paths.append((0., after_net+value(stop, after_remaining, True), start+300))
                elif not hit_second:
                    next_paths.append((after_remaining, after_net, None))
            elif not hit_stop:
                next_paths.append((remaining, net, None))
        paths = list(set(next_paths))
        if all(p[0] == 0 for p in paths):
            break
        if start+300 == deadline:
            paths = [(0., n+value(b['close'], r, True), deadline) if r else (r,n,e)
                     for r,n,e in paths]
    if not paths or any(p[0] for p in paths):
        return dict(status='unresolved', reason='incomplete_horizon')
    low, high = min(p[1] for p in paths), max(p[1] for p in paths)
    return dict(status='exact_in_model' if abs(low-high) < 1e-8 else 'intrabar_range',
                net_usdt_low=low, net_usdt_high=high,
                latest_exit_bar_end=iso(max(p[2] for p in paths)))


def aggregate(windows):
    signals = [s for window in windows.values() for s in window['signals']]
    outcomes = [s['benchmark'] for s in signals]
    known = [o for o in outcomes if o['status'] != 'unresolved']
    result = {k: sum(w[k] for w in windows.values()) for k in
              ('ready_observations','raw_candidate_bar_families','qualified_candidate_bars','modeled_alerts')}
    result.update(outcome_status=dict(Counter(o['status'] for o in outcomes)),
                  resolved_only_net_low=sum(o['net_usdt_low'] for o in known),
                  resolved_only_net_high=sum(o['net_usdt_high'] for o in known),
                  total_net_usdt=None, closed_trade_drawdown_usdt=None,
                  full_mark_to_market_drawdown_available=False)
    if all(o['status'] == 'exact_in_model' for o in outcomes):
        equity = peak = dd = 0.
        for o in sorted(outcomes, key=lambda o:o['latest_exit_bar_end']):
            equity += o['net_usdt_low']
            peak = max(peak, equity)
            dd = max(dd, peak-equity)
        result.update(total_net_usdt=equity, closed_trade_drawdown_usdt=dd)
    return result


def main():
    logging.disable(logging.CRITICAL)
    bundles, bars = load_archives(DATA/'archive_chunks.json')
    points = json.loads((DATA/'recorded_quotes.json').read_text())
    if len({(p['window'], p['logged_at']) for p in points}) != len(points):
        raise ValueError('duplicate quote observations')
    config = bundles[0]['model_config']
    outcome_bars = load_outcome_bars(DATA/'outcome_candle_logs.jsonl')
    result = dict(baseline_commit='4217f2047119fae96bca796f818c363594884bd9',
        independent_market_validation=False, actual_fills=False, sends=0,
        model_config=config, source_archives=len(bundles), unique_bars=dict(Counter(str(k[0]) for k in bars)),
        quote_count=len(points), outcome_5m_count=len(outcome_bars), experiments={},
        input_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in
            (DATA/'archive_chunks.json',DATA/'recorded_quotes.json',DATA/'outcome_candle_logs.jsonl',DATA/'PREDECLARED.md')})
    with tempfile.TemporaryDirectory(prefix='paired-entry-replay-') as directory:
        feed = make_feed(directory, bars)
        attributes = {CONFIG_MAP[k]:v for k,v in config.items()}
        attributes.update(COOLDOWN=2700, REVERSAL_COOLDOWN=5400, MAX_DAILY_ALERTS=8,
                          is_quiet_new_entry_time=lambda _:False)
        # Fail loudly if a future refactor accidentally adds a sender to this path.
        with patch.multiple(monitor, **attributes), patch.object(monitor,'telegram',side_effect=AssertionError('offline sender')):
            for delay in (0,15):
                for mode in MODES:
                    with patch.object(monitor, 'ENTRY_REFINEMENT_MODE', mode):
                        windows = {}
                        for window in sorted({p['window'] for p in points}):
                            selected = sorted((p for p in points if p['window']==window), key=lambda p:p['logged_at'])
                            row = replay_window(selected, feed, delay)
                            for signal in row['signals']:
                                signal['benchmark'] = outcome(signal, outcome_bars)
                            windows[window] = row
                        key = f'{mode}_delay{delay}'
                        result['experiments'][key] = dict(windows=windows, aggregate=aggregate(windows))
                        print(key, json.dumps(result['experiments'][key]['aggregate'], ensure_ascii=False))
    (DATA/'comparison.json').write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+'\n')


if __name__ == '__main__':
    main()
