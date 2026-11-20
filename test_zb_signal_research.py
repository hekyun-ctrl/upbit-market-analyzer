import json
import unittest
from unittest.mock import patch

import lbank_futures_monitor as monitor
from research.zb_signal_research import evaluate_snapshot


END = 180000


def bar(start, op, hi, lo, close, period=60):
    return dict(start=start, period=period, received_at=start+period+5,
                updated=start+period-1, quality="stream_closed",
                open=op, high=hi, low=lo, close=close)


def market(latest):
    minutes = [bar(t, 100., 100.08, 99.92, 100.) for t in range(END-1200, END, 60)]
    minutes[-1] = bar(END-60, *latest)
    fives = [bar(t, 100., 101., 99., 100., 300) for t in range(END-6000, END, 300)]
    fives[-8] = bar(END-2400, 100., 104., 96., 100., 300)
    fives[-1] = bar(END-300, 100., 100.1, 99.75, 99.8, 300)
    return dict(minute_bars=minutes, five_bars=fives, quote=latest[-1],
                quote_observed_at=END+6)


class SignalResearchTests(unittest.TestCase):
    def test_first_downside_break_has_independent_structural_stops(self):
        state = market((100., 100.05, 99.6, 99.7))
        result = evaluate_snapshot(state, END+6)
        item = next(x for x in result["candidates"] if x["lane"] == "B1")
        local, structural = item["plans"]["1m"], item["plans"]["5m"]
        self.assertEqual(item["signal_basis"], "first_closed_minute_break")
        self.assertEqual(local["first_barrier"], 96.)
        self.assertTrue(local["reference_is_fresh_quote"])
        self.assertGreater(local["stop"], max(b["high"] for b in state["minute_bars"][-8:]))
        self.assertGreater(structural["stop"], local["stop"])
        self.assertFalse(result["entry_approved"])

    def test_first_reversal_long_survives_falling_five_minute_context(self):
        result = evaluate_snapshot(market((100., 100.44, 99.98, 100.3)), END+6)
        self.assertEqual(result["timeframes"]["5m"]["direction"], "down")
        self.assertIn("C1", [x["lane"] for x in result["candidates"]])

    def test_retests_get_a_fresh_lane_after_a_prior_break(self):
        state = market((99.70, 99.92, 99.57, 99.62))
        state["minute_bars"][-2] = bar(END-120, 100., 100.02, 99.64, 99.70)
        result = evaluate_snapshot(state, END+6)
        self.assertEqual([x["lane"] for x in result["candidates"]], ["B2"])
        self.assertEqual(result["candidates"][0]["signal_basis"], "break_and_held_retest")

    def test_unreceived_quote_cannot_pass_cost_gate(self):
        state = market((100., 100.05, 99.6, 99.7))
        state["quote_observed_at"] = END+4
        result = evaluate_snapshot(state, END+6)
        item = next(x for x in result["candidates"] if x["lane"] == "B1")
        self.assertEqual(item["plans"]["1m"]["status"], "no_fresh_quote")

    def test_existing_completed_five_minute_chase_gate_still_blocks_entry(self):
        state = market((100., 100.05, 99.6, 99.7))
        state["five_bars"][-1] = bar(END-300, 100., 100.1, 98.9, 99., 300)
        result = evaluate_snapshot(state, END+6)
        item = next(x for x in result["candidates"] if x["lane"] == "B1")
        self.assertEqual(item["plans"]["1m"]["status"], "avoid_late_5m_impulse_chase")
        self.assertEqual(item["plans"]["1m"]["cap_080_status"], "avoid_late_5m_impulse_chase")

    def test_reprice_waits_for_new_minute_and_does_not_inherit_levels(self):
        state = market((100., 100.05, 99.6, 99.7))
        denial = dict(at=END-31, basis_price=101., minute_start=END-120,
                      old_stop=999., old_target=1.)
        with patch.object(monitor.LOG, "info") as logged:
            self.assertIsNone(monitor._zb_shadow_observe(state, END+6, denial))
            self.assertEqual(logged.call_args.args[0], "ZB_FRESH_PRICE_SHADOW %s")
            event = json.loads(logged.call_args.args[1])
            self.assertNotIn("old_stop", event)
            self.assertNotIn("old_target", event)
            plan = event["reassessment"]["candidates"][0]["plans"]["1m"]
            self.assertNotEqual(plan["stop"], denial["old_stop"])
            self.assertNotEqual(plan["target"], denial["old_target"])
            denial["minute_start"] = END-60
            self.assertEqual(monitor._zb_shadow_observe(state, END+6, denial), denial)
            self.assertEqual(logged.call_args.args[0], "ZB_NATIVE_RESEARCH_EVENT %s")
