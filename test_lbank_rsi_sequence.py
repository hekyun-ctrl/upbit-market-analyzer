"""Sequence semantics, causal timing, target geometry and durable explanations."""
from copy import deepcopy
import json
import unittest

from research.zerobase.rsi_sequence import MAX_SETUP_BARS, sequence_series
from research.zerobase.video_entry import Costs, _plan, _series, replay
from test_lbank_video_entry import breakout, incomplete_reversal, mirror, reversal
import test_lbank_video_alert as alert_tests


def sequence_fixture(direction=1):
    bars = [dict(start=300*(i+1), open=100., close=100., high=100.1, low=99.9)
            for i in range(26)]
    for i, o, c in ((20, 100, 99), (21, 99, 98), (22, 98, 97),
                    (23, 96.9, 98.5), (24, 98.5, 98), (25, 97.9, 98.6)):
        bars[i].update(open=o, close=c, low=min(o,c)-.1, high=max(o,c)+.1)
    bb = [dict(rsi=20 if i == 22 else 50, lower=97.5, upper=104) for i in range(26)]
    if direction == -1:
        bars = mirror(bars)
        bb = [dict(rsi=100-b["rsi"], lower=2000-b["upper"], upper=2000-b["lower"]) for b in bb]
    return bars, bb


class RsiSequenceTest(unittest.TestCase):
    def test_two_distinct_turns_are_required_in_both_directions(self):
        for d in (1, -1):
            bars, bb = sequence_fixture(d)
            rows = sequence_series(bars, bb, d)
            self.assertEqual([x["stage"] for x in rows[22:]],
                             ["waiting_first_engulfing", "waiting_pullback",
                              "waiting_second_engulfing", "confirmed"])
            self.assertEqual(sum(x["event"] is not None for x in rows), 1)
            e = rows[-1]["event"]
            self.assertLess(e["first_confirmation_start"], e["pullback_start"])
            self.assertEqual(e["second_confirmation_start"], bars[-1]["start"])

    def test_band_touch_and_wick_only_are_not_an_outside_close(self):
        for close in (97.5, 97.6):
            bars, bb = sequence_fixture()
            bars[22]["close"] = close  # Wick remains outside.
            self.assertTrue(all(x["event"] is None for x in sequence_series(bars, bb, 1)))

    def test_three_adverse_bodies_or_adverse_engulfing_are_required(self):
        bars, bb = sequence_fixture()
        bars[20].update(open=99, close=99)  # doji breaks the three-candle run
        self.assertIsNone(sequence_series(bars, bb, 1)[-1]["event"])
        bars[21].update(open=97.5, close=98)  # then the exhaustion engulfs this bull
        self.assertIsNotNone(sequence_series(bars, bb, 1)[-1]["event"])

    def test_old_single_turn_fixture_is_not_a_two_turn_pattern(self):
        for b in (incomplete_reversal(), mirror(incomplete_reversal())):
            self.assertFalse(replay(b, nominal=True)["events"])

    def test_v_rebound_does_not_invent_a_second_bottom(self):
        bars, bb = sequence_fixture()
        bars[24].update(open=98.5, close=99, high=99.1, low=98.4)
        rows = sequence_series(bars, bb, 1)
        self.assertIsNone(rows[-1]["event"])
        self.assertEqual(rows[-1]["stage"], "waiting_pullback")

    def test_second_extreme_outside_band_does_not_confirm(self):
        for d in (1, -1):
            bars, bb = sequence_fixture(d)
            if d == 1:
                bb[25]["lower"] = bars[25]["low"]+.01
            else:
                bb[25]["upper"] = bars[25]["high"]-.01
            row = sequence_series(bars, bb, d)[-1]
            self.assertIsNone(row["event"])
            self.assertEqual(row["stage"], "second_extreme_outside_band")

    def test_lower_second_low_inside_widened_band_is_allowed(self):
        bars, bb = sequence_fixture()
        bars[24]["low"] = 95
        bb[24]["lower"] = 94
        e = sequence_series(bars, bb, 1)[-1]["event"]
        self.assertIsNotNone(e)
        self.assertLess(e["second_price"], e["first_price"])

    def test_later_bars_cannot_rewrite_earlier_stage_or_signal(self):
        for d in (1, -1):
            bars, bb = sequence_fixture(d)
            full = sequence_series(bars, bb, d)
            for n in range(1, len(bars)+1):
                self.assertEqual(sequence_series(bars[:n], bb[:n], d), full[:n])

    def test_one_event_per_setup_even_with_more_engulfing_pairs(self):
        bars, bb = sequence_fixture()
        for i in range(4):
            bars.append(dict(bars[24+i%2], start=bars[-1]["start"]+300))
            bb.append(deepcopy(bb[25]))
        self.assertEqual(sum(x["event"] is not None for x in sequence_series(bars, bb, 1)), 1)

    def test_expired_setup_cannot_turn_into_late_entry(self):
        bars, bb = sequence_fixture()
        tail, tail_bb = bars[23:], bb[23:]
        bars, bb = bars[:23], bb[:23]
        for _ in range(MAX_SETUP_BARS+1):
            bars.append(dict(bars[0], start=bars[-1]["start"]+300))
            bb.append(dict(rsi=50, lower=97.5, upper=104))
        for b, ind in zip(tail, tail_bb):
            bars.append(dict(b, start=bars[-1]["start"]+300)); bb.append(ind)
        rows = sequence_series(bars, bb, 1)
        self.assertIn("setup_expired", [x["stage"] for x in rows])
        self.assertTrue(all(x["event"] is None for x in rows))

    def test_reclaimed_center_uses_known_structure_without_lowering_risk_floor(self):
        for d, bars in ((1, breakout()[:46]), (-1, mirror(breakout()[:46]))):
            series = _series(bars)
            setup = dict(lane="rsi_reversal", direction=d, anchor=999.8 if d == 1 else 1000.2)
            plan = _plan(setup, bars, series, [b["start"]+300 for b in bars],
                         len(bars)-1, Costs(), True)
            self.assertEqual(plan["status"], "qualified")
            self.assertEqual(plan["target_basis"], "confirmed_pivot")
            self.assertGreaterEqual(plan["net_rr_at_signal_close"], Costs().min_rr)
            self.assertLess(d*(plan["target"]-plan["barrier"]), 0)

    def test_center_reclaimed_without_structure_cannot_invent_three_r_target(self):
        bars = breakout()[:46]
        bars[25]["high"] = 1000.2
        setup = dict(lane="rsi_reversal", direction=1, anchor=999.8)
        plan = _plan(setup, bars, _series(bars), [b["start"]+300 for b in bars],
                     len(bars)-1, Costs(), True)
        self.assertEqual(plan["status"], "no_known_target")
        self.assertNotIn("target", plan)


class DecisionEvidenceTest(unittest.TestCase):
    setUp = alert_tests.VideoAlertTest.setUp
    decision = alert_tests.VideoAlertTest.decision
    def test_frozen_decision_preserves_costs_reason_and_both_turns(self):
        rows = reversal()
        status, _ = self.decision(rows)
        self.assertEqual(status, "send_disabled")
        row = self.ledger.db.execute("SELECT status,evidence FROM video_alert_decisions").fetchone()
        self.assertEqual(row[0], "send_disabled")
        e = json.loads(row[1])
        self.assertEqual(e["costs"]["min_rr"], 1.5)
        self.assertIn("first_confirmation_start", e["events"][0])
        self.assertIn("book", e)
        self.assertEqual(len(e["input_sha256"]), 64)
        self.decision(rows, now_offset=31)
        self.assertEqual(self.ledger.db.execute("SELECT status,evidence FROM video_alert_decisions").fetchone(), row)

    def test_no_signal_has_exact_stage_in_durable_ledger(self):
        self.decision(reversal()[:-1])
        e = json.loads(self.ledger.db.execute("SELECT evidence FROM video_alert_decisions").fetchone()[0])
        self.assertEqual(e["events"], [])
        self.assertIn("rsi_long_second_extreme_outside_band", e["reasons"])

    def test_same_pattern_cannot_send_on_another_bar_after_restart(self):
        self.assertTrue(self.ledger.claim(300, "rsi_reversal", "pattern-1"))
        other = alert_tests.DeliveryLedger(self.path)
        self.addCleanup(other.db.close)
        self.assertFalse(other.claim(600, "rsi_reversal", "pattern-1"))
        self.assertTrue(other.claim(600, "rsi_reversal", "pattern-2"))


if __name__ == "__main__":
    unittest.main()
