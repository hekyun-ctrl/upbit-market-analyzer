"""Causal 1h/4h annotations. This module cannot accept/reject or resize a trade.

Only complete, cross-checked, same-product native 5m inputs are aggregated.
Unknown context never changes the ZB entry/exit policy.
"""
from dataclasses import asdict
from .core import Bar, aggregate, contiguous, dec, features, pivots, regime

VERSION = 'ZB-CONTEXT-1.0'
PERIODS = {'1h': 3600, '4h': 14400}
WARMUP = 150
HISTORY = 160


def _bar(row):
    row = dict(row)
    for key in ('o', 'h', 'l', 'c', 'v'):
        row[key] = dec(row[key])
    return Bar(**row)


class ContextFrame:
    def __init__(self, period):
        self.period = period
        self.bars = []
        self.pending = []
        self.pending_trusted = True
        self.last_input = None
        self.last_bad_end = None

    def ingest(self, bar, now, trusted):
        bar.validate(now)
        if bar.period != 300:
            raise ValueError('CONTEXT_REQUIRES_NATIVE_5M')
        if self.last_input is not None and bar.start <= self.last_input:
            raise ValueError('CONTEXT_INPUT_ORDER')
        bucket = bar.start // self.period * self.period
        if self.pending and self.pending[0].start // self.period * self.period != bucket:
            self.pending = []
            self.pending_trusted = True
        if self.pending and self.pending[-1].end != bar.start:
            self.pending = []
            self.pending_trusted = False
        self.pending.append(bar)
        self.pending_trusted = self.pending_trusted and bool(trusted)
        self.last_input = bar.start
        if bar.end != bucket + self.period:
            return
        complete = (len(self.pending) == self.period // 300 and
                    self.pending[0].start == bucket and contiguous(self.pending))
        if complete and self.pending_trusted:
            closed = aggregate(self.pending, self.period)[0]
            if self.bars and self.bars[-1].end != closed.start:
                self.bars = []  # Re-seed after a missing/invalid higher-timeframe bar.
            self.bars = (self.bars + [closed])[-HISTORY:]
        else:
            self.last_bad_end = bar.end
        self.pending = []
        self.pending_trusted = True

    def snapshot(self, now, price):
        # Never use a partial candle, or a candle not available by this decision.
        available = [b for b in self.bars if b.end <= now and b.received <= now]
        result = {'period_seconds': self.period, 'state': 'UNKNOWN',
                  'reason': 'NO_COMPLETE_BAR', 'complete_bars': len(available),
                  'required_bars': WARMUP, 'bar_end': None, 'known_at': None,
                  'support': None, 'resistance': None, 'atr': None,
                  'ema20': None, 'ema50': None}
        if not available:
            return result
        last = available[-1]
        result.update(bar_end=last.end, known_at=last.received)
        expected = now // self.period * self.period
        if now % self.period <= 15:
            expected -= self.period
        if last.end < expected or (self.last_bad_end is not None and last.end < self.last_bad_end <= now):
            result['reason'] = 'STALE_OR_GAPPED_CONTEXT'
            return result
        if not contiguous(available):
            result['reason'] = 'GAPPED_CONTEXT'
            return result
        if len(available) < WARMUP:
            result['reason'] = 'CONTEXT_WARMUP'
            return result
        f = features(available)
        atr = f['atr'][-1]
        result.update(state=regime(available), reason=None,
                      atr=str(atr) if atr is not None else None,
                      ema20=str(f['ema20'][-1]), ema50=str(f['ema50'][-1]))
        if not atr:
            result.update(state='UNKNOWN', reason='ZERO_ATR')
            return result
        # These are structure candidates, not a claim that buying/selling occurs here.
        levels = [p for p in pivots(available, now) if p['index'] >= len(available) - 120]
        def level(p):
            return {'price': str(p['price']), 'origin_side': p['side'],
                    'pivot_time': p['time'], 'known_at': p['known_at'],
                    'distance_atr': str(abs(p['price'] - price) / atr),
                    'candidate_only': True}
        below = [p for p in levels if p['price'] < price]
        above = [p for p in levels if p['price'] > price]
        if below:
            result['support'] = level(max(below, key=lambda p: (p['price'], p['time'])))
        if above:
            result['resistance'] = level(min(above, key=lambda p: (p['price'], -p['time'])))
        return result

    def to_dict(self):
        return {'period': self.period, 'bars': [asdict(b) for b in self.bars],
                'pending': [asdict(b) for b in self.pending],
                'pending_trusted': self.pending_trusted,
                'last_input': self.last_input, 'last_bad_end': self.last_bad_end}

    @classmethod
    def from_dict(cls, row):
        obj = cls(row['period'])
        obj.bars = [_bar(b) for b in row['bars']]
        obj.pending = [_bar(b) for b in row['pending']]
        obj.pending_trusted = row['pending_trusted']
        obj.last_input = row['last_input']
        obj.last_bad_end = row['last_bad_end']
        return obj


class HigherTimeframes:
    def __init__(self):
        self.frames = {name: ContextFrame(period) for name, period in PERIODS.items()}

    def ingest(self, bar, now, trusted):
        for frame in self.frames.values():
            frame.ingest(bar, now, trusted)

    def snapshot(self, now, price, side):
        result = {'version': VERSION, 'mode': 'REFERENCE_ONLY', 'observed_at': now,
                  'reference_price': str(price), 'signal_side': side, 'frames': {}}
        for name, frame in self.frames.items():
            row = frame.snapshot(now, price)
            row['alignment'] = alignment(row['state'], side)
            result['frames'][name] = row
        one, four = result['frames']['1h'], result['frames']['4h']
        directional = {'TREND_UP', 'TREND_DOWN'}
        if one['state'] == 'UNKNOWN' or four['state'] == 'UNKNOWN':
            result['relationship'] = 'UNKNOWN'
        elif one['state'] in directional and four['state'] in directional:
            result['relationship'] = 'SAME_DIRECTION' if one['state'] == four['state'] else 'OPPOSITE_DIRECTIONS'
        else:
            result['relationship'] = 'MIXED_OR_RANGE'
        return result

    def to_dict(self):
        return {'version': VERSION, 'frames': {k: v.to_dict() for k, v in self.frames.items()}}

    @classmethod
    def from_dict(cls, row):
        if row['version'] != VERSION:
            raise ValueError('CONTEXT_VERSION')
        obj = cls()
        obj.frames = {k: ContextFrame.from_dict(v) for k, v in row['frames'].items()}
        if {k: v.period for k, v in obj.frames.items()} != PERIODS:
            raise ValueError('CONTEXT_PERIODS')
        return obj


def alignment(state, side):
    if state in ('TREND_UP', 'TREND_DOWN'):
        return 'ALIGNED' if (state == 'TREND_UP') == (side == 1) else 'COUNTERTREND'
    return state


def render_context(context):
    names = {'TREND_UP': '상승', 'TREND_DOWN': '하락', 'RANGE': '횡보',
             'TRANSITION': '전환', 'UNKNOWN': '자료 부족 또는 지연'}
    lines = ['상위 흐름 참고 · 진입 허가/차단 조건 아님']
    for name in ('4h', '1h'):
        row = context['frames'][name]
        support = row['support']['price'] if row['support'] else '미확인'
        resistance = row['resistance']['price'] if row['resistance'] else '미확인'
        lines.append(f'{"4시간" if name == "4h" else "1시간"}: {names[row["state"]]} / 지지 후보 {support} / 저항 후보 {resistance}')
    if context['relationship'] == 'OPPOSITE_DIRECTIONS':
        lines.append('1시간·4시간 방향이 다릅니다. 조정/반등 여부를 관찰하며 방향 전환을 확정하지 않습니다.')
    return '\n'.join(lines)
