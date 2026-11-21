"""Production ZB computation with an inert outbox and explicit qualification.

Runs independently of legacy entry/risk paths. Native stream arrivals are read
only; missing arrival provenance is never invented for old database rows.
Derived5m is recorded but cannot qualify as independent raw5m. No sender here.
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
from .core import Costs, D, dumps, digest
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
            # Candle volume semantics have not been independently established.
            # Keep it explicitly as a research assumption; qualification blocks
            # all READY events until a genuine independent 5m source is supplied.
            b = normalize_native(dict(row), row['received'], {**metadata, 'source': row['source']})
            result.append(b)
        return result
    finally:
        db.close()


class EngineThread(threading.Thread):
    def __init__(self, native_path=None, state_path=None, client=None, interval=3):
        super().__init__(name='lbank-zb-computation', daemon=True)
        self.native_path = native_path or os.getenv('LBANK_NATIVE_CANDLE_DB', '/data/lbank_native_candles.sqlite3')
        self.state_path = state_path or os.getenv('LBANK_ZB_ENGINE_DB', '/data/lbank_zb_engine.sqlite3')
        self.client = client or PublicClient()
        self.interval = max(3, interval)
        self.evidence = None
        self.last_evidence = 0
        self.last_log = 0

    def poll(self, store, now):
        if self.evidence is None or now - self.last_evidence >= 300:
            self.evidence = self.client.evidence()
            now=int(time.time())
            store.consume('evidence:' + digest(self.evidence), 'evidence', self.evidence, now)
            self.last_evidence = now
        e = store.engine
        # Snapshot stores the cursor atomically with the input it describes.
        after = getattr(e, 'native_cursor', None)
        for b in native_inputs(self.native_path, self.evidence['metadata'], after):
            saved = getattr(store.engine, 'native_cursor', None)
            store.engine.native_cursor = [b.received, b.end, b.period]
            try:
                store.consume('native:' + str(b.period) + ':' + str(b.start), 'bar', json.loads(dumps(b)), b.received)
            except Exception:
                store.engine.native_cursor = saved
                raise
        if store.engine.position or store.engine.pending:
            store.consume('clock-active:'+str(int(now)),'clock',{},now)
            q = self.client.quote(self.evidence)
            store.consume('quote:' + digest(q), 'quote', q, int(time.time()))
        if now - self.last_log >= 60:
            store.consume('clock:' + str(int(now) // 60), 'clock', {}, now)
            report = store.report()
            report.update({'paper_outbound_enabled': False, 'orders_enabled': False,
                           'cost_evidence': self.evidence['record'],
                           'native_1m': len(store.engine.one), 'native_5m': len(store.engine.five),
                           'native_cursor': getattr(store.engine, 'native_cursor', None),
                           'candle_volume_unit_status': 'UNVERIFIED_RESEARCH_ONLY'})
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
