import json
import os
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from lbank_performance import PerformanceBook, KST, leg_pnl
from lbank_signal_risk import SignalRiskTracker
import lbank_futures_monitor as monitor


def plan(direction=1):
    return {"direction": direction, "price": 100., "stop": 100. - direction,
            "target1": 100. + direction * 2, "target2": 100. + direction * 4,
            "quantity_btc": 1., "net_loss": 1.15, "strategy": "breakout"}


class PerformanceTests(unittest.TestCase):
    def test_half_exit_then_entry_stop_accounts_for_both_halves_and_costs(self):
        for d in (1, -1):
            with self.subTest(direction=d):
                book = PerformanceBook(":memory:", 1000)
                book.add(1, plan(d), 1000)
                book.observe(1060, 100 + d*2)
                events = book.observe(1120, 100)
                self.assertEqual(len(events), 2)
                p = json.loads(book.db.execute("SELECT plan FROM signals LIMIT 1").fetchone()[0])
                expected = leg_pnl(p, p["target1"], .5) + leg_pnl(p, 100, .5)
                self.assertAlmostEqual(events[0]["net_usdt_model"], expected)
                self.assertLess(expected, 1)  # Price breakeven still costs money.
                self.assertEqual(events[0]["status"], "entry_stop")

    def test_two_targets_cannot_double_count_full_position(self):
        book = PerformanceBook(":memory:", 1000)
        book.add(1, plan(), 1000, fee=0, slippage=0, funding=0)
        book.observe(1060, 105)
        self.assertEqual(book.summary()["levels"]["net_usdt_model"], 3.)
        book.observe(1120, 106)
        self.assertEqual(book.summary()["levels"]["net_usdt_model"], 3.)

    def test_gap_is_censored_even_when_next_quote_crosses_target(self):
        book = PerformanceBook(":memory:", 1000)
        book.add(1, plan(), 1000)
        book.observe(1300, 110)
        result = book.summary()["levels"]
        self.assertEqual(result["closed"], 0)
        self.assertEqual(result["censored"], 1)
        self.assertEqual(result["net_usdt_model"], 0)

    def test_stop_gap_uses_worse_observed_price(self):
        book = PerformanceBook(":memory:", 1000)
        book.add(1, plan(), 1000, fee=0, slippage=0, funding=0)
        book.observe(1060, 97)
        self.assertEqual(book.summary()["levels"]["net_usdt_model"], -3)

    def test_warning_policy_closes_separately(self):
        book = PerformanceBook(":memory:", 1000)
        book.add(1, plan(), 1000, fee=0, slippage=0, funding=0)
        book.observe(1060, 99.5)
        book.early_warning(1, 1000, 1060, 99.5)
        book.observe(1120, 104)
        self.assertEqual(book.summary()["levels"]["net_usdt_model"], 3)
        self.assertEqual(book.summary()["warning_exit"]["net_usdt_model"], -.5)

    def test_restart_restores_half_exit_and_duplicate_add_is_idempotent(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "performance.sqlite3")
            book = PerformanceBook(path, 1000)
            book.add(1, plan(), 1000, fee=0, slippage=0, funding=0)
            book.observe(1060, 102)
            book.db.close()
            restored = PerformanceBook(path, 1000)
            restored.add(1, plan(), 1000, fee=0, slippage=0, funding=0)
            restored.observe(1120, 100)
            self.assertEqual(restored.summary()["levels"]["signals"], 1)
            self.assertEqual(restored.summary()["levels"]["net_usdt_model"], 1)

    def test_out_of_order_observation_does_not_trigger_target(self):
        book = PerformanceBook(":memory:", 1000)
        book.add(1, plan(), 1000)
        self.assertEqual(book.observe(990, 110), [])
        self.assertEqual(book.summary()["levels"]["open"], 1)

    def test_report_reserved_before_send_and_survives_restart(self):
        with tempfile.TemporaryDirectory() as directory:
            path = os.path.join(directory, "performance.sqlite3")
            t = datetime(2026, 10, 17, 22, 50, tzinfo=KST).timestamp()
            book = PerformanceBook(path, 1000)
            self.assertIsNone(book.daily_message(t-60))
            note = book.daily_message(t)
            self.assertIn("목표 5%", note)
            self.assertIn("실제 체결·계좌 순수익이 아닙니다", note)
            book.report_delivery(t, False)
            book.db.close()
            restored = PerformanceBook(path, 1000)
            self.assertIsNone(restored.daily_message(t+60))
            self.assertIsNotNone(restored.daily_message(t+86400))

    def test_expiry_is_a_priced_exit_and_retention_preserves_open_signals(self):
        book = PerformanceBook(":memory:", 1000)
        book.add(1, plan(), 1000, fee=0, slippage=0, funding=0)
        for t in range(1060, 8260, 60):
            book.observe(t, 100.5)
        self.assertEqual(book.summary()["levels"]["net_usdt_model"], .5)
        book.add(2, plan(), 9000)
        book.sample(10000000, 100)
        self.assertEqual(book.summary()["levels"]["open"], 1)

    def test_bad_target_geometry_rejected(self):
        book = PerformanceBook(":memory:", 1000)
        for update in ({"stop": 101}, {"target1": 99}, {"quantity_btc": float('nan')}):
            with self.assertRaises(ValueError):
                book.add(1, dict(plan(), **update), 1000)


class StrategyIndependenceTests(unittest.TestCase):
    def test_rejected_structural_candidate_does_not_hide_breakout(self):
        prices = [80000. + 25*i + (45 if i%2 else 0) for i in range(60)]
        failed = {"direction": 1, "strategy": "range_rebound", "structure_anchor": 79000,
                  "barrier": 80000, "level": 79000}
        diag = {}
        with patch.object(monitor, "detect_structure", return_value=failed), \
             patch.object(monitor, "detect_structures", return_value=[]):
            result = monitor.classify(prices, diagnostics=diag)
        self.assertEqual(result["strategy"], "breakout")
        self.assertEqual([c["reason"] for c in diag["candidates"]], ["risk_or_target", "qualified"])

    def test_opposing_qualified_strategies_abstain(self):
        setups = [{"direction": 1, "strategy": "range_rebound"},
                  {"direction": -1, "strategy": "trend_retest"}]
        def pass_gate(prices, direction, setup, observations=None, diagnostics=None):
            diagnostics["reason"] = "qualified"
            return dict(setup)
        diag = {}
        with patch.object(monitor, "detect_structure", return_value=setups[0]), \
             patch.object(monitor, "detect_structures", return_value=setups), \
             patch.object(monitor, "assess_candidate", side_effect=pass_gate):
            self.assertIsNone(monitor.classify([100.]*70, diagnostics=diag))
        self.assertEqual(diag["reason"], "strategy_conflict")

    def test_tp1_stop_references_move_and_survive_restart(self):
        for d in (1, -1):
            with tempfile.TemporaryDirectory() as directory:
                path = os.path.join(directory, "signals.json")
                tracker = SignalRiskTracker(path)
                signal = tracker.add_sent_signal(plan(d), 1000)
                tracker.check([100+d*2], 1060)
                tracker.acknowledge(signal, "target1_reached")
                self.assertEqual(signal.stop, signal.entry)
                restored = SignalRiskTracker(path)
                self.assertEqual(restored.check([100.], 1120)[0][1], "stop_breached")


if __name__ == "__main__":
    unittest.main()
