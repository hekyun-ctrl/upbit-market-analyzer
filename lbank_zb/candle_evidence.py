"""Compare LBank's own closed 5m snapshots with the separately sealed 1m feed.

The public futures web client uses topic 2, and its linear candle adapter
reads h as contract volume and multiplies by volumeMultiple for BTC display.
Topic 2 pushes in this subscription can be 1m, so only ticketed 5m replies
captured at or after the target close qualify here.
"""
import asyncio
from contextlib import closing
import json
import logging
from pathlib import Path
import sqlite3
import threading
import time

import aiohttp

from lbank_native_candles import WS_URL, decode_frame
from .core import D, dec, dumps

LOG = logging.getLogger('lbank_zb_candle_evidence')
REFERENCE_URL = 'wss://uuws.lbank.com/ws/v3'
CHART_URL = 'https://www.lbank.com/futures/btcusdt'
DERIVED_SOURCE = 'lbank_native_1m_derived_5m'
PERIOD = 300
WARMUP_BARS = 450
EVIDENCE_GRACE = 15
CONNECTION_LEAD = 18
FIELDS = ('open', 'high', 'low', 'close', 'volume', 'turnover')
PRICE_TICK = D('.1')
VOLUME_TICK = D('.0001')
VOLUME_EPSILON = D('.00000001')
TURNOVER_EPSILON = D('.000001')
REFERENCE_ROUTES = ((REFERENCE_URL, .7), (WS_URL, .55))


class ReferenceDisagreement(ValueError):
    """Two independently requested LBank closes disagree at the same boundary."""


def closed_reply(frame, ticket, end, received_ms, endpoint=REFERENCE_URL):
    if (frame.get('x'), frame.get('z'), frame.get('y')) != (2, 3, ticket):
        return None
    server_ms, rows = frame.get('w'), frame.get('d')
    if (type(server_ms) is not int or not end*1000 <= server_ms <= (end+EVIDENCE_GRACE)*1000
            or not 0 <= received_ms-server_ms <= EVIDENCE_GRACE*1000
            or not isinstance(rows, list) or len(rows) != 1):
        return None
    raw = rows[0]
    if (not isinstance(raw, dict) or (raw.get('a'), raw.get('b'), raw.get('c')) != ('BTCUSDT', '5m', end-PERIOD)
            or type(raw.get('k')) is not int or not end <= raw['k'] <= server_ms//1000):
        return None
    try:
        values = [dec(raw[k]) for k in 'dfgehi']
    except (ArithmeticError, KeyError, ValueError):
        return None
    o, h, l, c, volume, turnover = values
    if (min(o, h, l, c) <= 0 or h < max(o, c, l) or l > min(o, c, h)
            or any(p % PRICE_TICK for p in (o, h, l, c))
            or volume < 0 or volume % VOLUME_TICK or turnover < 0):
        return None
    return {'start': raw['c'], 'updated': raw['k'], 'server_ms': server_ms,
            'received_ms': received_ms, 'ticket': ticket, 'source': endpoint,
            **dict(zip(FIELDS, values))}


def compare(reference, derived):
    if derived is None:
        return 'native_derived_missing', False
    if any(reference[key] != dec(derived[key]) for key in FIELDS[:4]):
        return 'ohlc_mismatch', False
    volume, turnover = reference['volume'], reference['turnover']
    if abs(volume-dec(derived['volume'])) > VOLUME_EPSILON:
        return 'volume_mismatch', False
    if abs(turnover-dec(derived['turnover'])) > TURNOVER_EPSILON:
        return 'turnover_mismatch', False
    # The EngineThread checks the active instrument specification before
    # accepting this BTC volume check.
    if not reference['low']*volume-TURNOVER_EPSILON <= turnover <= reference['high']*volume+TURNOVER_EPSILON:
        return 'turnover_outside_trade_price_range', False
    return 'matched', volume > 0


def read_derived(path, start):
    if not Path(path).is_file():
        return None
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True, timeout=1)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute("""SELECT b.*, a.received FROM native_bars b
          JOIN native_arrivals a USING(period,start) WHERE b.period=300 AND b.start=?
          AND b.quality='stream_closed' AND a.source=? AND a.received>=b.start+300""",
                         (start, DERIVED_SOURCE)).fetchone()
    return dict(row) if row else None


def audit_status(path, now):
    latest = int(now//PERIOD)*PERIOD-PERIOD
    result = {'required_consecutive': WARMUP_BARS, 'consecutive_matches': 0, 'matched_bars': 0,
              'last_checked_start': None, 'last_status': 'awaiting_capture',
              'last_volume_match_start': None, 'independent_5m': False}
    if not Path(path).is_file():
        return result
    uri = Path(path).resolve().as_uri() + '?mode=ro'
    db = sqlite3.connect(uri, uri=True, timeout=1)
    try:
        rows = db.execute("""SELECT start,status,volume_ok FROM candle_checks
          WHERE start>=? AND start<=? ORDER BY start DESC""",
                          (latest-7*86400, latest)).fetchall()
    finally:
        db.close()
    if not rows:
        return result
    result['last_checked_start'], result['last_status'], _ = rows[0]
    result['matched_bars'] = sum(status == 'matched' for _,status,_ in rows)
    for start, status, _ in rows:
        if status != 'matched' or start != latest-result['consecutive_matches']*PERIOD:
            break
        result['consecutive_matches'] += 1
    # A contradiction invalidates any older volume confirmation. A skipped
    # capture neither confirms nor contradicts the published quantity scale.
    for start, status, volume_ok in rows:
        if latest-start >= 86400 or status in (
            'ohlc_mismatch', 'volume_mismatch', 'turnover_mismatch', 'turnover_outside_trade_price_range',
            'reference_disagreement',
        ):
            break
        if volume_ok:
            result['last_volume_match_start'] = start
            break
    result['independent_5m'] = result['consecutive_matches'] >= WARMUP_BARS
    return result


async def capture_reference(session, end):
    # Both connections must be ready before rollover. Waiting for an empty
    # response before opening the second connection can miss the same old bar.
    results = await asyncio.gather(
        *(capture_endpoint(session, endpoint, end, lead) for endpoint, lead in REFERENCE_ROUTES),
        return_exceptions=True,
    )
    references = [result for result in results if isinstance(result, dict)]
    errors = [result for result in results if isinstance(result, Exception)]
    if not references:
        if len(errors) == len(results):
            raise errors[0]
        return None
    if len({tuple(reference[k] for k in FIELDS) for reference in references}) > 1:
        raise ReferenceDisagreement
    reference = max(references, key=lambda row: (row['updated'], row['server_ms']))
    return dict(reference, corroborated_routes=[row['source'] for row in references])


async def capture_endpoint(session, endpoint, end, lead):
    latest = None
    deadline = end + EVIDENCE_GRACE
    async with session.ws_connect(endpoint, timeout=4, max_msg_size=1024*1024) as ws:
        await asyncio.sleep(max(0, end-lead-time.time()))
        for serial in range(24):
            if time.time() >= deadline:
                break
            ticket = str(2000000001+serial)
            await ws.send_json({'x': 2, 'y': ticket, 'z': 1, 'a': {'i': 'BTCUSDT_5m'},
                                'e': json.dumps({'bvc': '202', 'isUsd': 1})})
            until = min(time.time()+.8, deadline)
            while time.time() < until:
                try:
                    msg = await ws.receive(timeout=max(.01, until-time.time()))
                except asyncio.TimeoutError:
                    break
                if msg.type not in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                    return latest
                frame = decode_frame(msg.data)
                if not isinstance(frame, dict) or (frame.get('x'), frame.get('z'), frame.get('y')) != (2, 3, ticket):
                    continue
                candidate = closed_reply(frame, ticket, end, int(time.time()*1000), endpoint)
                if candidate and (not latest or (candidate['updated'], candidate['server_ms'])
                                  >= (latest['updated'], latest['server_ms'])):
                    latest = candidate
                rows = frame.get('d')
                if (isinstance(rows, list) and len(rows) == 1 and isinstance(rows[0], dict)
                        and (rows[0].get('a'), rows[0].get('b'), rows[0].get('c')) == ('BTCUSDT', '5m', end)):
                    return latest
                break
            await asyncio.sleep(.22 if time.time() < end+2 else .5)
    return latest


class CandleAuditThread(threading.Thread):
    """A bounded public read and SQLite audit, separate from the alert loop."""

    def __init__(self, native_path, audit_path):
        super().__init__(name='lbank-zb-candle-evidence', daemon=True)
        self.native_path, self.audit_path = native_path, audit_path

    def run(self):
        try:
            asyncio.run(self.collect())
        except Exception as exc:
            LOG.error('ZB_CANDLE_AUDIT_STOPPED exception=%s', type(exc).__name__)

    async def collect(self):
        db = sqlite3.connect(self.audit_path, timeout=2)
        try:
            db.execute('PRAGMA journal_mode=WAL')
            db.execute("""CREATE TABLE IF NOT EXISTS candle_checks(
                start INTEGER PRIMARY KEY, status TEXT NOT NULL,
                volume_ok INTEGER NOT NULL, payload TEXT NOT NULL)""")
            LOG.info('ZB_CANDLE_AUDIT_STARTED sources=%s required_consecutive=%d',
                     ','.join(endpoint for endpoint, _ in REFERENCE_ROUTES), WARMUP_BARS)
            timeout = aiohttp.ClientTimeout(total=10, connect=8)
            async with aiohttp.ClientSession(trust_env=True, timeout=timeout,
                                             headers={'Origin': 'https://www.lbank.com'}) as session:
                while True:
                    end = (int(time.time())//PERIOD+1)*PERIOD
                    await asyncio.sleep(max(0, end-CONNECTION_LEAD-time.time()))
                    try:
                        await self.check_close(db, session, end)
                    except sqlite3.Error as exc:
                        LOG.warning('ZB_CANDLE_AUDIT_STORAGE_FAILURE exception=%s', type(exc).__name__)
        finally:
            db.close()

    async def check_close(self, db, session, end):
        start = end-PERIOD
        reference, derived = None, None
        status, volume_ok = 'closed_reference_missing', False
        try:
            reference = await capture_reference(session, end)
            if reference:
                while True:
                    derived = read_derived(self.native_path, start)
                    if derived is not None or time.time() >= end+EVIDENCE_GRACE:
                        break
                    await asyncio.sleep(.5)
                status, volume_ok = compare(reference, derived)
        except ReferenceDisagreement:
            status = 'reference_disagreement'
        except (OSError, TimeoutError, ValueError, KeyError, ArithmeticError, aiohttp.ClientError, sqlite3.Error) as exc:
            status = 'capture_failed'
            LOG.warning('ZB_CANDLE_AUDIT_CAPTURE_FAILURE exception=%s', type(exc).__name__)
        payload = {'reference': reference, 'derived': derived, 'chart': CHART_URL}
        with db:
            db.execute('INSERT OR IGNORE INTO candle_checks VALUES(?,?,?,?)',
                       (start, status, volume_ok, dumps(payload)))
            db.execute('DELETE FROM candle_checks WHERE start<?', (start-7*86400,))
        LOG.info('ZB_CANDLE_AUDIT_CHECK start=%d status=%s volume_scale_match=%s reference=%s',
                 start, status, volume_ok, dumps(reference))
