"""Native storage -> live decision boundary regression tests. No external calls."""
import os
import tempfile
import unittest
from unittest.mock import patch

import lbank_futures_monitor as monitor
from lbank_native_candles import NativeCandleStore
from lbank_native_entry import NativeEntryFeed, SOURCE, wilder_rsi_series


class NativeEntryTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = os.path.join(self.directory.name, "native.sqlite3")
        self.writer = NativeCandleStore(self.path)
        self.addCleanup(self.writer.db.close)
        self.start = 180000  # aligned 15m
        self.now = self.start + 300 * 60 + 20
        with self.writer.db:
            for i in range(300):
                t = self.start + i * 60
                close = 80000. + 25 * i + (45 if i % 2 else 0)
                self.writer.db.execute("INSERT INTO native_bars VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (60, t, close-10, close+15, close-20, close, i+1, (i+1)*close,
                     t+59, "stream_closed"))
            for t in range(self.start, self.start+300*60, 300):
                self.writer._aggregate(t)
        self.feed = NativeEntryFeed(self.path)

    def snapshot(self):
        return self.feed.snapshot(self.now, 87500.)

    def test_persisted_native_bars_and_timeframes_keep_correct_units(self):
        state, diag = self.snapshot()
        self.assertTrue(diag["entry_source_switched"])
        self.assertEqual(diag["closed_5m"], 60)
        self.assertEqual(len(state["prices"]), 300)
        self.assertEqual(state["prices"][-1], 87520.)
        self.assertEqual(state["quote"], 87500.)
        self.assertEqual(state["five_bars"][-1]["volume"], sum(range(296, 301)))
        self.assertEqual(state["closes15"][-1], state["rsi5"]["closes"][-1])
        self.assertEqual(state["last5_start"], int(self.now//300)*300-300)
        self.assertEqual(state["source"], SOURCE)
        self.assertFalse(diag["rsi_history_mature"])

    def test_rsi_known_wilder_seed_and_flat_series(self):
        prices = [44.34,44.09,44.15,43.61,44.33,44.83,45.10,45.42,
                  45.84,46.08,45.89,46.03,45.61,46.28,46.28]
        self.assertAlmostEqual(wilder_rsi_series(prices)[0], 70.464135, places=6)
        self.assertEqual(wilder_rsi_series([100.]*20), [50.]*6)
        self.assertEqual(wilder_rsi_series(list(range(1,21))), [100.]*6)

    def test_quote_and_forming_candles_cannot_change_completed_5m_rsi(self):
        before, _ = self.snapshot()
        t = int(self.now//60)*60
        with self.writer.db:
            self.writer.db.execute("INSERT INTO native_bars VALUES(?,?,?,?,?,?,?,?,?,?)",
                (60,t,99000,99999,98000,99999,50,5000000,t+1,"forming"))
            self.writer.db.execute("INSERT INTO native_bars VALUES(?,?,?,?,?,?,?,?,?,?)",
                (300,t,99000,99999,98000,99999,50,5000000,t+1,"forming"))
        after, _ = self.feed.snapshot(self.now, 99999.)
        self.assertEqual(before["rsi5"], after["rsi5"])
        self.assertEqual(before["prices"], after["prices"])

    def test_recent_gap_pauses_instead_of_using_old_history(self):
        with self.writer.db:
            self.writer.db.execute("DELETE FROM native_bars WHERE period=300 AND start=?",
                (int(self.now//300)*300-3*300,))
        state, diag = self.snapshot()
        self.assertIsNone(state)
        self.assertFalse(diag["entry_source_switched"])
        self.assertEqual(diag["closed_5m"], 2)

    def test_gap_outside_recent_minimum_uses_contiguous_tail_only(self):
        with self.writer.db:
            self.writer.db.execute("UPDATE native_bars SET quality='gap' WHERE period=300 AND start=?",
                (int(self.now//300)*300-25*300,))
        state, diag = self.snapshot()
        self.assertIsNotNone(state)
        self.assertEqual(diag["closed_5m"], 24)
        self.assertFalse(diag["rsi_history_mature"])

    def test_stale_latest_minute_and_latest_5m_fail_closed(self):
        self.assertIsNone(self.feed.snapshot(self.now+60, 87500.)[0])
        with self.writer.db:
            self.writer.db.execute("DELETE FROM native_bars WHERE period=300 AND start=?",
                (int(self.now//300)*300-300,))
        self.assertIsNone(self.snapshot()[0])

    def test_corrupt_latest_bar_is_not_skipped(self):
        with self.writer.db:
            self.writer.db.execute("UPDATE native_bars SET high=1 WHERE period=60 AND start=?",
                (int(self.now//60)*60-60,))
        self.assertIsNone(self.snapshot()[0])

    def test_reader_never_creates_database_or_marks_writer_forming_as_gap(self):
        absent = os.path.join(self.directory.name, "missing.sqlite3")
        self.assertIsNone(NativeEntryFeed(absent).snapshot(self.now, 87500.)[0])
        self.assertFalse(os.path.exists(absent))
        t = int(self.now//60)*60
        with self.writer.db:
            self.writer.db.execute("INSERT INTO native_bars VALUES(?,?,?,?,?,?,?,?,?,?)",
                (60,t,87500,87501,87499,87500,1,87500,t+1,"forming"))
        self.snapshot()
        self.assertEqual(self.writer.db.execute("SELECT quality FROM native_bars WHERE period=60 AND start=?", (t,)).fetchone()[0], "forming")

    def test_native_watch_and_confirmation_never_call_sampled_gates(self):
        state, _ = self.snapshot()
        # Isolate directional alignment while retaining real native context,
        # price structure and fee/risk/target calculation on both stages.
        state["rsi5"]["rsi"] = [84., 85.]
        with patch("lbank_futures_monitor.sampled_context_gate", side_effect=AssertionError("ticker fallback")), \
             patch("lbank_futures_monitor.sampled_five_minute_rsi", side_effect=AssertionError("ticker RSI fallback")):
            diag = {}
            plan = monitor.classify(state["prices"], native_state=state, diagnostics=diag)
            self.assertIsNotNone(plan, diag)
            self.assertEqual(plan["data_source"], SOURCE)
            self.assertEqual(plan["sampled_context"]["context_type"], SOURCE)
            self.assertEqual(plan["rsi5"], 85.)  # No overbought LONG veto.
            confirmed = monitor.confirm_watch(state["prices"], plan, 60, native_state=state)
            self.assertIsNotNone(confirmed)
            self.assertEqual(confirmed["data_source"], SOURCE)
            self.assertLessEqual(confirmed["net_loss"], monitor.REF_BALANCE*.005)
            self.assertLessEqual(confirmed["margin"], monitor.REF_BALANCE*.15)
            note = monitor.format_entry_alert(confirmed, "2026-10-17T08:00:00Z")
            self.assertIn("원본 5분봉 기반", note)
            self.assertNotIn("실제 5분봉/호가 미검증", note)
            self.assertLessEqual(len(note.splitlines()), 10)

    def test_real_wick_resistance_blocks_close_only_false_breakout(self):
        state, _ = self.snapshot()
        state["rsi5"]["rsi"] = [64., 65.]
        state["minute_bars"][-2]["high"] = state["prices"][-1] + 100
        with patch("lbank_futures_monitor.detect_structures", return_value=[]):
            diag = {}
            self.assertIsNone(monitor.classify(state["prices"], native_state=state, diagnostics=diag))
            self.assertEqual(diag["reason"], "market_no_setup")

    def test_native_stop_uses_real_low_not_only_minute_closes(self):
        state, _ = self.snapshot()
        state["minute_bars"][-3]["low"] = 87100.
        plan = monitor.calculate_plan(state["prices"], 1, native_state=state)
        self.assertIsNotNone(plan)
        self.assertAlmostEqual(plan["stop"], 87100.*.9997)
        self.assertLessEqual(plan["net_loss"], monitor.REF_BALANCE*.005)

    def test_native_direction_only_at_extremes_and_confirmation_quote_no_chase(self):
        state, _ = self.snapshot()
        for direction, values in ((1,[84.,85.]),(-1,[20.,19.])):
            state["rsi5"]["rsi"] = values
            self.assertTrue(monitor.five_minute_rsi_entry_gate(None,direction,native_state=state)[0])
            self.assertFalse(monitor.five_minute_rsi_entry_gate(None,-direction,native_state=state)[0])
        state["rsi5"]["rsi"] = [64.,65.]
        plan = monitor.classify(state["prices"],native_state=state)
        self.assertIsNotNone(plan)
        state["quote"] = plan["price"]*1.003
        diag = {}
        self.assertIsNone(monitor.confirm_watch(state["prices"],plan,60,native_state=state,diagnostics=diag))
        self.assertEqual(diag["reason"], "price_deviation")


if __name__ == "__main__":
    unittest.main()
