"""Regression tests for the independent public BTCUSDT candle comparison."""
import copy
import sqlite3
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import AsyncMock, patch

from lbank_native_candles import WS_URL
from lbank_zb.candle_evidence import (
    EVIDENCE_GRACE, REFERENCE_ROUTES, REFERENCE_URL, CandleAuditThread, ReferenceDisagreement,
    audit_status, capture_reference, closed_reply, compare, read_derived,
)
from lbank_zb.service import EngineThread
from lbank_zb.core import Bar, D, Costs, asdict
from lbank_zb.engine import Engine
from lbank_zb.journal import EventStore
from lbank_zb.live import LiveEngine


END = 1795307100
TICKET = '2000000052'
# Public LBank reply to BTCUSDT_5m and the distinct 1m roll-up at 09:25 KST.
FRAME = {'x': 2, 'z': 3, 'y': TICKET, 'w': END*1000+830, 'd': [{
    'a': 'BTCUSDT', 'b': '5m', 'c': END-300, 'k': END,
    'd': '60307.9', 'f': '60319.8', 'g': '60279.6', 'e': '60284.2',
    'h': '1.5108', 'i': '91085.96203',
}]}
DERIVED = dict(open=60307.9, high=60319.8, low=60279.6, close=60284.2,
               volume=1.5108000000000001, turnover=91085.96203)
COSTS = Costs(None, None, D(2), None, None, None, None)


class CandleEvidenceTests(unittest.TestCase):
    def setUp(self):
        folder = tempfile.TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.audit = Path(folder.name)/'audit.sqlite3'
        self.db = sqlite3.connect(self.audit)
        self.addCleanup(self.db.close)
        self.db.execute('CREATE TABLE candle_checks(start INTEGER,status TEXT,volume_ok INTEGER,payload TEXT)')

    def insert(self, start, status='matched', volume_ok=True):
        with self.db:
            self.db.execute('INSERT INTO candle_checks VALUES(?,?,?,?)', (start, status, volume_ok, '{}'))

    def test_compare_actual_exchange_5m_with_distinct_1m_aggregation(self):
        ref = closed_reply(FRAME, TICKET, END, END*1000+882)
        self.assertEqual(ref['volume'], D('1.5108'))
        self.assertEqual(compare(ref, DERIVED), ('matched', True))
        self.assertEqual(compare(ref, dict(DERIVED, high=60319.7)), ('ohlc_mismatch', False))
        self.assertEqual(compare(ref, dict(DERIVED, volume=1.5)), ('volume_mismatch', False))
        self.assertEqual(compare(ref, dict(DERIVED, turnover=91085.0)), ('turnover_mismatch', False))
        self.assertEqual(compare(ref, None), ('native_derived_missing', False))

    def test_rejects_forming_unmatched_or_wrong_stream_frames(self):
        for mutate in (
            lambda f: f.update(z=4),
            lambda f: f.update(y='wrong'),
            lambda f: f.update(w=END*1000-1),
            lambda f: f['d'][0].update(k=END-1),
            lambda f: f['d'][0].update(k=END+1),
            lambda f: f['d'][0].update(b='1m'),
            lambda f: f['d'][0].update(a='mBTCUSDT'),
            lambda f: f['d'][0].update(c=END),
            lambda f: f['d'][0].update(h='nan'),
        ):
            frame = copy.deepcopy(FRAME)
            mutate(frame)
            self.assertIsNone(closed_reply(frame, TICKET, END, END*1000+882))
        self.assertIsNone(closed_reply(FRAME, TICKET, END, (END+30)*1000))

    def test_turnover_checks_quantity_scale_even_when_sources_agree(self):
        ref = closed_reply(FRAME, TICKET, END, END*1000+882)
        self.assertEqual(compare(dict(ref, turnover=D('1.5108')), dict(DERIVED, turnover=1.5108)),
                         ('turnover_outside_trade_price_range', False))
        self.assertEqual(compare(dict(ref, volume=D(0), turnover=D(0)), dict(DERIVED, volume=0, turnover=0)),
                         ('matched', False))

    def test_progress_counts_only_consecutive_matches_through_latest_close(self):
        for i in range(1, 6):
            self.insert(END-i*300)
        status = audit_status(self.audit, END+8)
        self.assertEqual(status['consecutive_matches'], 5)
        self.assertEqual(status['required_consecutive'], 0)
        self.assertEqual(status['verification_policy'], 'latest_independent_closed_ohlcv_exact_match')
        self.assertTrue(status['independent_5m'])
        self.assertFalse(audit_status(self.audit, END+300)['independent_5m'])
        self.insert(END, 'closed_reference_missing', False)
        self.assertEqual(audit_status(self.audit, END+301)['consecutive_matches'], 0)
        self.assertEqual(audit_status(self.audit, END+301)['last_volume_match_start'], END-300)
        self.insert(END+300, 'volume_mismatch', False)
        self.assertIsNone(audit_status(self.audit, END+601)['last_volume_match_start'])

    def test_one_latest_matching_close_needs_no_long_streak(self):
        self.insert(END-600, 'volume_mismatch', False)
        self.insert(END-300)
        status = audit_status(self.audit, END+8)
        self.assertEqual(status['matched_bars'], 1)
        self.assertEqual(status['consecutive_matches'], 1)
        self.assertTrue(status['independent_5m'])
        self.insert(END, 'volume_mismatch', False)
        self.assertFalse(audit_status(self.audit, END+301)['independent_5m'])
        self.insert(END+300, 'ohlc_mismatch', False)
        self.assertFalse(audit_status(self.audit, END+601)['independent_5m'])

    def test_derived_comparison_requires_recorded_seal_and_source(self):
        native = self.audit.with_name('native.sqlite3')
        db = sqlite3.connect(native)
        self.addCleanup(db.close)
        with db:
            db.execute('CREATE TABLE native_bars(period,start,quality)')
            db.execute('CREATE TABLE native_arrivals(period,start,received,source)')
            db.execute('INSERT INTO native_bars VALUES(300,?,?)', (END-300, 'stream_closed'))
            self.assertIsNone(read_derived(native, END-300))
            db.execute('INSERT INTO native_arrivals VALUES(300,?,?,?)',
                       (END-300, END-1, 'lbank_native_1m_derived_5m'))
            db.commit()
            self.assertIsNone(read_derived(native, END-300))
            db.execute('UPDATE native_arrivals SET received=?', (END+1,))
            db.commit()
        self.assertEqual(read_derived(native, END-300)['received'], END+1)

    def test_research_volume_gate_uses_live_product_spec_and_match(self):
        self.insert(END-300)
        start = END+8
        engine = EngineThread(self.audit.with_name('missing.sqlite3'), audit_path=self.audit)
        engine.evidence = {'qualification': {'product': True, 'fees': False, 'funding_plan': False},
                           'metadata': {}, 'costs': asdict(COSTS), 'settlement_schedule': [],
                           'record': {'instrument': {'volumeMultiple': '1', 'priceTick': '.1', 'volumeTick': '.0001'}}}
        engine.last_evidence = start
        store = EventStore(':memory:', LiveEngine(COSTS))
        self.addCleanup(store.close)
        with patch.object(engine.client, 'evidence', side_effect=AssertionError('offline')):
            engine.poll(store, start)
            self.assertTrue(store.engine.qualification['candle_units'])
            self.assertTrue(store.engine.qualification['independent_5m'])
            self.assertIn('FEES_UNVERIFIED', store.engine.entry_blocks)
            engine.evidence['record']['instrument']['volumeMultiple'] = '.01'
            engine.poll(store, start+1)
            self.assertFalse(store.engine.qualification['candle_units'])
        self.assertIn('RAW5M_INDEPENDENCE_UNVERIFIED', store.engine.entry_blocks)
        self.assertIn('CANDLE_VOLUME_UNIT_UNVERIFIED', store.engine.entry_blocks)
        self.assertEqual(store.report()['outbox'], {})

    def test_latest_close_waits_for_its_own_audit_without_backdating(self):
        engine = EngineThread(self.audit.with_name('native.sqlite3'), audit_path=self.audit)
        costs = replace(COSTS, tick=D('.1'))
        engine.evidence = {'qualification': {'product': True, 'fees': True, 'funding_plan': True},
                           'metadata': {}, 'costs': asdict(costs), 'settlement_schedule': [],
                           'record': {'instrument': {'volumeMultiple': '1', 'priceTick': '.1', 'volumeTick': '.0001'}}}
        engine.last_evidence = END
        store = EventStore(':memory:', LiveEngine(costs))
        self.addCleanup(store.close)
        close = Bar(END-300, 300, D(100), D(101), D(99), D(100), D(5), END+1)
        # A match on the preceding bar does not authorize this one.
        self.insert(END-600)
        with patch('lbank_zb.service.native_inputs', return_value=[close]) as reads:
            engine.poll(store, END+2)
            self.assertEqual(store.engine.five, [])
            self.assertIsNone(getattr(store.engine, 'native_cursor', None))
            self.insert(END-300)
            engine.poll(store, END+5)
            self.assertIsNone(reads.call_args.args[2])
        self.assertEqual(store.engine.five, [close])
        self.assertTrue(store.engine.qualification['independent_5m'])
        self.assertEqual(store.engine.native_cursor, [END+1, END, 300])
        rows = store.db.execute(
            'SELECT kind,received FROM external_inputs ORDER BY rowid'
        ).fetchall()
        self.assertEqual([t for _, t in rows], sorted(t for _, t in rows))
        self.assertEqual([t for kind, t in rows if kind == 'bar'], [END+5])

    def test_pending_audit_has_a_deadline_and_mismatch_is_not_authorized(self):
        for status, offset in ((None, EVIDENCE_GRACE+1), ('volume_mismatch', 2)):
            with self.subTest(status=status):
                with self.db:
                    self.db.execute('DELETE FROM candle_checks')
                if status:
                    self.insert(END-300, status, False)
                engine = EngineThread(self.audit.with_name('native.sqlite3'), audit_path=self.audit)
                engine.evidence = {'qualification': {}, 'metadata': {}, 'costs': asdict(COSTS),
                                   'settlement_schedule': [], 'record': {'instrument': {
                                       'volumeMultiple': '1', 'priceTick': '.1', 'volumeTick': '.0001'}}}
                engine.last_evidence = END
                store = EventStore(':memory:', LiveEngine(COSTS))
                self.addCleanup(store.close)
                close = Bar(END-300, 300, D(100), D(101), D(99), D(100), D(5), END+1)
                with patch('lbank_zb.service.native_inputs', return_value=[close]):
                    engine.poll(store, END+offset)
                self.assertEqual(store.engine.five, [close])
                self.assertIn('RAW5M_INDEPENDENCE_UNVERIFIED', store.engine.entry_blocks)

    def test_research_crosscheck_uses_the_same_volume_tolerance_as_the_venue_audit(self):
        costs = replace(COSTS, tick=D('.1'))
        for volume, matched in ((DERIVED['volume'], True), (1.5109, False)):
            with self.subTest(volume=volume):
                engine = Engine(costs)
                for start, qty in zip(range(END-300, END, 60), ('.2', '.3', '.4', '.5', '.1108')):
                    engine.ingest(Bar(start, 60, D(100), D(101), D(99), D(100), D(qty),
                                      start+60), start+60)
                close = Bar(END-300, 300, D(100), D(101), D(99), D(100), D(str(volume)), END)
                with patch.object(engine.higher_timeframes, 'ingest') as context:
                    engine.ingest(close, END)
                context.assert_called_once_with(close, END, matched)
                self.assertEqual(END-300 in engine.crosscheck_failures, not matched)


class CandleConnectionTests(unittest.IsolatedAsyncioTestCase):
    async def test_collects_a_close_when_either_published_route_misses_it(self):
        session = object()
        proof = closed_reply(FRAME, TICKET, END, END*1000+882, WS_URL)
        for missing in (None, TimeoutError()):
            capture = AsyncMock(side_effect=[missing, proof])
            with patch('lbank_zb.candle_evidence.capture_endpoint', capture):
                self.assertEqual(await capture_reference(session, END), dict(proof, corroborated_routes=[WS_URL]))
            self.assertEqual([call.args for call in capture.await_args_list],
                             [(session, endpoint, END, lead) for endpoint, lead in REFERENCE_ROUTES])

    async def test_two_agreeing_responses_are_one_candle(self):
        first = closed_reply(FRAME, TICKET, END, END*1000+882)
        second = dict(first, source=WS_URL, server_ms=first['server_ms']+1)
        with patch('lbank_zb.candle_evidence.capture_endpoint', AsyncMock(side_effect=[first, second])):
            self.assertEqual(await capture_reference(object(), END),
                             dict(second, corroborated_routes=[REFERENCE_URL, WS_URL]))

    async def test_disagreement_stays_out_of_the_consecutive_count(self):
        first = closed_reply(FRAME, TICKET, END, END*1000+882)
        second = dict(first, source=WS_URL, volume=D('1.5109'))
        with patch('lbank_zb.candle_evidence.capture_endpoint', AsyncMock(side_effect=[first, second])):
            with self.assertRaises(ReferenceDisagreement):
                await capture_reference(object(), END)
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary)/'audit.sqlite3'
            db = sqlite3.connect(path)
            try:
                db.execute('CREATE TABLE candle_checks(start INTEGER,status TEXT,volume_ok INTEGER,payload TEXT)')
                db.execute('INSERT INTO candle_checks VALUES(?,?,?,?)', (END-600, 'matched', True, '{}'))
                audit = CandleAuditThread(path.with_name('native.sqlite3'), path)
                with patch('lbank_zb.candle_evidence.capture_reference', AsyncMock(side_effect=ReferenceDisagreement)):
                    await audit.check_close(db, object(), END)
                result = audit_status(path, END+1)
                self.assertEqual(result['consecutive_matches'], 0)
                self.assertEqual(result['matched_bars'], 1)
                self.assertEqual(result['last_status'], 'reference_disagreement')
                self.assertIsNone(result['last_volume_match_start'])
            finally:
                db.close()
