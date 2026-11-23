"""Regression: a fresh closed-minute break must not inherit stale flat context.

Synthetic native-shaped inputs exercise both sides; these are not real fills
or a claim about profit in the November 24 incident.
"""
import copy
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

import lbank_futures_monitor as m
from lbank_native_entry import native_context_gate
from lbank_sample_store import load_snapshots, save_snapshots
from lbank_sealed_breakout import (SOURCE, arm_stopped_retry, sealed_breakout,
                                  stopped_retry_allowed)


def fixture(d=-1):
    end = 1795451760  # 2026-11-24 01:36 KST; last closed minute.
    closes = [62580. + (i % 5 - 2) * 8 for i in range(80)]
    bars = []
    for i, c in enumerate(closes):
        o = closes[max(0, i - 1)]
        bars.append(dict(start=end - (79-i)*60, quality="stream_closed",
                         open=o, high=max(o, c)+6, low=min(o, c)-6,
                         close=c, volume=10., turnover=625800.))
    bars[-1].update(open=62580., close=62580.+d*130,
                    high=max(62580., 62580.+d*130)+2,
                    low=min(62580., 62580.+d*130)-2, volume=30.)
    closes[-1] = bars[-1]["close"]
    observed = end+65
    last5 = int(observed//300)*300-300
    five = [dict(start=last5-(19-i)*300, quality="stream_closed",
                 open=62580., high=62700., low=62400., close=62580.+d*i*2,
                 volume=50., turnover=3129000.) for i in range(20)]
    level = (max(b["high"] for b in bars[-16:-1]) if d == 1
             else min(b["low"] for b in bars[-16:-1]))
    s = dict(source=SOURCE, observed_at=observed, minute_bars=bars,
             five_bars=five, prices=closes, quote=closes[-1], last5_start=last5,
             rsi5=dict(closes=[b["close"] for b in five], rsi=[50., 50.+d*5]),
             closes15=[62580.+d*x for x in (-300, -200, -100, -40, 0)])
    return s, level


class SealedBreakoutContextTests(unittest.TestCase):
    def test_flat_completed_fives_do_not_veto_a_new_sealed_break_both_sides(self):
        for d in (-1, 1):
            s, level = fixture(d)
            old = native_context_gate(s, d, "breakout")
            self.assertEqual(old[1], "flat_sampled_market")
            new = native_context_gate(s, d, "breakout", breakout_level=level,
                                      allow_sealed_breakout=True)
            self.assertTrue(new[0], new)
            self.assertEqual(new[1], "context_sealed_breakout")
            self.assertTrue(new[2]["sealed_flat_breakout"]["detected"])

    def test_quote_only_wick_only_or_weak_break_cannot_unlock_context(self):
        for kind in ("quote", "wick", "weak_body", "low_volume", "wrong_side"):
            s, level = fixture()
            b = s["minute_bars"][-1]
            if kind == "quote":
                b.update(close=b["open"], low=b["open"]-2)
                s["prices"][-1] = b["close"]
            elif kind == "wick":
                b["close"] = b["open"]
                s["prices"][-1] = b["close"]
                s["quote"] = b["close"]
            elif kind == "weak_body":
                b["open"] = b["close"]+1
            elif kind == "low_volume":
                b["volume"] = 1.
            with self.subTest(kind=kind):
                self.assertFalse(sealed_breakout(s, 1 if kind == "wrong_side" else -1,
                                                level)["detected"])

    def test_future_forming_gapped_stale_or_misaligned_candles_fail_closed(self):
        for kind in ("future", "forming", "gap", "stale", "misaligned", "wrong_source", "nan"):
            s, level = fixture()
            if kind == "future": s["observed_at"] -= 60
            if kind == "forming": s["minute_bars"][-1]["quality"] = "forming"
            if kind == "gap": s["minute_bars"][-4]["start"] += 60
            if kind == "stale": s["observed_at"] += 120
            if kind == "misaligned": s["prices"][-1] += 1
            if kind == "wrong_source": s["source"] = "spot"
            if kind == "nan": s["minute_bars"][-1]["volume"] = float("nan")
            with self.subTest(kind=kind):
                self.assertFalse(sealed_breakout(s, -1, level)["detected"])

    def test_range_reaction_and_strong_countertrend_remain_blocked(self):
        s, level = fixture()
        self.assertFalse(native_context_gate(s, -1, "range_rebound",
            breakout_level=level, allow_sealed_breakout=True)[0])
        s["closes15"] = [62000., 62000., 62200., 62500., 63000.]
        result = native_context_gate(s, -1, "breakout", breakout_level=level,
                                     allow_sealed_breakout=True)
        self.assertFalse(result[0])
        self.assertEqual(result[1], "avoid_15m_strong_countertrend")

    def test_context_override_cannot_approve_a_plan_rejected_by_risk(self):
        s, level = fixture()
        setup = dict(strategy="breakout", direction=-1, level=level)
        with patch.object(m, "ENTRY_REFINEMENT_MODE", "capture"), \
             patch.object(m, "calculate_plan", return_value=None) as planner:
            diag = {}
            self.assertIsNone(m.assess_candidate(s["prices"], -1, setup,
                                                native_state=s, diagnostics=diag))
            self.assertEqual(diag["reason"], "risk_or_target")
            planner.assert_called_once()


class StoppedRetryTests(unittest.TestCase):
    def setUp(self):
        self.mode = patch.object(m, "ENTRY_REFINEMENT_MODE", "capture")
        self.mode.start()
        self.addCleanup(self.mode.stop)
        self.quiet = patch.object(m, "is_quiet_new_entry_time", return_value=False)
        self.quiet.start()
        self.addCleanup(self.quiet.stop)
        self.s, level = fixture()
        self.now = self.s["observed_at"]
        self.sent = self.now - 260
        self.state = dict(day=None, count=0, last_time=None, last_side=None)
        m.record_alert(self.sent, "숏", self.state,
                       plan=dict(direction=-1, price=62580.1))
        self.tracked = SimpleNamespace(sent_at=self.sent, direction=-1)
        self.plan = dict(direction=-1, strategy="breakout", price=self.s["quote"],
                         sealed_breakout_evidence=sealed_breakout(self.s, -1, level))

    def arm(self):
        self.assertTrue(arm_stopped_retry(self.state, self.tracked, "stop_breached", self.now-90))

    def test_only_latest_stopped_signal_can_arm_a_retry(self):
        self.assertEqual(m.alert_gate(self.now, "숏", self.state, self.plan), "global_cooldown")
        self.assertFalse(arm_stopped_retry(self.state, self.tracked, "early_invalidation", self.now-90))
        old = SimpleNamespace(sent_at=self.sent-1, direction=-1)
        self.assertFalse(arm_stopped_retry(self.state, old, "stop_breached", self.now-90))
        self.arm()
        self.assertEqual(m.alert_gate(self.now, "숏", self.state, self.plan), "ok")

    def test_one_retry_does_not_reset_daily_cap_or_allow_another_retry(self):
        self.arm()
        capped = copy.deepcopy(self.state)
        capped["count"] = m.MAX_DAILY_ALERTS
        self.assertEqual(m.alert_gate(self.now, "숏", capped, self.plan), "daily_cap")
        m.record_alert(self.now, "숏", self.state, self.plan)
        self.assertEqual(self.state["count"], 2)
        self.assertEqual(self.state["entry_retry"]["used"], 1)
        self.assertFalse(arm_stopped_retry(self.state,
            SimpleNamespace(sent_at=self.now, direction=-1), "stop_breached", self.now+1))
        self.assertEqual(m.alert_gate(self.now+2, "숏", self.state, self.plan), "global_cooldown")

    def test_pre_stop_old_or_unqualified_breakout_cannot_retry(self):
        self.arm()
        for kind in ("pre_stop", "stale", "not_breakout", "not_new_low", "not_detected", "opposite"):
            p = copy.deepcopy(self.plan)
            proof = p["sealed_breakout_evidence"]
            if kind == "pre_stop": proof["last_closed_1m_start"] = self.now-100
            if kind == "stale": proof["closed_at"] = self.now-91
            if kind == "not_breakout": p["strategy"] = "trend_retest"
            if kind == "not_new_low": p["price"] = 62600.
            if kind == "not_detected": proof["detected"] = False
            if kind == "opposite": p["direction"] = 1
            with self.subTest(kind=kind):
                self.assertFalse(stopped_retry_allowed(self.now, "숏", self.state, p))

    def test_restart_preserves_armed_and_used_retry_without_granting_new_budget(self):
        self.arm()
        with tempfile.TemporaryDirectory() as directory:
            path = directory+"/cache.json"
            samples = [(self.now-60, 62550.), (self.now, 62450.)]
            save_snapshots(path, m.SYMBOL, m.POLL, samples, self.state)
            _, restored = load_snapshots(path, m.SYMBOL, m.POLL, now=self.now)
            self.assertTrue(stopped_retry_allowed(self.now, "숏", restored, self.plan))
            m.record_alert(self.now, "숏", restored, self.plan)
            save_snapshots(path, m.SYMBOL, m.POLL, samples, restored)
            _, twice = load_snapshots(path, m.SYMBOL, m.POLL, now=self.now+1)
            self.assertEqual(twice["entry_retry"]["used"], 1)
            self.assertFalse(stopped_retry_allowed(self.now+1, "숏", twice, self.plan))


if __name__ == "__main__":
    unittest.main()
