"""Sequence semantics, causal timing, target geometry and durable explanations."""
from copy import deepcopy
import json
import sqlite3
import unittest
from unittest.mock import Mock, patch

from lbank_video_alert import LIVE_POLICY, SHADOW_POLICY, PublicBook, process
from research.zerobase.rsi_sequence import MAX_SETUP_BARS, POLICY, sequence_series
from research.zerobase.video_entry import Costs, _plan, _series, replay
from test_lbank_video_entry import ARCHIVE, breakout, mirror, reversal as incomplete_reversal
from research.zerobase.video_entry import read_retained_archive
import test_lbank_video_alert as alert_tests


def sequence_reversal():
    # Two separate body-engulfing reversals, unlike the old fixture whose
    # first rebound never engulfed its preceding large bearish body.
    closes = [1020]*34 + [1019, 1018, 1017, 1016, 1015, 1014, 1013,
                          997, 1013.2, 1010, 1008, 1007, 1006, 1005, 999, 1000.6]
    rows = []
    for i, close in enumerate(closes):
        opening = {42: 996.9, 48: 1000.5, 49: 998.95}.get(i, closes[max(0, i-1)])
        rows.append(dict(start=300*(i+1), open=opening, close=close,
                         high=max(opening, close)+.15, low=min(opening, close)-.15,
                         volume=100, quality="stream_closed"))
    return rows


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
            self.assertFalse(replay(b, nominal=True, rsi_sequence=True)["events"])

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
            setup = dict(lane="rsi_reversal", direction=d, policy=POLICY,
                         anchor=999.8 if d == 1 else 1000.2)
            legacy = _plan({k: v for k, v in setup.items() if k != "policy"}, bars, series,
                           [b["start"]+300 for b in bars], len(bars)-1, Costs(), True)
            self.assertEqual(legacy["status"], "center_already_reached")
            plan = _plan(setup, bars, series, [b["start"]+300 for b in bars],
                         len(bars)-1, Costs(), True)
            self.assertEqual(plan["status"], "qualified")
            self.assertEqual(plan["target_basis"], "confirmed_pivot")
            self.assertGreaterEqual(plan["net_rr_at_signal_close"], Costs().min_rr)
            self.assertLess(d*(plan["target"]-plan["barrier"]), 0)

    def test_center_reclaimed_without_structure_cannot_invent_three_r_target(self):
        bars = breakout()[:46]
        bars[25]["high"] = 1000.2
        setup = dict(lane="rsi_reversal", direction=1, policy=POLICY, anchor=999.8)
        plan = _plan(setup, bars, _series(bars), [b["start"]+300 for b in bars],
                     len(bars)-1, Costs(), True)
        self.assertEqual(plan["status"], "no_known_target")
        self.assertNotIn("target", plan)

    def test_development_archive_keeps_cci_identical_and_proves_no_profitability(self):
        bars = read_retained_archive(ARCHIVE)
        old, new = (replay(bars, nominal=True, rsi_sequence=v) for v in (False, True))
        self.assertEqual((old["bars"], old["evaluated_bars"]), (581, 361))
        self.assertEqual((old["candidate_counts"]["rsi_reversal"],
                          new["candidate_counts"]["rsi_reversal"]), (5, 3))
        self.assertEqual([e for e in old["events"] if e["lane"] == "bb_cci_breakout"],
                         [e for e in new["events"] if e["lane"] == "bb_cci_breakout"])
        self.assertTrue(all(e["status"] != "qualified" for r in (old, new) for e in r["events"]))


class DecisionEvidenceTest(unittest.TestCase):
    setUp = alert_tests.VideoAlertTest.setUp
    decision = alert_tests.VideoAlertTest.decision

    def observe(self, rows, *, send=None, quote=None, now_offset=2):
        rows = alert_tests.with_receipts(rows)
        end = rows[-1]["start"]+300
        now = end+now_offset
        return process(rows, now=now, started=end-1, clock=lambda: now,
                       get_book=quote or (lambda: (rows[-1]["close"]-.1, rows[-1]["close"]+.1)),
                       ledger=self.ledger, send=send, shadow=True)

    def evidence(self, policy):
        status, payload = self.ledger.db.execute(
            "SELECT status,evidence FROM video_alert_decisions WHERE policy=?",
            (policy,)).fetchone()
        return status, json.loads(payload)

    def test_shadow_only_uses_verified_quote_without_sending_or_reserving(self):
        rows = sequence_reversal()
        send = Mock(return_value=True)
        ws = Mock(return_value=(rows[-1]["close"]-.1, rows[-1]["close"]+.1))
        rest = Mock(side_effect=AssertionError("WS should suffice"))
        quote = PublicBook(rest, alternate=ws)
        with patch("lbank_video_alert.evaluate_latest", return_value=([], {})):
            status, _ = self.observe(rows, quote=quote, send=send)
        self.assertEqual(status, "no_qualified_entry")
        send.assert_not_called()
        ws.assert_called_once()
        rest.assert_not_called()
        self.assertEqual(self.ledger.db.execute("SELECT count(*) FROM video_alert_delivery").fetchone()[0], 0)
        self.assertEqual(self.ledger.db.execute("SELECT count(*) FROM video_alert_patterns").fetchone()[0], 0)
        state, e = self.evidence(SHADOW_POLICY)
        self.assertEqual(state, "shadow_eligible")
        self.assertEqual(e["delivery"], "shadow_only")
        self.assertEqual(e["costs"]["min_rr"], 1.5)
        self.assertEqual(e["book"]["source"], "websocket")
        self.assertIn("first_confirmation_start", e["events"][0])
        self.assertIn("목표 근거", e["alert_preview"])
        self.assertIn("두 번째 반전 마감", e["alert_preview"])
        self.assertEqual(len(e["input_sha256"]), 64)
        self.observe(rows, now_offset=31)
        self.assertEqual(self.evidence(SHADOW_POLICY), (state, e))

    def test_no_signal_has_exact_stage_in_durable_ledger(self):
        self.observe(sequence_reversal()[:-1])
        _, e = self.evidence(SHADOW_POLICY)
        self.assertEqual(e["events"], [])
        self.assertIn("rsi_long_second_extreme_outside_band", e["reasons"])

    def test_live_and_shadow_share_one_quote_and_only_live_can_send(self):
        rows = sequence_reversal()
        start, close = rows[-1]["start"], rows[-1]["close"]
        cci = dict(lane="bb_cci_breakout", direction=1, status="qualified", signal_start=start,
                   trigger=1000.3, stop=998, target=1010, entry_bound=1001)
        quote = Mock(return_value=(close-.1, close+.1))
        send = Mock(return_value=True)
        with patch("lbank_video_alert.evaluate_latest", return_value=([cci], {})):
            self.assertEqual(self.observe(rows, quote=quote, send=send)[0], "telegram_accepted")
        quote.assert_called_once()
        send.assert_called_once()
        self.assertIn("볼린저밴드·CCI", send.call_args.args[0])
        state, evidence = self.evidence(SHADOW_POLICY)
        self.assertEqual(state, "shadow_eligible")
        self.assertTrue(evidence["book"]["reused_live_evaluation"])
        self.assertEqual(evidence["input_sha256"], self.evidence(LIVE_POLICY)[1]["input_sha256"])

    def test_same_pattern_cannot_send_on_another_bar_after_restart(self):
        self.assertTrue(self.ledger.claim(300, "rsi_reversal", "pattern-1"))
        other = alert_tests.DeliveryLedger(self.path)
        self.addCleanup(other.db.close)
        self.assertFalse(other.claim(600, "rsi_reversal", "pattern-1"))
        self.assertTrue(other.claim(600, "rsi_reversal", "pattern-2"))

    def test_existing_delivery_table_migrates_without_erasing_reservation(self):
        path = self.path.with_name("old.db")
        with sqlite3.connect(path) as db:
            db.execute("CREATE TABLE video_alert_delivery (bar_start INTEGER PRIMARY KEY, lane TEXT, status TEXT)")
            db.execute("INSERT INTO video_alert_delivery VALUES (300,'bb_cci_breakout','reserved')")
        migrated = alert_tests.DeliveryLedger(path)
        self.addCleanup(migrated.db.close)
        self.assertFalse(migrated.claim(300, "rsi_reversal", "not-consumed"))
        self.assertTrue(migrated.claim(600, "rsi_reversal", "not-consumed"))

    def test_diagnostic_and_delivery_finalization_failure_do_not_hide_api_acceptance(self):
        send = Mock(return_value=True)
        with patch.object(self.ledger, "record_decision", side_effect=sqlite3.OperationalError), \
             patch.object(self.ledger, "finish", side_effect=sqlite3.OperationalError), \
             self.assertLogs("lbank_video_alert", "WARNING") as logs:
            self.assertEqual(self.observe(breakout()[:46], send=send)[0], "telegram_accepted")
        send.assert_called_once()
        self.assertTrue(any("DELIVERY_STATUS_FAILURE" in line for line in logs.output))
        self.assertTrue(any("EVIDENCE_FAILURE" in line for line in logs.output))
        self.assertEqual(self.ledger.db.execute("SELECT status FROM video_alert_delivery").fetchone()[0], "reserved")

    def test_live_rsi_repeat_does_not_block_independent_cci_after_restart(self):
        rows = breakout()[:46]
        start = rows[-1]["start"]
        first = rows[-10]["start"]
        cci = dict(lane="bb_cci_breakout", direction=1, status="qualified", signal_start=start,
                   trigger=1002, stop=999, target=1020, entry_bound=1006)
        rsi = dict(cci, lane="rsi_reversal", first_start=first, second_start=start-300,
                   anchor=1000.5, stop=1000.3)
        self.assertTrue(self.ledger.claim(start-300, "rsi_reversal", f"rsi_legacy:1:{first}"))
        self.ledger = alert_tests.DeliveryLedger(self.path)
        self.addCleanup(self.ledger.db.close)
        send = Mock(return_value=True)
        with patch("lbank_video_alert.evaluate_latest", return_value=([rsi, cci], {})):
            self.assertEqual(self.observe(rows, send=send)[0], "telegram_accepted")
        send.assert_called_once()
        self.assertIn("볼린저밴드·CCI", send.call_args.args[0])
        self.assertNotIn("RSI 반전형", send.call_args.args[0])
        self.assertEqual(self.evidence(LIVE_POLICY)[1]["selected"]["lane"], "bb_cci_breakout")


if __name__ == "__main__":
    unittest.main()
