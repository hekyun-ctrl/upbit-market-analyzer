import json
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from lbank_zb.core import Bar, D, aggregate, digest, dumps
from lbank_zb.context import ContextFrame, HigherTimeframes, render_context
from lbank_zb.compare_context import AvailableMark, NavMetrics, cohorts, compare_rows, economic_events
from lbank_zb.engine import Engine
from lbank_zb.store import Store, snapshot, restore
from lbank_zb.replay import replay
from test_core import C, bar


def higher_bars(period, end=None, trend='flat', count=150):
    end = end if end is not None else count * period
    result = []
    for i in range(count):
        price = D(100 + i if trend == 'up' else 300 - i if trend == 'down' else 100)
        start = end - (count - i) * period
        result.append(Bar(start, period, price, price + 2, price - 2, price, D(1), start + period, source='synthetic_fixture'))
    return result


def trading_fixture():
    """An actual B strategy signal with TP1 and a subsequent runner stop."""
    changes = {
        449: dict(o='102', h='105', l='101', c='104.5'),
        450: dict(o='103', h='104', l='102', c='103.8'),
        451: dict(o='103.8', h='104', l='103.6', c='103.9'),
        452: dict(o='104', h='110', l='103.7', c='109.5'),
        453: dict(o='109', h='109', l='95', c='97'),
    }
    for i in range(454):
        args = changes.get(i, dict(o='100', h='102', l='98', c='100'))
        for j in range(5):
            yield bar(i * 300 + j * 60, **args)
        yield bar(i * 300, 300, v='5', **args)


class HigherTimeframeTests(unittest.TestCase):
    def test_complete_native_buckets_and_no_forming_bar(self):
        ctx = HigherTimeframes()
        for i in range(47):
            ctx.ingest(bar(i * 300, 300), (i + 1) * 300, True)
        self.assertEqual(len(ctx.frames['1h'].bars), 3)
        self.assertEqual(len(ctx.frames['4h'].bars), 0)
        self.assertEqual(ctx.snapshot(14100, D(100), 1)['frames']['4h']['state'], 'UNKNOWN')
        ctx.ingest(bar(14100, 300, h='999'), 14400, True)
        self.assertEqual(len(ctx.frames['1h'].bars), 4)
        self.assertEqual(len(ctx.frames['4h'].bars), 1)
        self.assertEqual(ctx.frames['4h'].bars[0].h, D(999))
        self.assertEqual(ctx.frames['4h'].bars[0].v, D(48))
        self.assertEqual(ctx.snapshot(14399, D(100), 1)['frames']['4h']['complete_bars'], 0)

    def test_gap_untrusted_and_new_complete_bucket(self):
        for omitted, untrusted in [(6, None), (None, 6)]:
            frame = ContextFrame(3600)
            for i in range(12):
                if i != omitted:
                    frame.ingest(bar(i * 300, 300), (i + 1) * 300, i != untrusted)
            self.assertEqual(frame.bars, [])
            for i in range(12, 24):
                frame.ingest(bar(i * 300, 300), (i + 1) * 300, True)
            self.assertEqual(len(frame.bars), 1)
            self.assertEqual(frame.bars[0].start, 3600)

    def test_wrong_product_period_and_future_input_rejected(self):
        frame = ContextFrame(3600)
        for invalid, now in [(replace(bar(0, 300), exchange='binance'), 300),
                             (bar(), 60), (bar(0, 300, received=301), 300)]:
            with self.assertRaises(ValueError):
                frame.ingest(invalid, now, True)
        self.assertIsNone(frame.last_input)

    def test_pivots_require_right_confirmation_and_known_at(self):
        frame = ContextFrame(3600)
        frame.bars = higher_bars(3600)
        frame.bars[145] = replace(frame.bars[145], l=D(95), h=D(108))
        frame.bars[148] = replace(frame.bars[148], h=D(120))
        before = frame.snapshot(150 * 3600, D(100))
        self.assertEqual(before['support']['price'], '95')
        self.assertEqual(before['resistance']['price'], '108')
        original = dumps(before)
        future = higher_bars(3600, end=151 * 3600, count=1)[0]
        frame.bars.append(replace(future, received=future.end + 20))
        self.assertEqual(dumps(frame.snapshot(150 * 3600, D(100))), original)
        self.assertEqual(frame.snapshot(future.end + 15, D(110))['resistance'], None)
        self.assertEqual(frame.snapshot(future.end + 20, D(110))['resistance']['price'], '120')

    def test_stale_warmup_zero_atr_and_no_synthetic_levels(self):
        frame = ContextFrame(3600)
        frame.bars = higher_bars(3600)
        end = frame.bars[-1].end
        self.assertEqual(frame.snapshot(end + 3615, D(100))['state'], 'RANGE')
        stale = frame.snapshot(end + 3616, D(100))
        self.assertEqual(stale['state'], 'UNKNOWN')
        self.assertIsNone(stale['support'])
        self.assertIsNone(stale['resistance'])
        frame.bars = frame.bars[:149]
        self.assertEqual(frame.snapshot(149 * 3600, D(100))['reason'], 'CONTEXT_WARMUP')
        frame.bars = [replace(b, h=b.c, l=b.c) for b in higher_bars(3600)]
        self.assertEqual(frame.snapshot(end, D(100))['reason'], 'ZERO_ATR')

    def test_one_hour_and_four_hour_can_disagree_without_gate(self):
        ctx = HigherTimeframes()
        ctx.frames['1h'].bars = higher_bars(3600, end=2160000, trend='down')
        ctx.frames['4h'].bars = higher_bars(14400, end=2160000, trend='up')
        row = ctx.snapshot(2160000, D(150), 1)
        self.assertEqual(row['relationship'], 'OPPOSITE_DIRECTIONS')
        self.assertEqual(row['frames']['1h']['alignment'], 'COUNTERTREND')
        self.assertEqual(row['frames']['4h']['alignment'], 'ALIGNED')
        text = render_context(row)
        self.assertIn('4시간', text)
        self.assertIn('1시간', text)
        self.assertIn('진입 허가/차단 조건 아님', text)

    def test_restart_retains_bars_partial_bucket_and_frozen_context(self):
        engine = Engine(C)
        for i in range(17):
            engine.higher_timeframes.ingest(bar(i * 300, 300), (i + 1) * 300, True)
        engine.signal_contexts['frozen'] = engine.higher_timeframes.snapshot(5100, D(100), 1)
        saved = snapshot(engine)
        resumed = restore(saved)
        self.assertEqual(snapshot(resumed), saved)
        for i in range(17, 48):
            for e in (engine, resumed):
                e.higher_timeframes.ingest(bar(i * 300, 300), (i + 1) * 300, True)
        self.assertEqual(snapshot(engine), snapshot(resumed))
        self.assertEqual(resumed.signal_contexts['frozen']['frames']['4h']['complete_bars'], 0)
        old = json.loads(saved)
        for k in ('context_mode', 'higher_timeframes', 'signal_contexts'):
            old.pop(k)
        self.assertEqual(restore(json.dumps(old)).higher_timeframes.frames['4h'].bars, [])

    def test_store_rolls_back_context_on_failed_transaction(self):
        store = Store(':memory:', Engine(C))
        for i in range(5):
            store.ingest(bar(i * 60), (i + 1) * 60)
        before = snapshot(store.engine)
        store.db.execute("CREATE TRIGGER fail BEFORE INSERT ON inputs BEGIN SELECT RAISE(ABORT,'fixture_failure'); END")
        with self.assertRaises(Exception):
            store.ingest(bar(0, 300, v='5'), 300)
        self.assertEqual(snapshot(store.engine), before)
        store.close()

    def test_real_strategy_trade_unchanged_even_when_context_opposes(self):
        engines = {mode: Engine(C, context_mode=mode) for mode in ('off', 'record')}
        for b in trading_fixture():
            if b.period == 300 and b.start == 450 * 300:
                for name, frame in engines['record'].higher_timeframes.frames.items():
                    frame.bars = higher_bars(frame.period, end=b.end // frame.period * frame.period, trend='down')
            for e in engines.values():
                e.ingest(b, b.received)
        before, after = engines['off'], engines['record']
        self.assertGreater(len(after.closed), 0)
        self.assertGreater(after.counts.get('PARTIAL_TP1', 0), 0)
        self.assertEqual(digest(economic_events(before)), digest(economic_events(after)))
        self.assertEqual(before.nav(), after.nav())
        opened = next(ev for ev in after.events if ev['kind'] == 'PAPER_OPEN')
        context = after.signal_contexts[opened['id']]
        self.assertEqual(context['frames']['1h']['alignment'], 'COUNTERTREND')
        self.assertEqual(context['frames']['4h']['alignment'], 'COUNTERTREND')
        self.assertEqual(cohorts(after)['4h']['COUNTERTREND']['paper_entries'], 1)

    def test_comparison_unknown_context_keeps_trade_and_zero_deltas(self):
        report = compare_rows(trading_fixture(), C)
        self.assertTrue(report['economic_events_identical'])
        self.assertGreater(report['results']['record']['paper_entries'], 0)
        self.assertTrue(all(v in (D(0), None) for v in report['delta_record_minus_off'].values()))
        self.assertGreater(report['cohorts']['4h']['UNKNOWN']['paper_entries'], 0)
        self.assertIsNone(report['verified_net'])
        self.assertFalse(report['performance_claim'])
        self.assertEqual(report['parameters']['cost_scenario']['status'], C.status)
        self.assertEqual(report['parameters']['latency_seconds'], 60)
        self.assertEqual(report['parameter_hash'], digest(report['parameters']))

    def test_sampled_drawdown_numeric_example(self):
        metrics = NavMetrics()
        for value in ('1000', '1010', '990', '1005', '980'):
            metrics.observe(D(value))
        self.assertEqual(metrics.max_drawdown, D(30))
        self.assertEqual(metrics.max_drawdown_fraction, D(30) / D(1010))

    def test_comparison_mark_ignores_late_old_five_minute_close(self):
        mark = AvailableMark()
        mark.observe(bar(300, c='100'))
        mark.observe(bar(0, 300, c='80', l='79', received=370))
        self.assertEqual(mark.price, D(100))
        self.assertEqual(mark.end, 360)
        mark.observe(bar(300, 300, c='110', h='111'))
        mark.observe(bar(540, c='109', h='110'))
        self.assertEqual(mark.price, D(109))
        self.assertEqual(mark.period, 60)

    def test_replay_context_snapshot_mode_and_repeat_hash(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'bars.jsonl'
            path.write_text('\n'.join(dumps(bar(i * 60)) for i in range(6)))
            db = str(Path(directory) / 'state.sqlite')
            a = replay(path, C, db=db)
            b = replay(path, C, db=db)
            self.assertEqual(a['event_hash'], b['event_hash'])
            self.assertEqual(a['higher_timeframes'], b['higher_timeframes'])
            with self.assertRaisesRegex(ValueError, 'CONTEXT_MODE_DIFFERS'):
                replay(path, C, db=db, context_mode='off')


if __name__ == '__main__':
    unittest.main()
