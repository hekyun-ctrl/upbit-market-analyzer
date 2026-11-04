"""Tests of signal causality, directional geometry, and honest replay outcomes."""
from copy import deepcopy
from pathlib import Path
import unittest

from research.zerobase.video_entry import read_retained_archive, replay

ARCHIVE = Path(__file__).parent / "research/zerobase/entry_comparison_20261024/archive_chunks.json"


def breakout():
    bars = [dict(start=300*(i+1), open=1000, high=1000.2, low=999.8,
                 close=1000, volume=100, quality="stream_closed") for i in range(50)]
    bars[25]["high"] = 1015  # Prior known barrier, not the break level itself.
    bars[45].update(close=1003, high=1003.3, volume=160)
    bars[46].update(open=1003, close=1008, high=1016, low=1002.5)
    return bars


def mirror(bars):
    return [dict(b, open=2000-b["open"], close=2000-b["close"],
                 high=2000-b["low"], low=2000-b["high"]) for b in bars]


def incomplete_reversal():
    first, second = 997, 997.7
    closes = [1020]*34 + [1019, 1018, 1017, 1016, 1015, 1014, 1013,
                          first, first+6, first+8, second, second+1.6]
    rows = []
    for i, close in enumerate(closes):
        opening = {42: first+.3, 44: second+1.5, 45: second-.05}.get(
            i, closes[max(0, i-1)])
        rows.append(dict(start=300*(i+1), open=opening, close=close,
                         high=max(opening, close)+.15, low=min(opening, close)-.15,
                         volume=100, quality="stream_closed"))
    return rows


def reversal():
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


class VideoEntryTest(unittest.TestCase):
    def test_engulfing_confirms_reversal_at_close_in_both_directions(self):
        for d, rows in ((1, reversal()), (-1, mirror(reversal()))):
            with self.subTest(direction=d):
                self.assertFalse(replay(rows[:-1], nominal=True)["events"])
                e, = replay(rows, nominal=True)["events"]
                self.assertEqual((e["lane"], e["direction"], e["status"]),
                                 ("rsi_reversal", d, "qualified"))
                self.assertEqual(e["detected_at"], rows[-1]["start"]+300)
                self.assertTrue(e["second_inside_band"])
                self.assertLess(d*(e["exhaustion_rsi"]-50), -20)
                self.assertGreater(d*(e["signal_close"]-e["stop"]), 0)

    def test_confirmed_breakout_can_qualify_without_retest_in_both_directions(self):
        for d, rows in ((1, breakout()), (-1, mirror(breakout()))):
            with self.subTest(direction=d):
                report = replay(rows, nominal=True)
                e, = report["events"]
                self.assertEqual((e["lane"], e["direction"], e["status"]),
                                 ("bb_cci_breakout", d, "qualified"))
                self.assertFalse(e["retest_required"])
                self.assertLess(e["barrier_available_at"], e["detected_at"])
                self.assertGreater(d*(e["barrier"]-e["target"]), 0)
                self.assertGreater(d*(e["entry_bound"]-e["signal_close"]), 0)
                self.assertEqual(e["outcome"]["status"], "target")

    def test_zero_and_low_volume_do_not_pass_breakout_lane(self):
        for v, why in ((0, "volume_baseline_missing"), (100, "volume_below_threshold")):
            bars = breakout()[:46]
            for b in bars[:-1]:
                b["volume"] = v
            if v:
                bars[-1]["volume"] = 149
            report = replay(bars, nominal=True)
            self.assertFalse(report["events"])
            self.assertEqual(report["pre_candidate_rejections"].get(why), 1)

    def test_unseen_target_cannot_be_invented(self):
        bars = breakout()
        bars[25]["high"] = 1000.2
        e, = replay(bars, nominal=True)["events"]
        self.assertEqual(e["status"], "no_known_target")
        self.assertNotIn("target", e)

    def test_entry_bound_and_barrier_do_not_move_to_rescue_next_open(self):
        bars = breakout()[:47]
        bars[46].update(open=1006, low=1004)
        e, = replay(bars, nominal=True)["events"]
        self.assertEqual(e["status"], "qualified")
        self.assertEqual(e["target"], 1014.8)
        self.assertEqual(e["outcome"]["status"], "next_open_stop_too_far")

    def test_same_bar_target_and_stop_are_unresolved(self):
        bars = breakout()
        bars[46]["low"] = 999
        e = replay(bars, nominal=True)["events"][0]
        self.assertEqual(e["outcome"]["status"], "same_bar_unknown")
        self.assertNotIn("model_pnl_per_btc", e["outcome"])

    def test_delayed_receipt_cannot_fill_at_an_earlier_open(self):
        bars = breakout()
        for b in bars:
            b["received_at"] = b["start"] + 305
        e, = replay(bars)["events"]
        self.assertEqual(e["detected_at"], bars[45]["received_at"])
        self.assertEqual(e["outcome"]["status"], "open_precedes_receipt")

    def test_no_receipt_or_forming_or_duplicate_without_explicit_valid_input(self):
        bars = breakout()
        with self.assertRaises(ValueError):
            replay(bars)
        for replacement in (dict(quality="forming"), dict(received_at=bars[-1]["start"])):
            edited = deepcopy(bars)
            edited[-1].update(replacement)
            with self.assertRaises(ValueError):
                replay(edited, nominal=True)
        with self.assertRaises(ValueError):
            replay(bars + bars[-1:], nominal=True)

    def test_gap_restarts_warmup_and_does_not_bridge_trade(self):
        bars = breakout()[:46] + breakout()[47:]
        report = replay(bars, nominal=True)
        self.assertEqual(report["contiguous_runs"], 2)
        e, = report["events"]
        self.assertEqual(e["outcome"]["status"], "no_followup")

    def test_retained_reversal_and_breakout_events_are_prefix_invariant(self):
        bars = read_retained_archive(ARCHIVE)
        report = replay(bars, nominal=True, with_outcomes=False)
        self.assertEqual(report["bars"], 581)
        self.assertEqual(report["candidate_counts"], {"rsi_reversal": 3, "bb_cci_breakout": 26})
        self.assertEqual(report["plan_counts"], {"no_known_target": 13, "insufficient_net_rr": 16})
        for i, b in enumerate(bars):
            if any(e["signal_start"] == b["start"] for e in report["events"]):
                with self.subTest(signal=b["start"]):
                    prefix = replay(bars[:i+1], nominal=True, with_outcomes=False)["events"]
                    self.assertEqual(prefix, [e for e in report["events"]
                                              if e["signal_start"] <= b["start"]])
        reversals = [e for e in report["events"] if e["lane"] == "rsi_reversal"]
        self.assertEqual({e["direction"] for e in reversals}, {1, -1})
        self.assertTrue(all(e["first_start"] < e["second_start"] <= e["signal_start"]
                            and e["second_inside_band"] for e in reversals))


if __name__ == "__main__":
    unittest.main()
