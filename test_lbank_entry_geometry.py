"""Cost/volatility entry boundaries, not a strategy profitability test."""
import unittest
from datetime import datetime

import lbank_futures_monitor as m
from lbank_performance import PerformanceBook, KST
from lbank_structure_patterns import detect_structures


def state(spread=10):
    bars = [dict(open=80000., high=80000.+spread, low=80000.-spread,
                 close=80000., volume=1.) for _ in range(65)]
    # Historical excursion supplies target room without inventing a local stop.
    bars[10].update(high=80400., low=79600.)
    five = [dict(open=80000., high=80000.+spread, low=80000.-spread,
                 close=80000., volume=5.) for _ in range(30)]
    return dict(quote=80000., prices=[80000.]*65, minute_bars=bars, five_bars=five)


class GeometryTests(unittest.TestCase):
    def test_missing_or_zero_volatility_cannot_create_plan(self):
        for update in ({"five_bars": []}, {"quote": float("nan")},
                       {"five_bars": state(0)["five_bars"]}):
            s = dict(state(), **update)
            self.assertIsNone(m.native_entry_geometry(s))
            self.assertIsNone(m.calculate_plan(s["prices"], 1, native_state=s))

    def test_volatility_floor_and_hard_stop_cap_both_apply(self):
        quiet, volatile = state(), state(200)
        self.assertAlmostEqual(m.native_entry_geometry(quiet)["stop_floor_fraction"], .0005)
        self.assertGreater(m.native_entry_geometry(volatile)["stop_floor_fraction"], .006)
        self.assertIsNone(m.calculate_plan(volatile["prices"], 1, native_state=volatile))

    def test_long_and_short_keep_net_rr_risk_margin_and_real_wick(self):
        for direction in (1, -1):
            s = state()
            key = "low" if direction == 1 else "high"
            s["minute_bars"][-3][key] = 80000-direction*90
            plan = m.calculate_plan(s["prices"], direction, native_state=s)
            self.assertIsNotNone(plan)
            self.assertLessEqual(direction*(plan["stop"]-s["minute_bars"][-3][key]), 0)
            self.assertLessEqual(plan["net_loss"], m.REF_BALANCE*m.RISK_FRACTION)
            self.assertLessEqual(plan["margin"], m.REF_BALANCE*m.MAX_MARGIN_FRACTION)
            self.assertGreaterEqual(plan["rr1"], m.MIN_NET_RR-1e-7)
            self.assertLess(plan["stop_distance_pct"], .25)
            # New floor cannot override a nearby target barrier.
            setup = dict(strategy="range_rebound", structure_anchor=80000-direction*90,
                         barrier=80000+direction*100)
            self.assertIsNone(m.calculate_plan(s["prices"], direction, setup, native_state=s))

    def test_small_range_reports_cost_failure_and_legacy_default_unchanged(self):
        s = state()
        s["minute_bars"][10].update(high=80010., low=79990.)
        geo = m.native_entry_geometry(s)
        for options, expected in (({}, .6), ({"min_range_fraction": geo["min_range_fraction"]},
                                            geo["min_range_fraction"]*100)):
            diag = {}
            self.assertEqual(detect_structures(s["prices"], m.ema, m.rsi,
                             bars=s["minute_bars"], diagnostics=diag, **options), [])
            self.assertFalse(diag["range_width_ok"])
            self.assertAlmostEqual(diag["range_required_pct"], expected)
        self.assertIsNone(m.calculate_plan(s["prices"], 1, native_state=s))


class DecisionReportTests(unittest.TestCase):
    def test_kst_day_counts_evaluations_and_clears_stale_market(self):
        book = PerformanceBook(":memory:", 1000)
        self.addCleanup(book.db.close)
        start = datetime(2026, 10, 17, tzinfo=KST).timestamp()
        market = {"range60_pct": .12, "minimum_target_pct": .44}
        book.sample(start-1, 80000, {"reason": "risk_or_target"})
        book.sample(start+1, 80000, {"reason": "market_no_setup", "market": market})
        book.sample(start+61, 80000, {"reason": "market_no_setup", "market": market})
        summary = book.decision_summary("2026-10-17")
        self.assertEqual(summary["checks"], 2)
        self.assertEqual(summary["reasons"], {"market_no_setup": 2})
        note = book.daily_message(start+22*3600+50*60)
        self.assertIn("진입심사 기록 2회", note)
        self.assertIn("0.120%", note)
        self.assertIn("0.440%", note)
        book.sample(start+120, 80000, {"reason": "native_data_unavailable"})
        self.assertIsNone(book.decision_summary("2026-10-17")["last_market"])


if __name__ == "__main__":
    unittest.main()
