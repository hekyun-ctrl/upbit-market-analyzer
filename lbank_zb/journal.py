"""One durable input transaction for bars, quotes, funding and state events."""
import json
from dataclasses import asdict
from .core import dumps, digest, dec, ZERO
from .store import Store, snapshot, restore, bar_from, costs_from

OUTBOUND = {'READY', 'EXPIRED', 'CANCELLED', 'HARD_STOP', 'STRUCTURE_INVALIDATED',
            'PARTIAL_TP1', 'TRAIL_UPDATED', 'TRAIL_ALREADY_CROSSED', 'CAUTION',
            'DATA_DEGRADED', 'TIME_REVIEW', 'EXIT_REQUIRED'}


class EventStore(Store):
    def __init__(self, path, engine):
        super().__init__(path, engine)
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS external_inputs(
            key TEXT PRIMARY KEY,kind TEXT NOT NULL,received INTEGER NOT NULL,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS archived_positions(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS runtime_observations(time INTEGER PRIMARY KEY,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS delivery_meta(
            key TEXT PRIMARY KEY,next_try INTEGER NOT NULL,first_at INTEGER NOT NULL);
        ''')

    def consume(self, key, kind, payload, now):
        body = dumps(payload)
        old = self.db.execute('SELECT kind,payload FROM external_inputs WHERE key=?', (key,)).fetchone()
        if old:
            if old != (kind, body):
                raise ValueError('EXTERNAL_INPUT_REVISION_REQUIRES_REPLAY')
            return False
        saved = snapshot(self.engine)
        n = len(self.engine.events)
        try:
            if kind == 'bar':
                self.engine.ingest(bar_from(payload), now)
            elif kind == 'quote':
                self.engine.quote(payload, now)
            elif kind == 'funding':
                # Closed positions live in SQL after compaction. Late settlement
                # requires replay, not an optimistic no-position zero charge.
                for raw, in self.db.execute('SELECT payload FROM archived_positions'):
                    p=json.loads(raw)
                    if p['fill_time']<int(payload['time'])<=max((ev['time'] for ev in p['events']),default=0):
                        self.engine.health.add('FUNDING_HISTORY_UNRESOLVED')
                        self.engine.emit('FUNDING_UNRESOLVED',now,id=p['plan']['candidate']['strategy'],settlement=payload['time'],reason='ARCHIVED_POSITION_REQUIRES_REPLAY')
                self.engine.funding(payload, now)
            elif kind == 'evidence':
                self.engine.costs = costs_from(payload['costs'])
                self.engine.settlement_schedule = list(payload['settlement_schedule'])
                self.engine.qualify(payload['qualification'])
            elif kind == 'funding_coverage':
                self.engine.coverage(payload,now)
            elif kind == 'clock':
                self.engine.advance(now)
            else:
                raise ValueError('UNKNOWN_INPUT_KIND')
            with self.db:
                self.db.execute('INSERT INTO external_inputs VALUES(?,?,?,?)', (key, kind, now, body))
                seq = self.db.execute('SELECT coalesce(max(seq),0) FROM events').fetchone()[0]
                for ev in self.engine.events[n:]:
                    seq += 1
                    h = digest({'seq': seq, 'event': ev})
                    encoded = dumps(ev)
                    self.db.execute('INSERT INTO events VALUES(?,?,?)', (seq, h, encoded))
                    if ev['kind'] in OUTBOUND:
                        self.db.execute('INSERT INTO outbox(key,kind,payload,expires,status) VALUES(?,?,?,?,?)',
                                        (h, ev['kind'], encoded, ev.get('expires'), 'DISABLED'))
                for p in self.engine.closed:
                    raw = asdict(p)
                    # No complete-settlement evidence means unknown cost, even
                    # when a planning reserve happened to be zero.
                    end=max((ev['time'] for ev in p.events),default=p.fill_time)
                    coverage=any(x['start']<=p.fill_time and x['end']>=end for x in self.engine.funding_coverage)
                    if not coverage:p.health.add('FUNDING_UNVERIFIED')
                    raw['health'], raw['review'] = sorted(p.health), sorted(p.review)
                    self.db.execute('INSERT OR REPLACE INTO archived_positions VALUES(?,?)', (p.plan.id, dumps(raw)))
                # Full history lives in append-only SQL. Do not snapshot an ever
                # growing events list on each quote (quadratic disk growth).
                self.engine.events = []
                self.engine.closed = []
                self.engine.seen = {f'{b.period}:{b.start}' for b in self.engine.one + self.engine.five}
                live_ids = {c.id for c in self.engine.pending}
                if self.engine.position:
                    live_ids.add(self.engine.position.plan.id)
                self.engine.signal_contexts = {k: v for k, v in self.engine.signal_contexts.items() if k in live_ids}
                self.db.execute('INSERT OR REPLACE INTO snapshots VALUES(1,?)', (snapshot(self.engine),))
            return True
        except Exception:
            self.engine = restore(saved)
            raise

    def observe(self, now, report):
        with self.db:
            self.db.execute('INSERT OR REPLACE INTO runtime_observations VALUES(?,?)', (int(now), dumps(report)))
            self.db.execute('DELETE FROM runtime_observations WHERE time<?', (int(now) - 7 * 86400,))

    def report(self):
        result = self.engine.report()
        positions=[json.loads(r[0]) for r in self.db.execute('SELECT payload FROM archived_positions')]
        result['archived_closed'] = len(positions)
        result['model_archived_closed_net']=sum((dec(p['cash']) for p in positions),ZERO)
        result['archived_unresolved']=sum(bool(p['health']) for p in positions)
        result['verified_closed']=0
        result['journal_inputs'] = self.db.execute('SELECT count(*) FROM external_inputs').fetchone()[0]
        result['journal_events'] = self.db.execute('SELECT count(*) FROM events').fetchone()[0]
        result['outbox'] = dict(self.db.execute('SELECT status,count(*) FROM outbox GROUP BY status').fetchall())
        return result


def replay_events(events, costs, path=':memory:', latency=60):
    from .live import LiveEngine
    store = EventStore(path, LiveEngine(costs, latency))
    previous = None
    try:
        for e in events:
            if previous is not None and e['received'] < previous:
                raise ValueError('ARRIVAL_ORDER')
            previous = e['received']
            store.consume(e['id'], e['kind'], e['payload'], e['received'])
        result = store.report()
        result['event_hash'] = digest(store.db.execute('SELECT seq,event_hash FROM events ORDER BY seq').fetchall())
        return result
    finally:
        store.close()
