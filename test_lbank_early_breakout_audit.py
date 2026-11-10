"""Regression tests for observation-only candidate detection and journal."""
import json
import sqlite3
import unittest
from types import SimpleNamespace

from lbank_early_breakout_audit import (evaluate_early_breakout,
                                          persist_early_audit, POLICY)


class EarlyBreakoutAuditTests(unittest.TestCase):
    def rows(self, *, current_close=101.5, current_volume=20, history_volume=10):
        base = 1794000000 - 1794000000 % 300
        rows = []
        for i in range(21):
            last = i == 20
            o, h, l, c, v = (100, 102, 98, 100, history_volume)
            if last:
                o, c, v = 100, current_close, current_volume
                h, l = max(102, o, c), min(98, o, c)
            rows.append(dict(start=base+i*300, period=300,
                             open=o, high=h, low=l, close=c, volume=v,
                             quality="stream_closed", received_at=base+(i+1)*300+1))
        return rows

    def test_detects_long_breakout_at_threshold(self):
        x = self.rows(current_close=103, current_volume=15)
        o = evaluate_early_breakout(x)
        self.assertEqual((o["status"], o["direction"], o["volume_multiple"]),
                         ("candidate", "long", 1.5))

    def test_detects_short_breakout(self):
        o = evaluate_early_breakout(self.rows(current_close=97, current_volume=21))
        self.assertEqual((o["status"], o["direction"]), ("candidate", "short"))

    def test_does_not_include_current_volume_in_baseline(self):
        o = evaluate_early_breakout(self.rows(current_close=103, current_volume=1000))
        self.assertEqual(o["baseline_volume"], 10)
        self.assertEqual(o["volume_multiple"], 100)

    def test_excludes_low_volume_breakout(self):
        o = evaluate_early_breakout(self.rows(current_close=103, current_volume=14.99))
        self.assertEqual(o["status"], "breakout_below_volume_threshold")

    def test_excludes_touches_and_non_breakouts(self):
        for c in (102, 100, 98):
            with self.subTest(close=c):
                self.assertEqual(evaluate_early_breakout(self.rows(current_close=c))["status"],
                                 "no_breakout")

    def test_requires_contiguous_completed_input(self):
        x = self.rows(); x[-2]["start"] += 300
        self.assertEqual(evaluate_early_breakout(x)["status"], "invalid_source")
        x = self.rows(); x[-1]["quality"] = "forming"
        self.assertEqual(evaluate_early_breakout(x)["status"], "invalid_source")
        x = self.rows(); x[-1]["received_at"] = x[-1]["start"] + 290
        self.assertEqual(evaluate_early_breakout(x)["status"], "invalid_source")

    def test_warmup_and_zero_baseline(self):
        self.assertEqual(evaluate_early_breakout(self.rows()[:20])["status"], "insufficient_history")
        self.assertEqual(evaluate_early_breakout(self.rows(history_volume=0))["status"],
                         "missing_volume_baseline")

    def test_persist_is_idempotent_and_does_not_emit(self):
        db = sqlite3.connect(":memory:")
        self.addCleanup(db.close)
        ledger = SimpleNamespace(db=db)
        bars = self.rows(current_close=103)
        persist_early_audit(ledger, bars, now=10, final_status="no_qualified_entry",
                            final_reasons={"insufficient_net_rr": 1})
        persist_early_audit(ledger, bars, now=20, final_status="telegram_accepted",
                            final_reasons={})
        record = db.execute("SELECT policy, evaluated_at, status, evidence FROM video_early_breakout_audit").fetchone()
        self.assertEqual(record[:3], (POLICY, 10, "candidate"))
        evidence = json.loads(record[3])
        self.assertEqual(evidence["final_status"], "no_qualified_entry")
        self.assertEqual(evidence["final_reasons"], {"insufficient_net_rr": 1})
        self.assertEqual(evidence["delivery"], "observation_only_not_a_trade_signal")
        self.assertEqual(db.execute("SELECT COUNT(*) FROM video_early_breakout_audit").fetchone()[0], 1)


if __name__ == "__main__":
    unittest.main()
