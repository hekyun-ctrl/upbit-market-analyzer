"""Quantity-aware quote simulation and funding inputs for the shared ZB engine.

Public observations are never orders or evidence of a user's fill. OHLC replay
keeps its original minute execution. This engine consumes recorded quote events
in arrival order and uses the same candidate, plan and position accounting.
"""
from dataclasses import replace
import copy
from .core import (D, ZERO, dec, plan, pivots, Position, render, rounding,
                   bar_fresh, dumps)
from .adapter import depth_fill
from .engine import Engine
from .context import render_context

PRODUCT = 'lbank:linear_usdt_perpetual:BTCUSDT:LAST'


class LiveEngine(Engine):
    def __init__(self, costs, latency=60, strategies=None, context_mode='record'):
        super().__init__(costs, latency, strategies, context_mode)
        self.execution_mode = 'quote'
        self.last_quote_at = None
        self.settlement_schedule = []
        self.funding_applied = []
        self.funding_records = {}
        self.funding_coverage = []
        self.pending_exit = None
        self.qualification = {'product': False, 'independent_5m': False,
                              'candle_units': False, 'fees': False, 'funding_plan': False}
        self.entry_blocks = ['PRODUCT_UNVERIFIED', 'RAW5M_INDEPENDENCE_UNVERIFIED',
                             'CANDLE_VOLUME_UNIT_UNVERIFIED', 'FEES_UNVERIFIED', 'FUNDING_PLAN_UNAVAILABLE']

    def qualify(self, evidence):
        # The public adapter supplies observations, never an env "ready" switch.
        names = {'product': 'PRODUCT_UNVERIFIED',
                 'independent_5m': 'RAW5M_INDEPENDENCE_UNVERIFIED',
                 'candle_units': 'CANDLE_VOLUME_UNIT_UNVERIFIED',
                 'fees': 'FEES_UNVERIFIED', 'funding_plan': 'FUNDING_PLAN_UNAVAILABLE'}
        self.qualification = {k: evidence.get(k) is True for k in names}
        self.entry_blocks = [v for k, v in names.items() if not self.qualification[k]]

    def ingest(self, b, now, quote=None):
        b.validate(now)
        if b.period==60:
            for c in list(self.pending):
                if b.start>=c.signal_time and (b.l<=c.S if c.side==1 else b.h>=c.S):
                    self.pending.remove(c)
                    self.emit('CANCELLED',now,id=c.id,reason='STOP_BEFORE_FILL')
        # Entry permissions do not suspend risk processing for an open position.
        self.health.difference_update({'PRODUCT_UNVERIFIED', 'RAW5M_INDEPENDENCE_UNVERIFIED',
                                       'CANDLE_VOLUME_UNIT_UNVERIFIED','FEES_UNVERIFIED', 'FUNDING_PLAN_UNAVAILABLE'})
        self.health.update(self.entry_blocks)
        super().ingest(b, now)

    def advance(self, now):
        for c in list(self.pending):
            if now > c.expires:
                self.pending.remove(c)
                self.emit('EXPIRED', now, id=c.id)
        if self.position:
            if self.last_quote_at is None or now - self.last_quote_at > 5:
                self.position.health.add('QUOTE_GAP')
            for t in self.settlement_schedule:
                if self.position.fill_time < t <= now and t not in self.funding_applied:
                    self.position.health.add('FUNDING_UNVERIFIED')
            for hours in (6, 24):
                if now - self.position.fill_time >= hours * 3600 and hours not in self.position.review:
                    self.position.review.add(hours)
                    self.emit('TIME_REVIEW', now, id=self.position.plan.id, hours=hours)

    def quote(self, q, now):
        if q.get('product') != PRODUCT:
            raise ValueError('WRONG_QUOTE_PRODUCT')
        received = int(q['received'])
        if not 0 <= now - received <= 5:
            raise ValueError('STALE_QUOTE')
        if self.last_quote_at is not None and received < self.last_quote_at:
            raise ValueError('QUOTE_ORDER')
        last = dec(q['last'])
        if last <= 0:
            raise ValueError('LAST_PRICE')
        # Validate both sides even when no candidate is active.
        multiplier = dec(q['contract_multiplier'])
        if multiplier <= 0:
            raise ValueError('CONTRACT_MULTIPLIER')
        book = q['book']
        for side in (1, -1):
            depth_fill(book, side, D('.00000001'), multiplier, q['volume_unit'])
        self.advance(now)
        self.last_quote_at = received
        if self.position:
            self._manage_quote(q, last, now)
        for c in list(self.pending):
            if now > c.expires:
                continue
            if c.side * (last - c.S) <= 0 or c.side * (last - c.I) <= 0:
                self.pending.remove(c)
                self.emit('CANCELLED', now, id=c.id, reason='THESIS_CROSSED_BEFORE_FILL')
                continue
            if now < c.signal_time + self.latency:
                continue
            self.pending.remove(c)  # First eligible quote only; never revive later.
            why = list(self.entry_blocks)
            if self.position:
                why.append('ACTIVE_POSITION')
            if self.health or not bar_fresh(self.one, 60, now) or not bar_fresh(self.five, 300, now):
                why.append('DATA_DEGRADED')
            if self.review or self.daily_halted or self.nav(last) <= self.day_start * D('.99'):
                why.append('RISK_HALT')
            if c.origin <= self.last_exit:
                why.append('NEW_PATTERN_REQUIRED')
            if why:
                self.emit('UNFILLED', now, id=c.id, reasons=sorted(set(why)))
                continue
            levels = [p['price'] for p in pivots(self.five, c.signal_time)
                      if p['side'] == ('high' if c.side == 1 else 'low')]
            p, why = plan(c, last, self.costs, self.nav(last), levels,entry_fill=last)
            if p:
                # Quantity changes with VWAP. Demand a stable fixed point.
                for _ in range(8):
                    try:
                        fill = depth_fill(book, c.side, p.quantity, multiplier, q['volume_unit'])
                    except ValueError as exc:
                        p = None
                        why = [str(exc)]
                        break
                    revised, why = plan(c, last, self.costs, self.nav(last), levels, entry_fill=fill)
                    if not revised:
                        p = None
                        break
                    if revised.quantity == p.quantity:
                        p = revised
                        break
                    p = revised
                else:
                    p = None
                    why = ['DEPTH_QUANTITY_NOT_CONVERGED']
            if not p:
                self.emit('UNFILLED', now, id=c.id, reasons=why)
                continue
            message = render(p)
            if c.id in self.signal_contexts:
                message += '\n' + render_context(self.signal_contexts[c.id])
            self.emit('READY', now, id=c.id, plan=p, message=message, expires=c.expires,
                      quote_received=received, execution='observed_depth_paper_only')
            self.position = Position.open(p, now)
            self.emit('PAPER_OPEN', now, id=c.id, plan=p, actual_fill=False)
        # Quote valuation must not advance the finalized candle watermark: a
        # minute close may legitimately arrive after a quote just past boundary.
        nav=self.nav(last);self.peak=max(self.peak,nav)
        if nav<=self.peak*D('.95'):self.review=True
        if nav<=self.day_start*D('.99') and not self.daily_halted:
            self.daily_halted=True;self.daily_halt_at=now;self.daily_halt_received_at=now

    def _manage_quote(self, q, last, now):
        p = self.position
        d = p.plan.candidate.side
        p.last_price = last
        stop = d * (last - p.active_stop) <= 0
        target = d * (last - p.plan.target) >= 0 and not p.partial
        forced=self.pending_exit is not None and self.pending_exit['id']==p.plan.id
        if not stop and not target and not forced:
            return
        quantity = p.remaining if stop or forced else rounding(p.plan.quantity * D('.25'), p.plan.costs.step)
        if quantity <= 0 or (not stop and not forced and quantity >= p.remaining):
            if not p.partial_skipped:
                p.partial_skipped = True
                self.emit('PARTIAL_UNEXECUTABLE', now, id=p.plan.id, reason='STEP_OR_MINIMUM_ORDER')
            return
        try:
            fill = depth_fill(q['book'], -d, quantity, dec(q['contract_multiplier']), q['volume_unit'])
        except ValueError as exc:
            p.health.add('EXIT_DEPTH_UNAVAILABLE')
            self.emit('DATA_DEGRADED', now, id=p.plan.id, reason=str(exc), urgent=stop or forced)
            return
        if not stop and not forced and min(quantity, p.remaining - quantity) * fill < p.plan.costs.minimum_notional:
            if not p.partial_skipped:
                p.partial_skipped = True
                self.emit('PARTIAL_UNEXECUTABLE', now, id=p.plan.id, reason='MINIMUM_ORDER')
            return
        original = p.plan
        try:
            p.plan = replace(original, costs=replace(original.costs, slip_bps=ZERO))
            p.exit(fill, quantity, now, 'HARD_STOP' if stop else self.pending_exit['reason'] if forced else 'PARTIAL_TP1')
        finally:
            p.plan = original
        detail = p.events[-1]
        if forced:detail['decision_at']=self.pending_exit['first_at']
        if not stop and not forced:
            p.partial = True
            p.state = 'PAPER_PARTIAL'
            detail['actual_fraction'] = str(quantity / original.quantity)
        self.emit(detail['kind'], now, id=p.plan.id, detail=detail, actual_fill=False)
        self.finish_position(now)
        if self.position is None:self.pending_exit=None

    def manage_five_position(self,b,a,state,now):
        # Fix the bar decision, then await actual depth instead of pretending
        # that a delayed live exit filled at an old candle's closing price.
        proposed=copy.deepcopy(self.position);n=len(proposed.events)
        proposed.five(b,a,state,now)
        if proposed.state=='PAPER_CLOSED':
            reason=proposed.events[-1]['kind']
            if self.pending_exit is None:
                self.pending_exit={'id':self.position.plan.id,'reason':reason,'first_at':now,'bar_close':str(b.c)}
                self.emit('EXIT_REQUIRED',now,id=self.position.plan.id,reason=reason,bar_close=str(b.c))
            return
        self.position=proposed
        for ev in proposed.events[n:]:self.emit(ev['kind'],ev['time'],id=proposed.plan.id,detail=ev)

    def funding(self, event, now):
        if event.get('product') != PRODUCT or event.get('settled') is not True or event.get('price_basis')!='MARK':
            raise ValueError('UNCONFIRMED_FUNDING')
        t = int(event['time'])
        if not t<=int(event['received'])<=now or not event.get('source'):
            raise ValueError('FUNDING_AVAILABILITY')
        rate, price = dec(event['rate']), dec(event['price'])
        if price <= 0 or abs(rate) > 1:
            raise ValueError('FUNDING_VALUE')
        record={k:event[k] for k in ('product','time','rate','price','price_basis','settled')}
        if t in self.funding_applied:
            if self.funding_records.get(str(t))!=record:raise ValueError('FUNDING_REVISION_REQUIRES_REPLAY')
            return
        # Unknown late settlement must never be charged to today's remaining size.
        if self.position and self.position.fill_time < t:
            if self.position.events and any(e['time'] > t for e in self.position.events):
                self.position.health.add('FUNDING_UNVERIFIED')
                self.emit('FUNDING_UNRESOLVED', now, id=self.position.plan.id, settlement=t,
                          reason='LATE_AFTER_POSITION_MUTATION')
                return
            n = len(self.position.events)
            self.position.funding_event(t, rate, price)
            for ev in self.position.events[n:]:
                self.emit('FUNDING', now, id=self.position.plan.id, detail=ev, settlement=t)
        elif any(p.fill_time < t <= max((e['time'] for e in p.events), default=0) for p in self.closed):
            self.health.add('FUNDING_HISTORY_UNRESOLVED')
            self.emit('FUNDING_UNRESOLVED', now, settlement=t, reason='CLOSED_POSITION_REQUIRES_REPLAY')
            return
        self.funding_applied.append(t)
        self.funding_records[str(t)]=record

    def coverage(self, event, now):
        if (event.get('product')!=PRODUCT or event.get('complete') is not True
                or not event.get('source') or not 0<=int(event['start'])<int(event['end'])<=now
                or int(event['received'])>now):
            raise ValueError('FUNDING_COVERAGE_UNVERIFIED')
        expected=[int(t) for t in event['settlements']]
        if expected!=sorted(set(expected)) or any(not event['start']<t<=event['end'] for t in expected):
            raise ValueError('FUNDING_COVERAGE_SCHEDULE')
        if any(t not in self.funding_applied for t in expected):
            raise ValueError('FUNDING_COVERAGE_MISSING_SETTLEMENT')
        self.funding_coverage.append(dict(event))

    def report(self):
        result = super().report()
        result['execution_mode'] = self.execution_mode
        result['qualification'] = self.qualification
        result['blocked_reasons'] = self.entry_blocks
        result['funding_applied'] = self.funding_applied
        result['verified_net'] = None
        result['limitations'] = ['Quote snapshots are a paper execution estimate, not actual fills.',
                                'Independent raw5m and complete funding coverage are required.',
                                'Historical comparison and sealed qualification remain separate.']
        return result
