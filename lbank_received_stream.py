"""Inert, bounded import of sealed transport receipts and exact snapshot bindings.

A seal proves byte integrity, not exchange authenticity or market qualification.
The only built-in decoder describes the retained public chart protocol; it has
NO verified final flag. Additional decoders require reviewed code, not manifest
booleans. Imported evidence never clears the live source blockers or sends.
"""
from datetime import datetime
import hashlib
import json
import math
from pathlib import Path
import re
import zlib

from lbank_signal_evidence import canonical, digest

SCHEMA = 'sealed_received_stream_v1'
CHART_SCHEMA = 'lbank_chart_observed_20261017'
BLOCKERS = ['RAW5M_INDEPENDENCE_UNVERIFIED', 'CANDLE_VOLUME_UNIT_UNVERIFIED']
MAX_BYTES = 8 * 1024 * 1024
MAX_RECORDS = 10000
MAX_WIRE = 1024 * 1024
BAR_FIELDS = ('period', 'start', 'open', 'high', 'low', 'close', 'volume', 'turnover', 'updated')
GROUPS = {'minute_bars': 60, 'five_bars': 300, 'research_five_bars': 300}


def strict_json(raw):
    def pairs(items):
        value = {}
        for key, item in items:
            if key in value:
                raise ValueError('DUPLICATE_JSON_KEY')
            value[key] = item
        return value
    def invalid(value):
        raise ValueError('NONFINITE_JSON_NUMBER')
    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def receipt_time(value):
    # Keep original UTC text in storage; never synthesize a historical receipt.
    if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,6})?(?:Z|\+00:00)', value):
        raise ValueError('EXACT_UTC_TRANSPORT_TIME_REQUIRED')
    at = datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    if at < 0 or not math.isfinite(at):
        raise ValueError('INVALID_RECEIPT_TIME')
    return at


def _number(value):
    return type(value) in (int, float) and math.isfinite(value)


def validate_bar(bar):
    if (not isinstance(bar, dict) or set(bar) != set(BAR_FIELDS)
            or type(bar['period']) is not int or bar['period'] not in (60, 300)
            or type(bar['start']) is not int or bar['start'] <= 0 or bar['start'] % bar['period']
            or type(bar['updated']) is not int
            or not bar['start'] <= bar['updated'] < bar['start'] + bar['period']
            or not all(_number(bar[k]) for k in BAR_FIELDS[2:8])
            or min(bar[k] for k in ('open', 'high', 'low', 'close')) <= 0
            or min(bar['volume'], bar['turnover']) < 0
            or bar['high'] < max(bar['open'], bar['close'], bar['low'])
            or bar['low'] > min(bar['open'], bar['close'], bar['high'])):
        raise ValueError('INVALID_NATIVE_BAR')


def chart_decoder(wire):
    """Observed chart schema only; tradeTime and interval rollover are NOT final."""
    if not wire.lstrip().startswith(b'{'):
        decoder = zlib.decompressobj()
        wire = decoder.decompress(wire, MAX_WIRE)
        if not decoder.eof or decoder.unconsumed_tail or decoder.unused_data:
            raise ValueError('INVALID_COMPRESSED_WIRE')
    frame = strict_json(wire.decode('utf-8'))
    if (not isinstance(frame, dict) or set(frame) - {'d', 'e', 'w', 'x', 'y', 'z'}
            or type(frame.get('x')) is not int or frame['x'] != 2
            or type(frame.get('z')) is not int or frame['z'] not in (3, 4)
            or type(frame.get('w')) is not int or frame['w'] < 0):
        raise ValueError('UNKNOWN_CHART_SCHEMA')
    rows = frame.get('d')
    if isinstance(rows, dict):
        rows = [rows]
    if not isinstance(rows, list) or not rows or len(rows) > 1000:
        raise ValueError('UNKNOWN_CHART_ROWS')
    bars = []
    for raw in rows:
        if (not isinstance(raw, dict) or set(raw) - set('abcdefghijk') - {'tradeTime'}
                or raw.get('a') != 'BTCUSDT' or raw.get('b') not in ('1m', '5m')
                or type(raw.get('c')) is not int or type(raw.get('k')) is not int):
            raise ValueError('UNKNOWN_CHART_BAR_SCHEMA')
        bar = dict(period=60 if raw['b'] == '1m' else 300, start=raw['c'],
                   updated=raw['k'])
        try:
            for field, key in zip(('open', 'high', 'low', 'close', 'volume', 'turnover'), 'dfgehi'):
                if isinstance(raw[key], bool):
                    raise ValueError('INVALID_CHART_NUMBER')
                bar[field] = float(raw[key])
        except (KeyError, TypeError, OverflowError) as exc:
            raise ValueError('UNKNOWN_CHART_BAR_SCHEMA') from exc
        validate_bar(bar)
        if bar['updated'] > frame['w'] / 1000 + 2:
            raise ValueError('BAR_AHEAD_OF_SERVER')
        bars.append(dict(bar=bar, final=None, final_basis=None))
    return bars


def seal_segment(payload):
    """Deterministic producer helper. Call only with genuinely recorded times."""
    return dict(payload=payload, sha256=digest(payload))


class ReceivedStreamImporter:
    def __init__(self, store, *, decoders=None):
        self.store, self.db = store, store.db
        # Extension is a code API for future documented schemas and synthetic
        # tests. No JSON/config/CLI flag can turn chart forming bars into final.
        self.decoders = {CHART_SCHEMA: chart_decoder}
        if decoders:
            if CHART_SCHEMA in decoders:
                raise ValueError('BUILTIN_SCHEMA_CANNOT_BE_REPLACED')
            self.decoders.update(decoders)
        with store.book.transaction():
            for sql in (
                'CREATE TABLE IF NOT EXISTS received_segments(seal TEXT PRIMARY KEY,stream TEXT NOT NULL,segment TEXT NOT NULL,payload TEXT NOT NULL,UNIQUE(stream,segment))',
                'CREATE TABLE IF NOT EXISTS received_records(stream TEXT NOT NULL,seq INTEGER NOT NULL,segment_seal TEXT NOT NULL REFERENCES received_segments(seal),kind TEXT NOT NULL,session TEXT NOT NULL,at REAL NOT NULL,source_hash TEXT,payload TEXT NOT NULL,PRIMARY KEY(stream,seq))',
                'CREATE TABLE IF NOT EXISTS received_bars(stream TEXT NOT NULL,seq INTEGER NOT NULL,bar_index INTEGER NOT NULL,period INTEGER NOT NULL,start INTEGER NOT NULL,revision INTEGER NOT NULL,final INTEGER,known_at REAL,payload TEXT NOT NULL,PRIMARY KEY(stream,seq,bar_index),FOREIGN KEY(stream,seq) REFERENCES received_records(stream,seq))',
                'CREATE TABLE IF NOT EXISTS received_snapshots(snapshot_hash TEXT PRIMARY KEY,stream TEXT NOT NULL,seq INTEGER NOT NULL,payload TEXT NOT NULL,FOREIGN KEY(stream,seq) REFERENCES received_records(stream,seq))',
                'CREATE INDEX IF NOT EXISTS received_bar_key ON received_bars(stream,period,start,seq)',
            ):
                self.db.execute(sql)

    def import_file(self, path):
        with Path(path).open('rb') as source:
            raw = source.read(MAX_BYTES + 1)
        if len(raw) > MAX_BYTES:
            raise ValueError('SEALED_STREAM_BYTE_CAP')
        return self.import_segment(strict_json(raw.decode('utf-8')))

    def import_segment(self, sealed):
        if len(canonical(sealed).encode()) > MAX_BYTES:
            raise ValueError('SEALED_STREAM_BYTE_CAP')
        if not isinstance(sealed, dict) or set(sealed) != {'payload', 'sha256'}:
            raise ValueError('INVALID_SEAL_ENVELOPE')
        value, seal = sealed['payload'], sealed['sha256']
        if digest(value) != seal:
            raise ValueError('SEALED_STREAM_HASH_MISMATCH')
        if (not isinstance(value, dict) or set(value) != {'schema', 'stream', 'segment', 'previous', 'records'}
                or value.get('schema') != SCHEMA or not isinstance(value.get('records'), list)
                or not 0 < len(value['records']) <= MAX_RECORDS
                or not all(isinstance(value.get(k), str) and 0 < len(value[k]) <= 128 for k in ('stream', 'segment'))):
            raise ValueError('UNKNOWN_SEALED_STREAM_SCHEMA')
        old = self.db.execute('SELECT payload FROM received_segments WHERE seal=?', (seal,)).fetchone()
        if old:
            if old[0] != canonical(value):
                raise ValueError('SEALED_STREAM_REVISION')
            return dict(seal=seal, records=len(value['records']), duplicate=True, qualified=False)
        stream = value['stream']
        tail = self.db.execute('SELECT seq,segment_seal,session,at FROM received_records WHERE stream=? ORDER BY seq DESC LIMIT 1', (stream,)).fetchone()
        if value['previous'] != (tail[1] if tail else None):
            raise ValueError('SEGMENT_CHAIN_MISMATCH')
        count = self.db.execute('SELECT count(*) FROM received_records').fetchone()[0]
        live_count = self.db.execute('SELECT count(*) FROM live_records').fetchone()[0]
        if count + live_count + len(value['records']) > self.store.max_records:
            raise RuntimeError('EVIDENCE_RECORD_CAP_REACHED')
        self.store._check_wal_cap()
        with self.store.book.transaction():
            self.db.execute('INSERT INTO received_segments VALUES(?,?,?,?)', (seal, stream, value['segment'], canonical(value)))
            seq, session, last_at = (tail[0], tail[2], tail[3]) if tail else (0, None, None)
            for row in value['records']:
                seq, session, last_at = self._record(stream, seal, row, seq, session, last_at)
        self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        return dict(seal=seal, records=len(value['records']), duplicate=False, qualified=False)

    def _record(self, stream, seal, row, previous_seq, session, last_at):
        if (not isinstance(row, dict) or type(row.get('seq')) is not int
                or row['seq'] != previous_seq + 1 or not isinstance(row.get('session'), str)
                or not 0 < len(row['session']) <= 128):
            raise ValueError('STREAM_SEQUENCE_OR_SESSION_INVALID')
        kind, seq = row.get('kind'), row['seq']
        fields = {'wire': {'received_at', 'wire_hex', 'wire_sha256', 'schema'},
                  'gap': {'observed_at', 'reason', 'lost_records'},
                  'restart': {'observed_at', 'previous_session', 'lost_records'},
                  'snapshot': {'evaluated_at', 'native_state', 'quote', 'bar_refs'}}
        if kind not in fields or set(row) != fields[kind] | {'kind', 'seq', 'session'}:
            raise ValueError('UNKNOWN_RECEIVED_RECORD_SCHEMA')
        at = receipt_time(row['received_at' if kind == 'wire' else 'evaluated_at' if kind == 'snapshot' else 'observed_at'])
        if last_at is not None and at < last_at:
            raise ValueError('RECEIPT_CLOCK_REGRESSION')
        if kind == 'restart':
            if row['previous_session'] != session or row['session'] == session:
                raise ValueError('RESTART_PREDECESSOR_REQUIRED')
        elif session is not None and row['session'] != session:
            raise ValueError('UNDECLARED_RESTART')
        if kind in ('gap', 'restart'):
            lost = row['lost_records']
            if lost is not None and (type(lost) is not int or lost < 0):
                raise ValueError('INVALID_GAP_LOSS')
            if kind == 'gap' and (not isinstance(row['reason'], str) or not row['reason']):
                raise ValueError('GAP_REASON_REQUIRED')
        source_hash = None
        bars = None
        if kind == 'wire':
            if row['schema'] not in self.decoders:
                raise ValueError('UNKNOWN_NATIVE_WIRE_SCHEMA')
            if not isinstance(row['wire_hex'], str) or len(row['wire_hex']) > MAX_WIRE * 2:
                raise ValueError('RAW_WIRE_BYTE_CAP')
            wire = bytes.fromhex(row['wire_hex'])
            if not wire or hashlib.sha256(wire).hexdigest() != row['wire_sha256']:
                raise ValueError('RAW_WIRE_HASH_MISMATCH')
            bars = self.decoders[row['schema']](wire)
            if not isinstance(bars, list) or not 0 < len(bars) <= 1000:
                raise ValueError('INVALID_DECODED_BARS')
            source_hash = self.store.book.source(dict(stream=stream, occurrence_seq=seq,
                session=row['session'], schema=row['schema'], wire_hex=row['wire_hex'],
                wire_sha256=row['wire_sha256'], transport_received_at=row['received_at']),
                event_time=None, received_at=at, quality='sealed_transport_receipt; source_qualification_unverified')
        self.db.execute('INSERT INTO received_records VALUES(?,?,?,?,?,?,?,?)',
            (stream, seq, seal, kind, row['session'], at, source_hash, canonical(row)))
        for index, item in enumerate(bars or []):
            if not isinstance(item, dict) or set(item) != {'bar', 'final', 'final_basis'}:
                raise ValueError('INVALID_DECODED_FINAL_SCHEMA')
            bar, final = item['bar'], item['final']
            validate_bar(bar)
            if final is not None and type(final) is not bool:
                raise ValueError('FINAL_STATUS_REQUIRED')
            if final is True and (not isinstance(item['final_basis'], str) or not item['final_basis']):
                raise ValueError('FINAL_DEFINITION_REQUIRED')
            if bar['updated'] > at or final is True and bar['start'] + bar['period'] > at:
                raise ValueError('BAR_NOT_AVAILABLE_AT_RECEIPT')
            prior = self.db.execute('SELECT payload FROM received_bars WHERE stream=? AND period=? AND start=? ORDER BY seq DESC,bar_index DESC LIMIT 1',
                (stream, bar['period'], bar['start'])).fetchone()
            previous = json.loads(prior[0]) if prior else None
            same_revision = (previous is not None and canonical(item) == canonical(
                {key:previous[key] for key in ('bar', 'final', 'final_basis')}))
            revision = previous['revision'] + (0 if same_revision else 1) if previous else 1
            payload = dict(item, source_hash=source_hash, received_at=row['received_at'],
                           occurrence_seq=seq, bar_index=index, revision=revision)
            self.db.execute('INSERT INTO received_bars VALUES(?,?,?,?,?,?,?,?,?)',
                (stream, seq, index, bar['period'], bar['start'], revision,
                 None if final is None else int(final), at if final is True else None, canonical(payload)))
        if kind == 'snapshot':
            self._snapshot(stream, row, at)
        return seq, row['session'], at

    def _snapshot(self, stream, row, at):
        state, refs = row['native_state'], row['bar_refs']
        if (not isinstance(state, dict) or not isinstance(refs, dict)
                or not _number(row['quote']) or row['quote'] <= 0
                or set(refs) != set(state).intersection(GROUPS)
                or not {'minute_bars', 'five_bars'} <= set(refs)):
            raise ValueError('SNAPSHOT_BAR_COVERAGE_REQUIRED')
        links, hashes, known = [], [], []
        for group, pointers in refs.items():
            bars = state[group]
            if not isinstance(bars, list) or not isinstance(pointers, list) or len(bars) != len(pointers):
                raise ValueError('SNAPSHOT_BAR_COVERAGE_REQUIRED')
            seen = set()
            for bar, ref in zip(bars, pointers):
                if (not isinstance(ref, dict) or set(ref) != {'seq', 'index'}
                        or type(ref['seq']) is not int or type(ref['index']) is not int
                        or ref['seq'] >= row['seq']):
                    raise ValueError('EXACT_OCCURRENCE_REFERENCE_REQUIRED')
                source = self.db.execute('SELECT payload,known_at FROM received_bars WHERE stream=? AND seq=? AND bar_index=?',
                    (stream, ref['seq'], ref['index'])).fetchone()
                if not source:
                    raise ValueError('BAR_OCCURRENCE_NOT_FOUND')
                item = json.loads(source[0]); decoded = item['bar']
                if source[1] is None or source[1] > at:
                    raise ValueError('FINAL_RECEIPT_UNVERIFIED')
                if (not isinstance(bar, dict)
                        or canonical({k:bar.get(k) for k in BAR_FIELDS}) != canonical(decoded)
                        or decoded['period'] != GROUPS[group] or decoded['start'] in seen):
                    raise ValueError('SNAPSHOT_BAR_REVISION_MISMATCH')
                if bar.get('received_at') not in (None, source[1]):
                    raise ValueError('SNAPSHOT_RECEIPT_MISMATCH')
                latest = self.db.execute('SELECT seq,bar_index FROM received_bars WHERE stream=? AND period=? AND start=? AND seq<? ORDER BY seq DESC,bar_index DESC LIMIT 1',
                    (stream, decoded['period'], decoded['start'], row['seq'])).fetchone()
                if latest != (ref['seq'], ref['index']):
                    raise ValueError('STALE_BAR_REVISION_AT_SNAPSHOT')
                seen.add(decoded['start'])
                links.append(dict(group=group, snapshot_bar_hash=digest(bar), **item, known_at=source[1]))
                hashes.append(item['source_hash']); known.append(source[1])
        if not links:
            raise ValueError('SNAPSHOT_HAS_NO_FINAL_BARS')
        gaps = [json.loads(r[0]) for r in self.db.execute(
            "SELECT payload FROM received_records WHERE stream=? AND seq<=? AND kind IN ('gap','restart') ORDER BY seq", (stream, row['seq']))]
        payload = dict(stream=stream, seq=row['seq'], evaluated_at=at,
                       state_hash=digest(state), quote=row['quote'], links=links,
                       input_sources=sorted(set(hashes)), available_at=max(known),
                       gap_provenance=gaps, receipt_continuity_verified=False,
                       source_blockers=BLOCKERS, qualified=False)
        key = digest(payload)
        self.db.execute('INSERT INTO received_snapshots VALUES(?,?,?,?)', (key, stream, row['seq'], canonical(payload)))

    def snapshots(self):
        return [dict(snapshot_hash=r[0], **json.loads(r[1])) for r in self.db.execute(
            'SELECT snapshot_hash,payload FROM received_snapshots ORDER BY stream,seq')]


def snapshot_binding(db, key, state, quote, at):
    """Read-only exact binding; optional live envelopes cannot rewrite snapshots."""
    row = db.execute('SELECT payload FROM received_snapshots WHERE snapshot_hash=?', (key,)).fetchone()
    if not row:
        raise ValueError('RECEIVED_SNAPSHOT_NOT_FOUND')
    value = json.loads(row[0])
    if value['state_hash'] != digest(state) or value['quote'] != quote or value['evaluated_at'] != at:
        raise ValueError('RECEIVED_SNAPSHOT_CAUSAL_MISMATCH')
    return value
