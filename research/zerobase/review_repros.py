"""Offline reproductions for ZB-1 commit 22724922c146eeb9c927d65afa8aaad88164115d.

Run from that repository checkout:
  PYTHONPATH=. python /path/to/ZB1_review_repros_20261021.py

Synthetic fixtures only; no requests, orders, notifications, or production writes.
`reproduced: true` means the reported defect is present, not a passing acceptance test.
"""
import json
from dataclasses import replace
from lbank_zb.core import Bar, Candidate, Costs, D, Position, contiguous, plan
from lbank_zb.engine import Engine
from lbank_zb.store import Store

C = Costs(D('.0001'), D('.0001'), D(0), D('.01'), D('.00001'), D(1), D(0), 'ASSUMED', 'review_fixture')


def bar(t=0, p=60, o='100', h='101', l='99', c='100', v='1', received=None):
    return Bar(t, p, D(o), D(h), D(l), D(c), D(v), t+p if received is None else received, source='synthetic_review_fixture')


def candidate(side=1):
    return Candidate('TREND_PULLBACK', side, 600, 300, D(100), D(2), D(97) if side == 1 else D(103), D(96) if side == 1 else D(104), D(97) if side == 1 else D(103))


def position(costs=C, side=1):
    p, reasons = plan(candidate(side), D(100), costs, D(1000))
    assert not reasons, reasons
    return Position.open(p, 660)


def duplicate_arrival():
    store = Store(':memory:', Engine(C))
    try:
        original = bar()
        store.ingest(original, 60)
        error = None
        try:
            store.ingest(replace(original, received=61), 61)
        except ValueError as exc:
            error = str(exc)
        return {'id': 'R1', 'reproduced': error == 'REVISION_REQUIRES_REPLAY_VERSION', 'expected': 'Same OHLCV received again is idempotent; preserve first-known time.', 'actual_error': error, 'correction_rows': store.db.execute('SELECT count(*) FROM corrections').fetchone()[0]}
    finally:
        store.close()


def premature_data_recovery():
    e = Engine(C)
    t = 449 * 300
    e.five = [bar(i * 300, 300, v='5') for i in range(449)]
    # A missing minute remains in the last 60 observations, outside the latest 5m.
    missing = t - 600
    e.one = [bar(x) for x in range(t - 3600, t, 60) if x != missing]
    e.health.add('DATA_DEGRADED')
    for x in range(t, t + 300, 60):
        e.ingest(bar(x), x + 60)
    assert len(e.one) == 60 and not contiguous(e.one)
    e.ingest(bar(t, 300, v='5'), t + 300)
    return {'id': 'R2', 'reproduced': not contiguous(e.one) and 'DATA_DEGRADED' not in e.health, 'expected': 'Keep entries blocked until missing inputs are resolved and mandatory continuity is restored.', 'one_minute_contiguous': contiguous(e.one), 'one_minute_count': len(e.one), 'health_after_matching_5m': sorted(e.health), 'missing_minute_start': missing}


def partial_quantity():
    p = position()
    p.minute(bar(660, h='107', l='99', c='106'), 720)
    sold = D(p.events[-1]['quantity'])
    return {'id': 'R3', 'reproduced': sold % C.step != 0 or p.remaining % C.step != 0, 'expected': 'Both exit quantity and remaining quantity conform to the known quantity step.', 'step': str(C.step), 'original_quantity': str(p.plan.quantity), 'partial_quantity': str(sold), 'remaining_quantity': str(p.remaining)}


def break_even_slippage():
    values = []
    for side in (1, -1):
        costs = replace(C, slip_bps=D(2))
        p = position(costs, side)
        # Actual first partial exit, followed by a full 5m that lets BE dominate ATR.
        first = bar(660, h='107', l='99', c='106') if side == 1 else bar(660, h='101', l='93', c='94')
        p.minute(first, 720)
        follow = bar(900, 300, o='100.1', h='100.2', l='99.9', c='100.15') if side == 1 else bar(900, 300, o='99.9', h='100.1', l='99.8', c='99.85')
        p.five(follow, D(2), 'TREND_UP' if side == 1 else 'TREND_DOWN', 1200)
        assert p.partial and p.state == 'PAPER_PARTIAL'
        exit_fill = costs.fill(p.active_stop, -side)
        residual_net_per_btc = side * (exit_fill - p.plan.entry) - p.plan.entry * costs.fee_entry - exit_fill * costs.fee_exit
        values.append({'side': side, 'entry': str(p.plan.entry), 'stop_trigger': str(p.active_stop), 'modeled_exit_fill': str(exit_fill), 'remaining_leg_net_per_btc': str(residual_net_per_btc)})
    return {'id': 'R4', 'reproduced': all(D(v['remaining_leg_net_per_btc']) < 0 for v in values), 'expected': 'A BE-determined stop covers entry fees, exit fees, modeled exit slippage and allocated funding.', 'funding': '0', 'slippage_bps': '2', 'cases': values}


def daily_halt_not_latched():
    e = Engine(C)
    e.day = (720 + 9 * 3600) // 86400
    e.day_start = D(1000)
    e.equity = D(991)  # Earlier same-day realized losses; still above the 990 halt line.
    e.position = position()
    e.ingest(bar(660, o='100', h='100', l='97.9', c='98'), 720)
    touched_nav = e.nav()
    assert touched_nav < D(990) and e.position is not None
    e.ingest(bar(720, o='98', h='107', l='98', c='106'), 780)
    e.ingest(bar(780, o='100', h='101', l='95', c='96'), 840)
    assert e.position is None
    recovered_nav = e.nav()
    assert recovered_nav > D(990)
    for t in range(840, 1200, 60):
        e.ingest(bar(t), t + 60)
    e.pending = [replace(candidate(), signal_time=1200, origin=900)]
    e.ingest(bar(1200), 1260)
    e.ingest(bar(1260), 1320)
    return {'id': 'R5', 'reproduced': touched_nav < D(990) and e.counts.get('PAPER_OPEN', 0) == 1, 'expected': 'After the 1% daily NAV limit is reached, block new entries until the next KST day, even after recovery.', 'day_start_nav': '1000', 'halt_line': '990', 'nav_below_limit': str(touched_nav), 'nav_after_recovery': str(recovered_nav), 'new_entry_count_after_limit': e.counts.get('PAPER_OPEN', 0)}


def stop_before_day_rollover():
    midnight = 54000  # 1970-01-02 00:00 KST, UTC seconds.
    e = Engine(C)
    e.day = (midnight - 60 + 9 * 3600) // 86400
    e.day_start = D(1000)
    p = position()
    p.fill_time = midnight - 600
    p.stop_since = p.fill_time
    e.position = p
    old_day = str(e.day)
    # Stop hit before midnight; the close later rebounds, still below TP1.
    e.ingest(bar(midnight - 60, o='100', h='105', l='95', c='104'), midnight)
    actual_nav = e.nav()
    return {'id': 'R6', 'reproduced': e.day_start != actual_nav and D(e.daily[old_day]['end']) != actual_nav, 'expected': 'Process this completed minute\'s stop before finalizing previous-day ending NAV and next-day starting NAV.', 'actual_cash_nav_after_stop': str(actual_nav), 'recorded_previous_day_end_nav': e.daily[old_day]['end'], 'recorded_next_day_start_nav': str(e.day_start), 'closed_positions': len(e.closed)}


if __name__ == '__main__':
    results = [duplicate_arrival(), premature_data_recovery(), partial_quantity(), break_even_slippage(), daily_halt_not_latched(), stop_before_day_rollover()]
    print(json.dumps({'reviewed_commit': '22724922c146eeb9c927d65afa8aaad88164115d', 'fixtures': 'SYNTHETIC; NOT MARKET PERFORMANCE', 'results': results}, ensure_ascii=False, indent=2))
    assert all(r['reproduced'] for r in results), 'A finding no longer reproduces; inspect the checked-out revision.'

