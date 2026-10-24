import copy
import json
import os
import tempfile
import unittest

from lbank_native_entry import SOURCE, wilder_rsi_series
from lbank_trend_runner import atr_wilder, runner_decision, valid_state, format_runner_alert, RUNNER_TTL
from lbank_signal_risk import SignalRiskTracker
from lbank_performance import PerformanceBook, leg_pnl


def state(direction=1):
    bars = []
    for i in range(35):
        c = 100 + .5*i
        o, h, l = c-.5, c+.3, c-.8
        if direction == -1:
            o, h, l, c = 200-o, 200-l, 200-h, 200-c
        bars.append(dict(start=300*i, open=o, high=h, low=l, close=c,
                         volume=20. if i == 34 else 10., quality='stream_closed'))
    closes = [b['close'] for b in bars]
    return dict(source=SOURCE, five_bars=bars, last5_start=10200,
                closes15=[closes[-4], closes[-1]],
                rsi5={'closes': closes, 'rsi': wilder_rsi_series(closes)})


def plan(d=1):
    return dict(direction=d, price=114. if d == 1 else 86.,
                stop=113. if d == 1 else 87., target1=115. if d == 1 else 85.,
                target2=117. if d == 1 else 83., quantity_btc=1., net_loss=1.2,
                runner_enabled=True, data_source=SOURCE, strategy='breakout')


def decision(d=1, **updates):
    p = plan(d)
    args = dict(direction=d, entry=p['price'], stop=p['price'], target2=p['target2'],
                sent_at=8500, last_bar=-1, state=state(d), now=10515,
                price=116.8 if d == 1 else 83.2)
    args.update(updates)
    return runner_decision(**args)


class RunnerPolicyTests(unittest.TestCase):
    def test_wilder_atr_constant_range(self):
        self.assertAlmostEqual(atr_wilder(state()['five_bars']), 1.1)

    def test_long_and_short_extend_and_protect(self):
        for d in (1, -1):
            result = decision(d)
            p = plan(d)
            self.assertEqual(result['reason'], 'target_extended')
            self.assertGreater(d*(result['target2']-p['target2']), 0)
            self.assertGreater(d*(result['stop']-p['price']), 0)
            self.assertLess(d*(result['stop']-(116.8 if d == 1 else 83.2)), 0)

    def test_stop_can_only_tighten(self):
        for d in (1, -1):
            old = 116. if d == 1 else 84.
            result = decision(d, stop=old)
            self.assertGreaterEqual(d*(result['stop']-old), 0)

    def test_target_can_extend_again_on_a_later_bar(self):
        first = decision()
        self.assertIsNone(decision(last_bar=first['bar_start']))
        updated = state()
        updated['five_bars'].append(dict(start=10500, open=117., high=120.3,
            low=116.8, close=120., volume=30., quality='stream_closed'))
        updated['last5_start'] = 10500
        updated['closes15'] = [115., 119.]
        updated['rsi5']['rsi'] = [100., 100., 100.]
        second = decision(state=updated, now=10815, price=120.,
                          stop=first['stop'], target2=first['target2'], last_bar=10200)
        self.assertEqual(second['reason'], 'target_extended')
        self.assertGreater(second['target2'], first['target2'])

    def test_no_volume_confirmation_means_no_extension(self):
        s = state()
        s['five_bars'][-1]['volume'] = 1.
        result = decision(state=s)
        self.assertEqual(result['reason'], 'trail_update')
        self.assertEqual(result['target2'], 117.)

    def test_range_needs_confirmed_break_of_opposite_boundary(self):
        self.assertNotEqual(decision(strategy='range_rebound', barrier=118.)['reason'], 'target_extended')
        self.assertEqual(decision(strategy='range_rebound', barrier=116.)['reason'], 'target_extended')

    def test_current_quote_beyond_proposed_stop_is_not_backfilled(self):
        self.assertIsNone(decision(price=114.5, target2=125.))

    def test_missing_stale_corrupt_gapped_and_forming_data_cannot_extend(self):
        cases = [None, dict(state(), source='spot'), dict(state(), last5_start=9900)]
        for key, value in [('volume', float('nan')), ('quality', 'forming'), ('start', 9901), ('high', 1)]:
            s = state()
            s['five_bars'][-1][key] = value
            cases.append(s)
        for s in cases:
            self.assertIsNone(decision(state=s))
        self.assertIsNone(decision(now=10800))
        self.assertIsNone(decision(sent_at=10201))

    def test_two_opposing_closes_below_ema_exit(self):
        s = state()
        for i, c in [(-2, 110.), (-1, 109.)]:
            s['five_bars'][i].update(open=c+.5, high=c+.8, low=c-.3, close=c)
        self.assertEqual(decision(state=s, price=114.5)['reason'], 'trend_exit')

    def test_neutral_stall_exits_remaining_half(self):
        s = state()
        for b in s['five_bars'][-3:]:
            b.update(open=116., close=116., high=116.1, low=115.9)
        s['rsi5']['rsi'] = [50., 50.1, 50.]
        self.assertEqual(decision(state=s, price=116.)['reason'], 'neutral_exit')

    def test_15m_conflict_cannot_extend(self):
        s = state()
        s['closes15'] = [120., 115.]
        self.assertIsNone(decision(state=s))


class RunnerDeliveryTests(unittest.TestCase):
    def prepared(self, d=1, path=None):
        tracker = SignalRiskTracker(path)
        sig = tracker.add_sent_signal(plan(d), 8500)
        tracker.check([sig.target1], 8560)
        tracker.acknowledge(sig, 'target1_reached')
        return tracker, sig

    def test_failed_delivery_never_moves_stop_and_retry_recomputes(self):
        tracker, sig = self.prepared()
        event = tracker.check([116.8], 10515, state())[0]
        self.assertEqual(event[1], 'target_extended')
        self.assertEqual(sig.stop, 114.)
        self.assertEqual(sig.target2, 117.)
        self.assertEqual(tracker.check([113.9], 10530, state())[0][1], 'stop_breached')
        self.assertIsNone(sig.pending_runner)

    def test_success_persists_profitable_stop_and_no_duplicate(self):
        for d in (1, -1):
            with tempfile.TemporaryDirectory() as td:
                path = os.path.join(td, 'state.json')
                tracker, sig = self.prepared(d, path)
                quote = 116.8 if d == 1 else 83.2
                tracker.check([quote], 10515, state(d))
                tracker.acknowledge(sig, 'target_extended')
                self.assertGreater(d*(sig.stop-sig.entry), 0)
                restored = SignalRiskTracker(path)
                self.assertEqual(len(restored.signals), 1)
                self.assertEqual(restored.signals[0].stop, sig.stop)
                self.assertEqual(restored.check([quote], 10530, state(d)), [])
                self.assertEqual(restored.check([sig.stop-d*.1], 10545, state(d))[0][1], 'stop_breached')

    def test_restart_drops_undelivered_proposal_and_retains_old_protection(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'state.json')
            tracker, sig = self.prepared(path=path)
            tracker.check([116.8], 10515, state())
            restored = SignalRiskTracker(path)
            self.assertIsNone(restored.signals[0].pending_runner)
            self.assertEqual(restored.signals[0].stop, 114.)

    def test_fallback_target_when_data_missing_or_weak(self):
        tracker, sig = self.prepared()
        self.assertEqual(tracker.check([117.1], 10515, None)[0][1], 'target2_reached')
        self.assertEqual(sig.target2, 117.)

    def test_no_retroactive_extension_after_target_touch_and_retreat(self):
        tracker, sig = self.prepared()
        tracker.check([117.1], 10515, None)  # notice not acknowledged
        event = tracker.check([116.8], 10530, state())[0]
        self.assertEqual(event[1], 'target2_reached')

    def test_stop_has_priority_over_expiry_and_extension(self):
        tracker, sig = self.prepared()
        self.assertEqual(tracker.check([113.], 8500+RUNNER_TTL, state())[0][1], 'stop_breached')

    def test_runner_expiry_not_silently_dropped(self):
        tracker, sig = self.prepared()
        event = tracker.check([114.5], 8500+RUNNER_TTL, None)[0]
        self.assertEqual(event[1], 'time_exit')
        tracker.acknowledge(sig, event[1])
        self.assertEqual(tracker.signals, [])

    def test_no_tp1_means_no_trailing_and_legacy_unchanged(self):
        tracker = SignalRiskTracker()
        sig = tracker.add_sent_signal(plan(), 8500)
        self.assertEqual(tracker.check([116.8], 10515, state())[0][1], 'target1_reached')
        self.assertEqual(sig.stop, 113.)
        legacy = SignalRiskTracker()
        p = dict(plan(), runner_enabled=False)
        old = legacy.add_sent_signal(p, 8500)
        legacy.check([115.], 8560)
        legacy.acknowledge(old, 'target1_reached')
        self.assertEqual(legacy.check([117.1], 10515, state())[0][1], 'target2_reached')

    def test_usdt_message_no_claim_of_exchange_fill(self):
        tracker, sig = self.prepared()
        tracker.check([116.8], 10515, state())
        note = format_runner_alert(sig, 'target_extended', 116.8, '10/17 18:00')
        self.assertIn('목표 연장', note)
        self.assertIn('자동주문 없음', note)
        self.assertIn('잔여 50%', note)
        self.assertIn('신호 #1', note)
        self.assertLessEqual(len(note.splitlines()), 7)


class RunnerAccountingTests(unittest.TestCase):
    def test_new_policy_only_new_enabled_plans_and_source_is_native(self):
        book = PerformanceBook(':memory:', 1000)
        book.add(1, dict(plan(), runner_enabled=False), 8500)
        book.add(2, plan(), 8501)
        self.assertEqual(book.summary()['trend_runner']['signals'], 1)
        p = json.loads(book.db.execute("SELECT plan FROM signals WHERE policy='trend_runner'").fetchone()[0])
        self.assertEqual(p['data_source'], SOURCE)
        self.assertEqual(p['observation_source'], 'lbank_perpetual_ticker')

    def test_runner_extends_while_baseline_takes_target_then_stops_at_worse_quote(self):
        book = PerformanceBook(':memory:', 1000)
        book.add(1, plan(), 8500, fee=0, slippage=0, funding=0)
        for ts in range(8560, 10481, 60):
            book.observe(ts, 114.5)
        book.observe(10515, 117.1, state())
        self.assertEqual(book.summary()['levels']['closed'], 1)
        self.assertEqual(book.summary()['trend_runner']['open'], 1)
        self.assertEqual(book.paired_runner_comparison()['completed_pairs'], 0)
        row = book.db.execute("SELECT * FROM signals WHERE policy='trend_runner'").fetchone()
        p = json.loads(row['plan'])
        exit_price = p['runner_stop']-.1
        book.observe(10530, exit_price, state())
        result = book.summary()['trend_runner']
        self.assertAlmostEqual(result['net_usdt_model'], .5*(115-114)+.5*(exit_price-114))
        self.assertEqual(book.paired_runner_comparison()['completed_pairs'], 1)

    def test_gap_censors_runner_not_win(self):
        book = PerformanceBook(':memory:', 1000)
        book.add(1, plan(), 8500)
        book.observe(10515, 130., state())
        self.assertEqual(book.summary()['trend_runner']['censored'], 1)
        self.assertEqual(book.paired_runner_comparison()['completed_pairs'], 0)

    def test_net_costs_and_reported_expectancy_not_target_touch_win(self):
        book = PerformanceBook(':memory:', 1000)
        book.add(1, plan(), 8500)
        book.observe(8560, 113.)
        stats = book.summary()['trend_runner']
        self.assertEqual(stats['wins'], 0)
        self.assertEqual(stats['losses'], 1)
        self.assertLess(stats['expectancy_usdt'], -1.)
        self.assertGreater(stats['closed_model_drawdown_usdt'], 1.)
        self.assertAlmostEqual(stats['win_rate_wilson95_iid_only'][0], 0.)

    def test_warning_closes_runner_and_comparator_only(self):
        book = PerformanceBook(':memory:', 1000)
        book.add(1, plan(), 8500)
        book.observe(8560, 113.5)
        book.early_warning(1, 8500, 8560, 113.5)
        self.assertEqual(book.summary()['trend_runner']['closed'], 1)
        self.assertEqual(book.summary()['levels']['open'], 1)

    def test_runner_book_restart_and_duplicate_add_preserve_stop(self):
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, 'book.sqlite3')
            book = PerformanceBook(path, 1000)
            book.add(1, plan(), 8500)
            for ts in range(8560, 10481, 60):
                book.observe(ts, 114.5)
            book.observe(10515, 117.1, state())
            old = book.db.execute("SELECT plan FROM signals WHERE policy='trend_runner'").fetchone()[0]
            book.db.close()
            restored = PerformanceBook(path, 1000)
            restored.add(1, plan(), 8500)
            self.assertEqual(restored.db.execute("SELECT plan FROM signals WHERE policy='trend_runner'").fetchone()[0], old)


if __name__ == '__main__':
    unittest.main()
