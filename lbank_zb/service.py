"""Production ZB computation with an inert outbox and explicit qualification.

Runs independently of legacy entry/risk paths. Native stream arrivals are read
only; missing arrival provenance is never invented for old database rows.
Derived 5m is cross-checked against a separate same-venue close audit. No sender.
"""
import json
import logging
import os
import shutil
import sqlite3
import threading
import time
from pathlib import Path
from .adapter import normalize_native
from .candle_evidence import (
    CandleAuditThread, EVIDENCE_GRACE, PERIOD, PRICE_TICK, VOLUME_TICK, audit_status,
)
from .core import Costs, D, dec, dumps, digest
from .journal import EventStore
from .live import LiveEngine
from .public import PublicClient

LOG = logging.getLogger('lbank_zb_engine')


def native_inputs(path, metadata, after=None, limit=100):
    if not Path(path).is_file():
        return []
    db = sqlite3.connect('file:' + str(Path(path).resolve()) + '?mode=ro', uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    try:
        if not db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='native_arrivals'").fetchone():
            return []
        # Cursor is recorded arrival/end/period, not sqlite rowid (rowid can be
        # reused by INSERT OR REPLACE). 1m precedes 5m at a common close.
        rows = db.execute('''SELECT b.*,a.received,a.source FROM native_bars b
          JOIN native_arrivals a USING(period,start) WHERE b.quality='stream_closed'
          AND (a.received,b.start+b.period,b.period)>(?,?,?)
          ORDER BY a.received,b.start+b.period,b.period LIMIT ?''',
                          (*after, limit) if after else (0, 0, 0, limit)).fetchall()
        result = []
        for row in rows:
            b = normalize_native(dict(row), row['received'], {**metadata, 'source': row['source']})
            result.append(b)
        return result
    finally:
        db.close()


class EngineThread(threading.Thread):
    def __init__(self, native_path=None, state_path=None, client=None, interval=3, audit_path=None):
        super().__init__(name='lbank-zb-computation', daemon=True)
        self.native_path = native_path or os.getenv('LBANK_NATIVE_CANDLE_DB', '/data/lbank_native_candles.sqlite3')
        self.state_path = state_path or os.getenv('LBANK_ZB_ENGINE_DB', '/data/lbank_zb_engine.sqlite3')
        self.audit_path = audit_path or str(Path(self.state_path).with_name('lbank_zb_candle_evidence.sqlite3'))
        self.client = client or PublicClient()
        self.interval = max(3, interval)
        self.evidence = None
        self.last_evidence = 0
        self.last_log = 0

    def poll(self, store, now):
        refresh = self.evidence is None or now - self.last_evidence >= 300
        if refresh:
            self.evidence = self.client.evidence()
            now = int(time.time())
            self.last_evidence = now
        try:
            candles = audit_status(self.audit_path, now)
        except sqlite3.Error:
            candles = {'last_status': 'audit_db_unavailable', 'independent_5m': False}
        instrument = self.evidence['record']['instrument']
        volume_verified = (candles.get('last_volume_match_start') is not None
                           and dec(instrument['volumeMultiple']) == D(1)
                           and dec(instrument['priceTick']) == PRICE_TICK
                           and dec(instrument['volumeTick']) == VOLUME_TICK)
        qualification = dict(self.evidence['qualification'], candle_units=volume_verified,
                             independent_5m=volume_verified and candles['independent_5m'])
        if refresh or qualification != store.engine.qualification:
            evidence = dict(self.evidence, qualification=qualification,
                            candle_audit=candles, qualification_observed_at=now)
            store.consume('evidence:' + digest(evidence), 'evidence', evidence, now)
        e = store.engine
        # Snapshot stores the cursor atomically with the input it describes.
        after = getattr(e, 'native_cursor', None)
        for b in native_inputs(self.native_path, self.evidence['metadata'], after):
            if b.received > now:
                break
            latest_close = b.period == PERIOD and b.end == now // PERIOD * PERIOD
            # The independent checker reads the sealed derived bar first. Hold
            # only the new flat-state evaluation while that check is in flight.
            # A known mismatch, an expired wait, and open risk use the normal path.
            if (latest_close and not e.position and not e.pending
                    and candles.get('last_checked_start') != b.start
                    and now <= b.end + EVIDENCE_GRACE):
                break
            saved = getattr(store.engine, 'native_cursor', None)
            store.engine.native_cursor = [b.received, b.end, b.period]
            try:
                # Receipt remains in the bar. The journal event records when
                # evidence was actually available to this evaluation.
                store.consume('native:' + str(b.period) + ':' + str(b.start),
                              'bar', json.loads(dumps(b)), now)
            except Exception:
                store.engine.native_cursor = saved
                raise
            if latest_close:
                LOG.info('ZB_5M_INPUT start=%d received=%d evaluated=%d audit=%s independent_5m=%s',
                         b.start, b.received, now,
                         candles.get('last_status') if candles.get('last_checked_start') == b.start
                         else 'unavailable', qualification['independent_5m'])
        if store.engine.position or store.engine.pending:
            store.consume('clock-active:'+str(int(now)),'clock',{},now)
            q = self.client.quote(self.evidence)
            now = int(time.time())
            store.consume('quote:' + digest(q), 'quote', q, now)
        if now - self.last_log >= 60:
            store.consume('clock:' + str(int(now) // 60), 'clock', {}, now)
            report = store.report()
            units_status = 'VERIFIED_BTCUSDT_MULTIPLIER_1' if volume_verified else 'UNVERIFIED_RESEARCH_ONLY'
            report.update({'paper_outbound_enabled': False, 'orders_enabled': False,
                           'cost_evidence': dict(self.evidence['record'], candle_volume_unit=units_status),
                           'candle_independence_audit': candles,
                           'native_1m': len(store.engine.one), 'native_5m': len(store.engine.five),
                           'native_cursor': getattr(store.engine, 'native_cursor', None),
                           'candle_volume_unit_status': units_status})
            store.observe(now, report)
            LOG.info('ZB_ENGINE_STATUS %s', dumps(report))
            self.last_log = now
        return store.report()

    def run(self):
        store = None
        Path(self.state_path).parent.mkdir(parents=True, exist_ok=True)
        try:
            unknown = Costs(None, None, D(2), None, None, None, None)
            store = EventStore(self.state_path, LiveEngine(unknown))
            CandleAuditThread(self.native_path, self.audit_path).start()
            LOG.info('ZB_ENGINE_STARTED commit=%s paper_outbound=false orders=false', os.getenv('RAILWAY_GIT_COMMIT_SHA', 'unknown'))
            failures = 0
            while True:
                try:
                    if shutil.disk_usage(Path(self.state_path).parent).free < 20 * 1024 * 1024:
                        raise OSError('ZB_STORAGE_LOW')
                    self.poll(store, int(time.time()))
                    failures = 0
                except Exception as exc:
                    failures += 1
                    if failures == 1 or failures % 20 == 0:
                        LOG.warning('ZB_ENGINE_DEGRADED exception=%s reason=%s outbound=false legacy_risk_unchanged=true',
                                    type(exc).__name__, str(exc)[:150])
                    # Evidence staleness blocks new candidates; a last-known
                    # cost response never confers permanent entry permission.
                    if time.time() - self.last_evidence > 600:
                        store.engine.qualify({})
                time.sleep(min(60,self.interval*2**min(failures,4)))
        except Exception as exc:
            LOG.error('ZB_ENGINE_STOPPED exception=%s legacy_risk_unchanged=true', type(exc).__name__)
        finally:
            if store:
                store.close()
