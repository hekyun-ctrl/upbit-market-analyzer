import json
import sqlite3
import tempfile
import unittest
from dataclasses import replace, asdict
from pathlib import Path
from unittest.mock import patch
from lbank_zb.core import D, Costs, Position, dumps, plan
from lbank_zb.live import LiveEngine, PRODUCT
from lbank_zb.journal import EventStore, replay_events
from lbank_zb.delivery import Dispatcher
from lbank_zb.service import native_inputs, EngineThread
from lbank_zb.public import PublicClient
from lbank_zb.store import snapshot, restore
from lbank_native_candles import NativeCandleStore
from test_core import C, bar, candidate

T = 150000


def engine():
    e = LiveEngine(C)
    e.qualify({k: True for k in e.qualification})
    e.one = [bar(t) for t in range(T-3600, T, 60)]
    e.five = [bar(t, 300, v='5') for t in range(T-450*300, T, 300)]
    e.day = (T+32400)//86400
    e.pending = [replace(candidate(), signal_time=T, origin=T-300)]
    return e


def quote(now=T+60, last='100', bid='99.99', ask='100.01', qty='10'):
    return {'product': PRODUCT, 'received': now, 'last': last,
            'contract_multiplier': '1', 'volume_unit': 'BTC',
            'book': {'asks': [{'price': ask, 'volume': qty}],
                     'bids': [{'price': bid, 'volume': qty}]}}


class LiveTests(unittest.TestCase):
    def test_no_minute_open_fill_in_live_mode(self):
        e = engine()
        e.ingest(bar(T+60), T+120)
        self.assertIsNone(e.position)
        self.assertEqual(len(e.pending), 1)
        e.ingest(bar(T+120,l='95'),T+180)
        self.assertFalse(e.pending)
        self.assertEqual(e.events[-1]['reason'],'STOP_BEFORE_FILL')

    def test_book_vwap_fill_has_no_double_entry_slippage(self):
        e = engine()
        e.costs = replace(C, slip_bps=D(2))
        e.quote(quote(), T+60)
        self.assertIsNotNone(e.position)
        self.assertEqual(e.position.plan.entry, D('100.01'))
        self.assertLessEqual(e.position.plan.risk, D('2.5'))
        self.assertEqual(e.counts['READY'], 1)
        # Live quote can arrive just after a boundary before the closed bar.
        e.quote(quote(T+61),T+61)
        e.ingest(bar(T,received=T+62),T+62)
        self.assertEqual(e.one[-1].end,T+60)

    def test_stale_wrong_crossed_and_depth_unknown_rejected(self):
        for q in [quote(T+54), {**quote(), 'product': 'spot'}, quote(bid='101'),
                  {**quote(), 'volume_unit': 'UNKNOWN'}]:
            e = engine()
            with self.assertRaises(ValueError):
                e.quote(q, T+60)
            self.assertIsNone(e.position)

    def test_insufficient_size_never_revives_next_quote(self):
        e = engine()
        e.quote(quote(qty='.000001'), T+60)
        self.assertFalse(e.pending)
        self.assertEqual(e.events[-1]['reasons'], ['INSUFFICIENT_DEPTH'])
        e.quote(quote(T+61), T+61)
        self.assertIsNone(e.position)

    def test_unknown_qualification_blocks_entry_but_not_risk(self):
        e = engine()
        e.qualify({})
        e.quote(quote(), T+60)
        self.assertIsNone(e.position)
        e = engine()
        e.quote(quote(), T+60)
        e.qualify({})
        e.review = True
        e.quote(quote(T+61, '90', '89.9', '90'), T+61)
        self.assertIsNone(e.position)
        self.assertEqual(e.closed[0].events[-1]['kind'], 'HARD_STOP')
        self.assertEqual(e.closed[0].events[-1]['price'], '89.9')

    def test_partial_uses_actual_depth_quantity_then_funding_remaining(self):
        e = engine()
        e.quote(quote(), T+60)
        original = e.position.remaining
        e.quote(quote(T+61, '110', '109.99', '110'), T+61)
        self.assertTrue(e.position.partial)
        remaining = e.position.remaining
        self.assertEqual(remaining % C.step, 0)
        ev = {'product': PRODUCT, 'time': T+62, 'received': T+62, 'rate': '.001',
              'price': '110', 'price_basis':'MARK', 'settled': True, 'source': 'synthetic_settlement'}
        e.funding(ev, T+62)
        self.assertEqual(e.position.funding, remaining * D('.11'))
        self.assertLess(remaining, original)
        before = e.position.cash
        e.funding(ev, T+62)
        self.assertEqual(e.position.cash, before)

    def test_unconfirmed_and_late_funding_not_fabricated(self):
        e = engine()
        e.quote(quote(), T+60)
        e.quote(quote(T+62, '110', '109.99', '110'), T+62)
        ev = {'product': PRODUCT, 'time': T+61, 'received': T+63, 'rate': '.001',
              'price': '110', 'price_basis':'MARK', 'settled': True, 'source': 'fixture'}
        e.funding(ev, T+63)
        self.assertEqual(e.position.funding, 0)
        self.assertIn('FUNDING_UNVERIFIED', e.position.health)
        with self.assertRaises(ValueError):
            e.funding({**ev, 'settled': False}, T+63)

    def test_ttl_cancel_before_fill_and_quote_gap(self):
        e = engine()
        e.advance(T+121)
        self.assertFalse(e.pending)
        self.assertEqual(e.events[-1]['kind'], 'EXPIRED')
        e = engine()
        e.quote(quote(T+1, '96', '95.9', '96'), T+1)
        self.assertFalse(e.pending)
        self.assertIsNone(e.position)
        e = engine()
        e.quote(quote(), T+60)
        e.settlement_schedule = [T+61]
        e.advance(T+66)
        self.assertIn('FUNDING_UNVERIFIED', e.position.health)
        self.assertIn('QUOTE_GAP', e.position.health)

    def test_structure_exit_waits_for_real_quote_and_survives_rebound(self):
        e=engine();e.quote(quote(),T+60)
        e.manage_five_position(bar(T+300,300,o='98',h='99',l='96',c='96.5'),D(2),'RANGE',T+600)
        self.assertIsNotNone(e.position)
        self.assertEqual(e.pending_exit['reason'],'STRUCTURE_INVALIDATED')
        e=restore(snapshot(e))
        e.quote(quote(T+601,'100','99.99','100.01'),T+601)
        self.assertIsNone(e.position)
        self.assertEqual(e.closed[0].events[-1]['price'],'99.99')
        self.assertEqual(e.closed[0].events[-1]['decision_at'],T+600)

    def test_funding_coverage_requires_all_recorded_settlements(self):
        e=engine()
        record={'product':PRODUCT,'start':T,'end':T+120,'received':T+121,
                'settlements':[T+60],'source':'synthetic_complete_schedule','complete':True}
        with self.assertRaisesRegex(ValueError,'MISSING_SETTLEMENT'):e.coverage(record,T+121)
        e.funding({'product':PRODUCT,'time':T+60,'received':T+61,'rate':'.001',
                   'price':'100','price_basis':'MARK','settled':True,'source':'fixture'},T+61)
        e.coverage(record,T+121)
        self.assertEqual(len(e.funding_coverage),1)

    def test_store_restart_rolls_back_and_exact_replay(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = tmp+'/events.sqlite'
            s = EventStore(path, engine())
            payload = quote()
            s.consume('q1', 'quote', payload, T+60)
            before = snapshot(s.engine)
            self.assertFalse(s.consume('q1', 'quote', payload, T+60))
            with self.assertRaises(ValueError):
                s.consume('q1', 'quote', quote(last='101'), T+60)
            s.close()
            s = EventStore(path, LiveEngine(C))
            self.assertEqual(snapshot(s.engine), before)
            self.assertIsInstance(s.engine, LiveEngine)
            s.db.execute("CREATE TRIGGER fail BEFORE INSERT ON external_inputs BEGIN SELECT RAISE(ABORT,'fixture'); END")
            with self.assertRaises(sqlite3.IntegrityError):
                s.consume('q2', 'quote', quote(T+61, '90', '89.9', '90'), T+61)
            self.assertEqual(snapshot(s.engine), before)
            self.assertEqual(s.db.execute('SELECT count(*) FROM archived_positions').fetchone()[0], 0)
            s.close()
        events = [{'id':'b1', 'kind':'bar', 'received':60, 'payload':json.loads(dumps(bar()))}]
        self.assertEqual(replay_events(events,C)['event_hash'],replay_events(events,C)['event_hash'])

    def test_sender_disabled_expired_unknown_and_urgent_retry(self):
        s = EventStore(':memory:', engine())
        s.consume('q1', 'quote', quote(), T+60)
        sent = []
        w = Dispatcher(s, lambda m: sent.append(m) or True)
        self.assertEqual(w.drain(T+60), 0)
        self.assertEqual(sent, [])
        w.enabled = True
        w.activate_new(T+60)
        self.assertEqual(w.drain(T+66), 0)  # quote stale even before TTL
        self.assertEqual(s.db.execute("SELECT status FROM outbox WHERE kind='READY'").fetchone()[0], 'STALE_QUOTE')
        s.consume('q2', 'quote', quote(T+67, '90', '89.9', '90'), T+67)
        w.activate_new(T+67)
        w.sender = lambda m: sent.append(m)  # ambiguous response
        self.assertEqual(w.drain(T+67), 1)
        self.assertEqual(w.drain(T+68), 0)
        w.sender = lambda m: sent.append(m) or True
        self.assertEqual(w.drain(T+72), 1)
        self.assertIn('이전 응답 미확인', sent[-1])
        self.assertIn(str(T+67), s.db.execute("SELECT payload FROM outbox WHERE kind='HARD_STOP'").fetchone()[0])
        s.close()

    def test_new_sender_does_not_enable_old_disabled_backlog(self):
        s = EventStore(':memory:', engine())
        s.consume('q1', 'quote', quote(), T+60)
        w = Dispatcher(s, lambda m: self.fail('old backlog sent'), enabled=True)
        w.activate_new(T+61)
        self.assertEqual(w.drain(T+61), 0)
        s.close()

    def test_arrival_provenance_read_only_and_derived_label(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = tmp+'/native.sqlite'
            n = NativeCandleStore(p)
            n.db.execute("INSERT INTO native_bars VALUES(60,60,100,101,99,100,1,100,119,'stream_closed')")
            n.db.commit()
            metadata = {'exchange':'lbank','market_type':'linear_usdt_perpetual','symbol':'BTCUSDT',
                        'settlement_asset':'USDT','price_basis':'LAST','volume_unit':'BTC',
                        'contract_multiplier':'1','source':'fixture'}
            self.assertEqual(native_inputs(p, metadata), [])  # no invented historical receipt
            n.db.execute("INSERT INTO native_arrivals VALUES(60,60,121,'lbank_native_stream_1m')")
            n.db.commit()
            rows = native_inputs(p, metadata)
            self.assertEqual(len(rows), 1)
            self.assertEqual(rows[0].received, 121)
            self.assertEqual(native_inputs(p, metadata, [121,120,60]), [])
            n.db.close()

    def test_public_parser_uses_current_interval_not_eight_hour_constant(self):
        i = {'symbol':'BTCUSDT','baseCurrency':'BTC','clearCurrency':'USDT','priceCurrency':'USDT',
             'needSuspend':0,'volumeMultiple':'1','priceTick':'.1','volumeTick':'.0001',
             'minOrderVolume':'.0001','minOrderCost':'1'}
        m = {'symbol':'BTCUSDT','instrumentStatus':'2','lastTime':T,'lastPrice':'100000','markedPrice':'100000',
             'nextFeeTime':(T+3600)*1000,'positionFeeTime':3600,'fundingRate':'.0001'}
        class Fixture(PublicClient):
            def get(self, name, parameters):
                return [i if name=='instrument' else m], {'url':'synthetic://'+name,'received':T}
        e = Fixture(clock=lambda:T).evidence()
        self.assertEqual(len(e['settlement_schedule']),6)
        self.assertEqual(e['costs']['funding_per_btc'],D(60))
        self.assertFalse(e['qualification']['independent_5m'])
        self.assertFalse(e['record']['funding_settlements_verified'])
        m['nextFeeTime']=0
        with self.assertRaises(ValueError):
            Fixture(clock=lambda:T).evidence()


if __name__=='__main__':
    unittest.main()
