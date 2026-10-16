"""Offline unit checks for LBank BTC cost-aware watch signal.

Run: python -m unittest test_lbank_fee_risk.py
"""
import unittest
from unittest.mock import patch
from lbank_signal_risk import SignalRiskTracker, format_risk_alert, format_target_alert, format_target2_alert


from lbank_futures_monitor import (
    REF_BALANCE, RISK_FRACTION, MAX_MARGIN_FRACTION, LEVERAGE, classify,
    alert_gate, record_alert, verified_reversal_exception,
    format_reversal_pre_watch, COOLDOWN, REVERSAL_COOLDOWN, MAX_DAILY_ALERTS,
    minimum_net_rr_target, net_pnl_per_btc,
    confirm_watch, format_entry_alert, trend_retest_momentum_gate,
    sampled_five_minute_rsi, five_minute_rsi_entry_gate,
    fast_sample_rsi_gate, confirmed_sampled_5m_oversold_rebound,
)


class LBankCostModelTests(unittest.TestCase):
    def test_flat_price_is_loss_after_fees_slippage_and_funding_reserve(self):
        for side in (-1, 1):
            self.assertLess(net_pnl_per_btc(side, 83000, 83000), -70.0)

    def test_both_directions_targets_clear_net_rr(self):
        for direction in (-1, 1):
            entry = 83000.
            stop = entry - direction * 260
            loss = -net_pnl_per_btc(direction, entry, stop)
            self.assertGreater(loss, 0)
            for ratio in (1.5, 2.5):
                target = minimum_net_rr_target(direction, entry, loss, ratio)
                self.assertIsNotNone(target)
                self.assertGreater(direction * (target - entry), 0)
                self.assertAlmostEqual(net_pnl_per_btc(direction, entry, target, exit_kind="take_profit") / loss,
                                       ratio, places=5)

    def test_maker_profit_and_taker_stop_are_distinct(self):
        entry = 83000.
        for direction in (-1, 1):
            # A resting maker TP is cheaper than immediate taker liquidation.
            same_target_as_stop = entry + direction * 500
            take_profit = net_pnl_per_btc(direction, entry, same_target_as_stop,
                                           exit_kind="take_profit")
            taker_exit = net_pnl_per_btc(direction, entry, same_target_as_stop,
                                         exit_kind="stop")
            self.assertGreater(take_profit, taker_exit)
        # LBank conditional market TP is charged Taker; break-even is a loss.
        self.assertAlmostEqual(
            net_pnl_per_btc(1, 83000, 83000, exit_kind="take_profit"),
            -(83000 * (0.0006 + 0.0006 + 0.0001)), places=6)
        # 287.4 USDT margin x 50 = 14,370 USDT nominal:
        self.assertAlmostEqual(14370 * (0.0002 + 0.0002), 5.748)
        self.assertAlmostEqual(14370 * (0.0002 + 0.0006), 11.496)

    def test_no_alert_before_60_samples(self):
        self.assertIsNone(classify([80000. + i * 25 for i in range(59)]))

    def test_risk_limit_and_target_after_realistic_uptrend(self):
        # Alternating advances and small declines produce RSI < 80 while
        # preserving a confirmed high and strong short-term momentum.
        data = [80000. + 25 * i + (45 if i % 2 else 0) for i in range(60)]
        signal = classify(data)
        self.assertIsNotNone(signal)
        self.assertEqual(signal["side"], "롱")
        self.assertLessEqual(signal["net_loss"], REF_BALANCE * RISK_FRACTION + 1e-8)
        self.assertLessEqual(signal["margin"], REF_BALANCE * MAX_MARGIN_FRACTION + 1e-8)
        self.assertGreaterEqual(signal["rr1"], 1.5 - 1e-7)
        self.assertGreaterEqual(signal["rr2"], 2.5 - 1e-7)
        self.assertGreater(signal["fee_roundtrip_est"], 0)
        self.assertAlmostEqual(signal["quantity_btc"] * signal["price"],
                               signal["notional"], places=7)
        self.assertAlmostEqual(signal["reference_risk_budget"],
                               REF_BALANCE * RISK_FRACTION, places=6)
        self.assertGreater(signal["stop_distance_pct"], 0)
        self.assertGreater(signal["sampled_1m_abs_move_p90_pct"], 0)

    def test_two_stage_watch_converts_to_entry_review_with_confirmation(self):
        base = [80000. + 25 * i + (45 if i % 2 else 0) for i in range(60)]
        watch = classify(base)
        self.assertIsNotNone(watch)
        self.assertEqual(watch["direction"], 1)
        # Next one-minute quote stabilizes near the breakout rather than chasing.
        now = base + [base[-1] + 5]
        confirmed = confirm_watch(now, watch, 60)
        self.assertIsNotNone(confirmed)
        self.assertEqual(confirmed["side"], "롱")
        self.assertLessEqual(confirmed["net_loss"], REF_BALANCE * RISK_FRACTION + 1e-8)
        self.assertGreaterEqual(confirmed["rr1"], 1.5 - 1e-7)
        alert = format_entry_alert(confirmed, "2026-10-13T00:00:00Z")
        self.assertIn("조건부 진입 검토", alert)
        self.assertIn("예상 총손실", alert)
        self.assertIn(f"{confirmed['margin']:.2f} USDT", alert)
        self.assertIn("진입 증거금", alert)
        self.assertNotIn(" BTC\n", alert)
        self.assertNotIn("참고 최대 진입수량", alert)
        self.assertIn(f"레버리지 {LEVERAGE:.0f}배", alert)
        self.assertIn("관찰 신호", alert)

    def test_stage_two_rejects_stale_watch_and_price_chasing(self):
        base = [80000. + 25 * i + (45 if i % 2 else 0) for i in range(60)]
        watch = classify(base)
        self.assertIsNotNone(watch)
        self.assertIsNone(confirm_watch(base + [base[-1]], watch, 300))
        self.assertIsNone(confirm_watch(base + [base[-1] * 1.003], watch, 60))

    def test_avoids_narrow_targets_that_only_cover_fees(self):
        entry = 82000.
        loss = -net_pnl_per_btc(1, entry, entry * (1 - .0025))
        target = minimum_net_rr_target(1, entry, loss, 1.5)
        self.assertGreater((target - entry) / entry, .005)  # Fees still require a material gross move




class WeakRetestMomentumRegressionTests(unittest.TestCase):
    """Protect failed 00:58 signal without relabeling past TP1 hits as failures."""

    @staticmethod
    def synthetic(direction, five_pct, fifteen_pct):
        last = 82445.6
        prices = [last] * 70
        prices[-6] = last / (1 + direction * five_pct / 100)
        prices[-16] = last / (1 + direction * fifteen_pct / 100)
        return prices

    def test_failed_long_price_impulse_blocked_both_stages(self):
        for direction in (1, -1):
            with self.subTest(direction=direction):
                prices = self.synthetic(direction, .04, .01)
                ok, reason, move5, move15 = trend_retest_momentum_gate(
                    prices, direction)
                self.assertFalse(ok)
                self.assertEqual(reason, "weak_retest_momentum_both_horizons")
                self.assertAlmostEqual(move5, .04, places=5)
                self.assertAlmostEqual(move15, .01, places=5)
                # Final stage must not accept a watch created earlier if the
                # momentum fades, even when structural confirmation holds.
                with patch("lbank_futures_monitor.detect_structure",
                           return_value={"direction": direction,
                                         "strategy": "trend_retest"}), \
                     patch("lbank_futures_monitor.calculate_plan") as calc:
                    self.assertIsNone(classify(prices))
                    calc.assert_not_called()
                watch = {"direction": direction, "strategy": "trend_retest",
                         "price": prices[-1]}
                with patch("lbank_futures_monitor.confirm_structure",
                           return_value=True), \
                     patch("lbank_futures_monitor.calculate_plan") as calc:
                    self.assertIsNone(confirm_watch(prices, watch, 60))
                    calc.assert_not_called()

    def test_both_horizons_weak_blocks_preliminary_failed_watch(self):
        prices = self.synthetic(1, .09, -.09)
        ok, reason, _, _ = trend_retest_momentum_gate(prices, 1)
        self.assertFalse(ok)
        self.assertEqual(reason, "weak_retest_momentum_both_horizons")

    def test_past_short_and_long_cases_keep_sufficient_momentum(self):
        # Recorded ALARM fields: 21:51 SHORT -0.15/-0.03,
        # 23:37 LONG +0.23/+0.10 (both sampled, not exchange OHLCV).
        for direction, five, fifteen in ((-1, .15, .03), (1, .23, .10)):
            with self.subTest(direction=direction):
                prices = self.synthetic(direction, five, fifteen)
                ok, reason, _, _ = trend_retest_momentum_gate(prices, direction)
                self.assertTrue(ok)
                self.assertEqual(reason, "retest_momentum_confirmed")
                with patch("lbank_futures_monitor.detect_structure",
                           return_value={"direction": direction,
                                         "strategy": "trend_retest"}), \
                     patch("lbank_futures_monitor.calculate_plan",
                           return_value={"direction": direction,
                                         "strategy": "trend_retest"}) as calc:
                    self.assertIsNotNone(classify(prices))
                    calc.assert_called_once()
                watch = {"direction": direction, "strategy": "trend_retest",
                         "price": prices[-1]}
                with patch("lbank_futures_monitor.confirm_structure",
                           return_value=True), \
                     patch("lbank_futures_monitor.calculate_plan",
                           return_value={"direction": direction,
                                         "strategy": "trend_retest"}) as calc:
                    self.assertIsNotNone(confirm_watch(prices, watch, 60))
                    calc.assert_called_once()

    def test_strong_single_horizon_suffices_and_boundaries_are_inclusive(self):
        for five, fifteen in ((.10, 0.), (0., .08), (.20, -.15),
                              (-.05, .23)):
            with self.subTest(five=five, fifteen=fifteen):
                values = self.synthetic(1, five, fifteen)
                ok, _, _, _ = trend_retest_momentum_gate(values, 1)
                self.assertTrue(ok)
        self.assertFalse(trend_retest_momentum_gate([], 1)[0])
        self.assertFalse(trend_retest_momentum_gate([82000.] * 70, 0)[0])

    def test_other_structural_strategies_not_subject_to_retest_gate(self):
        prices = self.synthetic(1, .02, .02)
        for strategy in ("breakout", "range_rebound"):
            with self.subTest(strategy=strategy), \
                 patch("lbank_futures_monitor.detect_structure",
                       return_value={"direction": 1, "strategy": strategy}), \
                 patch("lbank_futures_monitor.calculate_plan",
                       return_value={"direction": 1, "strategy": strategy}) as calc:
                self.assertIsNotNone(classify(prices))
                calc.assert_called_once()


class ReversalCooldownReviewTests(unittest.TestCase):
    @staticmethod
    def state(now, elapsed=75 * 60):
        from datetime import datetime, timezone, timedelta
        day = datetime.fromtimestamp(
            now, tz=timezone(timedelta(hours=9))).date().isoformat()
        return {"day": day, "count": 1, "last_time": now - elapsed,
                "last_side": "숏", "last_reversal_preview": None}

    @staticmethod
    def plan():
        return {"side": "롱", "direction": 1, "strategy": "breakout",
                "price": 80550.9, "stop": 80077.0, "target1": 81400.,
                "rr1": 1.5, "net_loss": REF_BALANCE * RISK_FRACTION * .99,
                "rsi_direction_status": "5m_rsi_direction_aligned",
                "sampled_context": {
                    "reversal_confirmed": True,
                    "reversal_10m_pct": .788, "reversal_last5_pct": .45,
                    "trend5_pct": .06, "trend15_pct": -.46}}

    def test_verified_reversal_bypasses_only_opposing_90m_cooldown(self):
        now = 1800000000
        state = self.state(now)
        plan = self.plan()
        self.assertEqual(alert_gate(now, "롱", state), "reverse_cooldown")
        self.assertTrue(verified_reversal_exception(now, "롱", state, plan))
        self.assertEqual(alert_gate(now, "롱", state, plan), "ok")
        record_alert(now, "롱", state, plan)
        self.assertEqual(state["count"], 2)
        self.assertEqual(state["last_side"], "롱")
        self.assertEqual(alert_gate(now + 60, "롱", state, plan), "global_cooldown")

    def test_45m_global_cooldown_and_8_daily_limit_remain(self):
        now = 1800000000
        state = self.state(now, elapsed=COOLDOWN - 60)
        self.assertEqual(alert_gate(now, "롱", state, self.plan()),
                         "global_cooldown")
        state = self.state(now)
        state["count"] = MAX_DAILY_ALERTS
        self.assertEqual(alert_gate(now, "롱", state, self.plan()), "daily_cap")
        state = self.state(now, elapsed=REVERSAL_COOLDOWN + 60)
        self.assertEqual(alert_gate(now, "롱", state), "ok")

    def test_nonverified_early_rebound_and_bad_rsi_remain_blocked(self):
        now = 1800000000
        state = self.state(now)
        plan = self.plan()
        plan["sampled_context"]["reversal_confirmed"] = False
        self.assertEqual(alert_gate(now, "롱", state, plan), "reverse_cooldown")
        plan = self.plan()
        plan["rsi_direction_status"] = "5m_rsi_direction_mismatch_or_flat"
        self.assertEqual(alert_gate(now, "롱", state, plan), "reverse_cooldown")
        plan = self.plan()
        plan["strategy"] = "range_rebound"
        self.assertEqual(alert_gate(now, "롱", state, plan), "reverse_cooldown")

    def test_risk_or_cost_failure_never_bypasses_cooldown(self):
        now = 1800000000
        for key, value in (("rr1", 1.49), ("net_loss", 25.),
                           ("stop", 81100.), ("target1", 80400.)):
            with self.subTest(key=key):
                plan = self.plan()
                plan[key] = value
                self.assertEqual(alert_gate(now, "롱", self.state(now), plan),
                                 "reverse_cooldown")

    def test_preview_is_explicitly_not_actionable(self):
        msg = format_reversal_pre_watch(self.plan(), "2026-10-15 23:06")
        self.assertIn("롱 전환 사전포착", msg)
        self.assertIn("진입 미확정", msg)
        self.assertIn("2단계 재확인 전", msg)
        self.assertIn("자동주문 없음", msg)
        self.assertNotIn("매수 완료", msg)


class FiveMinuteRsiGateTests(unittest.TestCase):
    """Operational RSI checks direction only; legacy extreme research is separate."""

    @staticmethod
    def observations(closes):
        # One-minute perpetual ticker samples, NOT OHLCV candles.
        start = (1760000000 // 300) * 300
        points = []
        for idx, close in enumerate(closes):
            for minute in range(5):
                points.append((start + idx * 300 + minute * 60 + 55, float(close)))
        points.append((start + len(closes) * 300 + 30, float(closes[-1])))
        return points

    def test_5m_rsi_requires_complete_continuous_buckets(self):
        prices = [83000 - 30 * i for i in range(30)]
        data = self.observations(prices)
        state = sampled_five_minute_rsi(data)
        self.assertIsNotNone(state)
        self.assertEqual(len(state["closes"]), len(prices))
        data[-1] = (data[-1][0], 95000.)
        self.assertEqual(sampled_five_minute_rsi(data)["rsi"][-1], state["rsi"][-1])
        self.assertIsNone(sampled_five_minute_rsi(data[:35]))
        missing = [s for s in data if int(s[0] // 300) != int(data[40][0] // 300)]
        self.assertIsNone(sampled_five_minute_rsi(missing))

    def test_5m_rsi_only_current_direction_matters(self):
        from unittest.mock import patch
        for direction, previous, current in (
                (1, 22., 23.), (1, 72., 74.),
                (-1, 78., 77.), (-1, 29., 28.)):
            with self.subTest(direction=direction, previous=previous), \
                 patch("lbank_futures_monitor.sampled_five_minute_rsi",
                       return_value={"closes": [83000.] * 20,
                                     "rsi": [previous, current]}):
                ok, reason, rsi5 = five_minute_rsi_entry_gate([], direction)
                self.assertTrue(ok, reason)
                self.assertEqual(reason, "5m_rsi_direction_aligned")
                self.assertEqual(rsi5, current)

    def test_flat_or_opposing_direction_denied_even_in_neutral_zone(self):
        from unittest.mock import patch
        for direction, previous, current in (
                (1, 50., 50.), (1, 50., 49.), (-1, 50., 50.), (-1, 50., 51.)):
            with self.subTest(direction=direction, previous=previous), \
                 patch("lbank_futures_monitor.sampled_five_minute_rsi",
                       return_value={"closes": [83000.] * 20,
                                     "rsi": [previous, current]}):
                ok, reason, _ = five_minute_rsi_entry_gate([], direction)
                self.assertFalse(ok)
                self.assertEqual(reason, "5m_rsi_direction_mismatch_or_flat")

    def test_short_does_not_require_prior_overbought_or_absolute_zone(self):
        from unittest.mock import patch
        with patch("lbank_futures_monitor.sampled_five_minute_rsi",
                   return_value={"closes": [83000.] * 20, "rsi": [41., 40.]}):
            for strategy in ("range_rebound", "trend_retest", "breakout"):
                self.assertTrue(five_minute_rsi_entry_gate(
                    [], -1, setup={"strategy": strategy})[0])

    def test_long_does_not_require_prior_oversold_or_absolute_zone(self):
        from unittest.mock import patch
        with patch("lbank_futures_monitor.sampled_five_minute_rsi",
                   return_value={"closes": [83000.] * 20, "rsi": [59., 60.]}):
            for strategy in ("range_rebound", "trend_retest", "breakout"):
                self.assertTrue(five_minute_rsi_entry_gate(
                    [], 1, setup={"strategy": strategy})[0])

    def test_unfinished_5m_price_does_not_flip_direction(self):
        up = [80000. + 40 * i + (25 if i % 2 else 0) for i in range(26)]
        obs = self.observations(up)
        before = [five_minute_rsi_entry_gate(obs, d) for d in (-1, 1)]
        obs[-1] = (obs[-1][0], obs[-1][1] - 4000.)
        self.assertEqual(
            [five_minute_rsi_entry_gate(obs, d) for d in (-1, 1)], before)

    def test_missing_closed_bucket_fails_closed(self):
        obs = self.observations([80000. + 15 * i for i in range(26)])
        target = int(obs[37][0] // 300)
        obs = [p for p in obs if int(p[0] // 300) != target]
        allowed, reason, _ = five_minute_rsi_entry_gate(obs, -1)
        self.assertFalse(allowed)
        self.assertEqual(reason, "5m_rsi_direction_data_unavailable")

    def test_legacy_reversal_still_usable_for_shadow_research(self):
        down = [83000. - 90 * i for i in range(30)]
        up = [down[-1] + 140 * (i + 1) for i in range(6)]
        ready, reason, current = confirmed_sampled_5m_oversold_rebound(
            self.observations(down + up))
        self.assertTrue(ready, (reason, current))

    def test_fast_rsi_extreme_diagnostic_is_not_a_live_gate(self):
        down = [83000 - 50 * i for i in range(60)]
        permitted, reason, value = fast_sample_rsi_gate(down, -1)
        self.assertFalse(permitted)
        self.assertLessEqual(value, 35)
        up = [80000 + 50 * i for i in range(60)]
        permitted, reason, value = fast_sample_rsi_gate(up, 1)
        self.assertFalse(permitted)
        self.assertGreaterEqual(value, 70)

    def test_live_gate_fails_closed_without_real_sample_timestamps(self):
        up = [80000 + 25 * i + (45 if i % 2 else 0) for i in range(60)]
        self.assertIsNotNone(classify(up))
        self.assertIsNone(classify(up, observations=[(i * 60, p) for i, p in enumerate(up)]))


class SignalFollowupRiskTests(unittest.TestCase):
    def _plan(self, direction=-1):
        return {
            "direction": direction, "price": 81000.,
            "stop": 81250. if direction == -1 else 80750.,
            "target1": 80450. if direction == -1 else 81550.,
        }

    def test_no_warnings_without_a_sent_signal(self):
        tracker = SignalRiskTracker()
        self.assertEqual(tracker.check([81000, 81400], 300), [])

    def test_urgent_stop_line_breach_not_delayed(self):
        tracker = SignalRiskTracker()
        sig = tracker.add_sent_signal(self._plan(), 0)
        events = tracker.check([81000, 81255], 60)
        self.assertEqual(len(events), 1)
        self.assertIs(events[0][0], sig)
        self.assertEqual(events[0][1], "stop_breached")
        alert = format_risk_alert(sig, "stop_breached", 81255, "2026-10-13 10:00")
        self.assertIn("시장가 청산", alert)
        self.assertIn("미진입/기청산", alert)
        tracker.acknowledge(sig)
        self.assertEqual(tracker.check([81300], 120), [])

    def test_early_warning_requires_two_confirmations(self):
        tracker = SignalRiskTracker()
        sig = tracker.add_sent_signal(self._plan(), 0)
        sample = [80990., 81010., 81045., 81080., 81110., 81160.]
        self.assertEqual(tracker.check(sample, 60), [])
        events = tracker.check(sample + [81175.], 120)
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0][1], "early_invalidation")
        self.assertLess(events[0][2], sig.stop)

    def test_rebound_and_stale_signal_do_not_spam(self):
        tracker = SignalRiskTracker()
        tracker.add_sent_signal(self._plan(), 0)
        self.assertEqual(tracker.check([81000, 81005, 81010, 80995, 81003, 81000], 60), [])
        self.assertEqual(tracker.check([81180], 7300), [])
        self.assertEqual(len(tracker.signals), 0)

    def test_reference_target1_generates_one_notice_after_delivery(self):
        tracker = SignalRiskTracker()
        sig = tracker.add_sent_signal(self._plan(), 0)
        events = tracker.check([80400.], 60)
        self.assertEqual(len(events), 1)
        self.assertIs(events[0][0], sig)
        self.assertEqual(events[0][1], "target1_reached")
        notice = format_target_alert(sig, 80400., "2026-10-15 22:00")
        self.assertIn("현재 1차 목표 80,450.0 USDT", notice)
        self.assertIn("50% 익절", notice)
        self.assertIn("손절가는 진입가", notice)
        self.assertIn("미진입", notice)
        self.assertIn("수수료", notice)
        # A failed Telegram send must NOT mark the event as delivered.
        self.assertEqual(tracker.check([80390.], 75)[0][1], "target1_reached")
        tracker.acknowledge(sig)
        self.assertEqual(tracker.check([80390.], 90), [])
        self.assertEqual(len(tracker.signals), 0)

    def test_reference_long_tp_and_stop_messages_both_preserved(self):
        tracker = SignalRiskTracker()
        sig = tracker.add_sent_signal(self._plan(1), 0)
        result = tracker.check([81550.], 60)
        self.assertEqual(result[0][1], "target1_reached")
        self.assertIn("롱", format_target_alert(sig, 81550., "2026-10-15 22:00"))
        tracker.acknowledge(sig)
        short = tracker.add_sent_signal(self._plan(), 100)
        result = tracker.check([81251.], 130)
        self.assertEqual(result[0][1], "stop_breached")
        self.assertIn("긴급 위험 경고", format_risk_alert(short, "stop_breached",
                                                     81251., "2026-10-15 22:00"))

    def test_reference_target1_persists_restart_and_delivered_once(self):
        import os
        import tempfile
        with tempfile.TemporaryDirectory() as td:
            path = os.path.join(td, "followup.json")
            tracker = SignalRiskTracker(path=path)
            tracker.add_sent_signal(self._plan(), 0)
            resumed = SignalRiskTracker(path=path)
            self.assertEqual(len(resumed.signals), 1)
            reached = resumed.check([80420.], 60)
            self.assertEqual(reached[0][1], "target1_reached")
            resumed.acknowledge(reached[0][0])
            restarted_again = SignalRiskTracker(path=path)
            self.assertEqual(restarted_again.check([80390.], 90), [])



class SecondTargetFollowupTests(unittest.TestCase):
    @staticmethod
    def plan(direction):
        return {
            "direction": direction, "price": 81000.,
            "stop": 80750. if direction == 1 else 81250.,
            "target1": 81550. if direction == 1 else 80450.,
            "target2": 81850. if direction == 1 else 80150.,
        }

    def test_long_first_and_second_target_notified_separately_once(self):
        tracker = SignalRiskTracker()
        sig = tracker.add_sent_signal(self.plan(1), 0)
        self.assertEqual(tracker.check([81400.], 40), [])
        first = tracker.check([81560.], 60)
        self.assertEqual(first[0][1], "target1_reached")
        self.assertIn("1차 목표", format_target_alert(sig, first[0][2], "KST"))
        self.assertEqual(tracker.check([81570.], 70)[0][1], "target1_reached")
        tracker.acknowledge(sig, reason="target1_reached")
        self.assertEqual(len(tracker.signals), 1)
        self.assertEqual(tracker.check([81570.], 80), [])
        self.assertEqual(tracker.check([81620.], 100), [])
        second = tracker.check([81850.], 120)
        self.assertEqual(second[0][1], "target2_reached")
        text = format_target2_alert(sig, second[0][2], "2026-10-16 00:05")
        self.assertIn("2차 목표 81,850.0", text)
        self.assertIn("남은 50%", text)
        self.assertIn("미진입", text)
        self.assertEqual(tracker.check([81700.], 130)[0][1], "target2_reached")
        tracker.acknowledge(sig, reason="target2_reached")
        self.assertEqual(tracker.check([81900.], 140), [])
        self.assertEqual(len(tracker.signals), 0)

    def test_short_both_targets_and_completed_first_after_second_gap(self):
        tracker = SignalRiskTracker()
        sig = tracker.add_sent_signal(self.plan(-1), 0)
        # A one-minute snapshot jumps straight past TP2; do not lose TP2.
        event = tracker.check([80080.], 60)
        self.assertEqual(event[0][1], "target1_reached")
        tracker.acknowledge(sig, "target1_reached")
        # Price rebounds before next polling cycle; preserve hit state.
        event = tracker.check([80500.], 70)
        self.assertEqual(event[0][1], "target2_reached")
        self.assertIn("숏", format_target2_alert(sig, 80500., "KST"))
        tracker.acknowledge(sig, "target2_reached")
        self.assertEqual(tracker.check([79900.], 90), [])

    def test_reboot_preserves_second_target_and_delivery_acknowledgment(self):
        import tempfile
        import os
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "tracker.json")
            tracker = SignalRiskTracker(path=path)
            sig = tracker.add_sent_signal(self.plan(1), 0)
            self.assertEqual(tracker.check([81560.], 60)[0][1],
                             "target1_reached")
            tracker.acknowledge(sig, "target1_reached")
            resumed = SignalRiskTracker(path=path)
            self.assertEqual(len(resumed.signals), 1)
            self.assertTrue(resumed.signals[0].target1_notified)
            self.assertEqual(resumed.check([81860.], 80)[0][1],
                             "target2_reached")
            # Simulate failed Telegram transmission then restart and retried send
            after_failure = SignalRiskTracker(path=path)
            self.assertEqual(after_failure.check([81500.], 90)[0][1],
                             "target2_reached")
            after_failure.acknowledge(after_failure.signals[0], "target2_reached")
            final = SignalRiskTracker(path=path)
            self.assertEqual(final.check([81900.], 100), [])
            self.assertEqual(len(final.signals), 0)

    def test_urgent_stop_remains_priority_after_tp1(self):
        tracker = SignalRiskTracker()
        sig = tracker.add_sent_signal(self.plan(1), 0)
        tracker.check([81560.], 60)
        tracker.acknowledge(sig, "target1_reached")
        event = tracker.check([80740.], 70)
        self.assertEqual(event[0][1], "stop_breached")
        tracker.acknowledge(sig, "stop_breached")
        self.assertEqual(len(tracker.signals), 0)

    def test_legacy_first_target_without_second_remains_compatible(self):
        tracker = SignalRiskTracker()
        older = self.plan(1)
        del older["target2"]
        sig = tracker.add_sent_signal(older, 0)
        self.assertIsNone(sig.target2)
        self.assertEqual(tracker.check([81551.], 30)[0][1], "target1_reached")
        tracker.acknowledge(sig, "target1_reached")
        self.assertEqual(len(tracker.signals), 0)

    def test_invalid_or_unreasonable_second_target_is_rejected(self):
        for invalid in (81550., 81300., -1., float("inf")):
            with self.subTest(invalid=invalid):
                p = self.plan(1)
                p["target2"] = invalid
                with self.assertRaises(ValueError):
                    SignalRiskTracker().add_sent_signal(p, 0)

    def test_expired_signal_does_not_send_stale_tp2(self):
        tracker = SignalRiskTracker()
        sig = tracker.add_sent_signal(self.plan(1), 0)
        tracker.check([81560.], 60)
        tracker.acknowledge(sig, "target1_reached")
        self.assertEqual(tracker.check([81999.], 7201), [])
        self.assertEqual(len(tracker.signals), 0)


class StructureReactionTests(unittest.TestCase):
    @staticmethod
    def ranging_long():
        import math
        old = [83000. + 460 * math.sin(2 * math.pi * i / 16) for i in range(66)]
        return old + [82565., 82549., 82563., 82595., 82630., 82665.]

    def test_range_support_rebound_produces_net_positive_structured_watch(self):
        from lbank_structure_patterns import detect_structure
        from lbank_futures_monitor import ema, rsi
        prices = self.ranging_long()
        setup = detect_structure(prices, ema, rsi)
        self.assertIsNotNone(setup)
        self.assertEqual(setup["strategy"], "range_rebound")
        self.assertEqual(setup["direction"], 1)
        watch = classify(prices)
        self.assertIsNotNone(watch)
        self.assertEqual(watch["strategy"], "range_rebound")
        self.assertLess(watch["stop"], setup["structure_anchor"])
        self.assertLess(watch["target1"], setup["barrier"])
        self.assertLessEqual(watch["net_loss"], REF_BALANCE * RISK_FRACTION + 1e-8)
        self.assertLessEqual(watch["margin"], REF_BALANCE * MAX_MARGIN_FRACTION + 1e-8)
        self.assertGreaterEqual(watch["rr1"], 1.5 - 1e-8)

    def test_range_resistance_short_and_second_stage(self):
        prices = [166000. - p for p in self.ranging_long()]
        watch = classify(prices)
        self.assertIsNotNone(watch)
        self.assertEqual(watch["strategy"], "range_rebound")
        self.assertEqual(watch["direction"], -1)
        confirmed = confirm_watch(prices + [prices[-1] - 20], watch, 60)
        self.assertIsNotNone(confirmed)
        self.assertEqual(confirmed["strategy"], "range_rebound")
        self.assertGreater(confirmed["stop"], watch["structure_anchor"])
        self.assertGreater(confirmed["target1"], watch["barrier"])

    def test_range_two_stage_rejects_chase_and_failed_support(self):
        prices = self.ranging_long()
        watch = classify(prices)
        self.assertIsNotNone(watch)
        self.assertIsNone(confirm_watch(prices + [prices[-1] + 300], watch, 60))
        self.assertIsNone(confirm_watch(prices + [watch["structure_anchor"] - 20], watch, 60))
        self.assertIsNone(confirm_watch(prices + [prices[-1] + 10], watch, 300))

    def test_tight_channel_is_not_a_fee_feasible_range(self):
        import math
        prices = [83000. + 90 * math.sin(2 * math.pi * i / 16) for i in range(66)]
        prices += [82910., 82904., 82915., 82925., 82935., 82945.]
        self.assertIsNone(classify(prices))

    @staticmethod
    def trending_long():
        old = [82000. + 24 * i + (55 if i % 2 else 0) for i in range(66)]
        peak = old[-1]
        return old + [peak - 140, peak - 195, peak - 220,
                      peak - 210, peak - 170, peak - 130]

    def test_trend_pullback_retest_has_confirmed_structure_stop(self):
        prices = self.trending_long()
        watch = classify(prices)
        self.assertIsNotNone(watch)
        self.assertEqual(watch["strategy"], "trend_retest")
        self.assertEqual(watch["direction"], 1)
        self.assertLess(watch["stop"], watch["structure_anchor"])
        self.assertLessEqual(watch["net_loss"], REF_BALANCE * RISK_FRACTION + 1e-8)
        confirmed = confirm_watch(prices + [prices[-1] + 8], watch, 60)
        self.assertIsNotNone(confirmed)
        self.assertEqual(confirmed["strategy"], "trend_retest")

    def test_downtrend_pullback_rejection_symmetric(self):
        prices = [166000. - p for p in self.trending_long()]
        watch = classify(prices)
        self.assertIsNotNone(watch)
        self.assertEqual(watch["strategy"], "trend_retest")
        self.assertEqual(watch["direction"], -1)
        self.assertGreater(watch["stop"], watch["structure_anchor"])
        self.assertLessEqual(watch["margin"], REF_BALANCE * MAX_MARGIN_FRACTION + 1e-8)

    def test_5m_gate_fails_closed_for_structure_without_contiguous_samples(self):
        prices = self.ranging_long()
        self.assertIsNotNone(classify(prices))
        self.assertIsNone(classify(prices, observations=[(i * 60, p) for i, p in enumerate(prices)]))


class SignalReferenceMessagingTests(unittest.TestCase):
    """07:02 entry / 07:10 invalidation must name the SAME reference."""

    @staticmethod
    def make_plan():
        base = [80000. + 25 * i + (45 if i % 2 else 0) for i in range(60)]
        watch = classify(base)
        return confirm_watch(base + [base[-1] + 5], watch, 60)

    def test_numbered_entry_alert_and_freshness(self):
        plan = self.make_plan()
        self.assertIsNotNone(plan)
        tr = SignalRiskTracker()
        ref_number = tr.next_number
        alert = format_entry_alert(plan, "2026-10-16T00:00:00Z",
                                   reference_number=ref_number)
        self.assertIn(f"신호번호: #{ref_number}", alert)
        self.assertIn("유효 5분", alert)
        sent = tr.add_sent_signal(plan, 0)
        self.assertEqual(sent.number, ref_number)
        invalid = format_risk_alert(sent, "early_invalidation", plan["stop"] + 5,
                                    "2026-10-16 09:10")
        self.assertIn(f"기존 진입 알람: #{ref_number}", invalid)
        self.assertIn("이전 조건부 진입 신호 무효화", invalid)
        self.assertIn("신규 진입 금지", invalid)
        self.assertIn("별도 신규 2단계 확인", invalid)

    def test_stop_invalidates_and_targets_link_reference(self):
        plan = self.make_plan()
        tr = SignalRiskTracker()
        s = tr.add_sent_signal(plan, 0)
        stop = format_risk_alert(s, "stop_breached", s.stop - 5, "2026-10-16 09:10")
        self.assertIn("신규 진입 금지", stop)
        self.assertIn(f"기존 진입 알람: #{s.number}", stop)
        tp1 = format_target_alert(s, s.target1 + 5, "2026-10-16 09:20")
        tp2 = format_target2_alert(s, s.target2 + 5, "2026-10-16 09:30")
        self.assertIn(f"기존 진입 알람: #{s.number}", tp1)
        self.assertIn(f"기존 진입 알람: #{s.number}", tp2)

    def test_legacy_alert_without_reference_remains_supported(self):
        self.assertIn("조건부 진입 검토",
                      format_entry_alert(self.make_plan(), "2026-10-16T00:00:00Z"))

    def test_reject_unrecognized_risk_notice_reason(self):
        s = SignalRiskTracker().add_sent_signal(self.make_plan(), 0)
        with self.assertRaises(ValueError):
            format_risk_alert(s, "unknown", s.entry, "2026-10-16 09:20")



class SignalDiagnosticsRegressionTests(unittest.TestCase):
    def test_absent_market_setup_is_not_labeled_filter_failure(self):
        diag = {}
        self.assertIsNone(classify([80000.] * 60, diagnostics=diag))
        self.assertEqual(diag["reason"], "market_no_setup")

    def test_insufficient_market_samples_labelled_as_data_gap(self):
        diag = {}
        self.assertIsNone(classify([80000.] * 59, diagnostics=diag))
        self.assertEqual(diag["reason"], "insufficient_samples")

    def test_fee_or_target_rejection_distinguished_from_market_no_setup(self):
        base = [80000. + 25 * i + (45 if i % 2 else 0) for i in range(60)]
        diag = {}
        with patch("lbank_futures_monitor.calculate_plan", return_value=None):
            self.assertIsNone(classify(base, diagnostics=diag))
        self.assertEqual(diag["reason"], "risk_or_target")

    def test_second_stage_expiry_reports_real_reason(self):
        base = [80000. + 25 * i + (45 if i % 2 else 0) for i in range(60)]
        watch = classify(base)
        self.assertIsNotNone(watch)
        diag = {}
        self.assertIsNone(confirm_watch(base, watch, 9999, diagnostics=diag))
        self.assertEqual(diag["reason"], "invalid_watch_age")

    def test_normal_qualified_watch_unaffected_by_optional_diagnostics(self):
        base = [80000. + 25 * i + (45 if i % 2 else 0) for i in range(60)]
        diag = {}
        watch = classify(base, diagnostics=diag)
        self.assertIsNotNone(watch)
        self.assertEqual(diag["reason"], "qualified")



class RetestEvidenceDisclosureTests(unittest.TestCase):
    """Show weak evidence clearly but do not fit a hard veto to one losing trade."""

    @staticmethod
    def example_plan():
        base = [80000. + 25 * i + (45 if i % 2 else 0) for i in range(60)]
        plan = classify(base)
        assert plan is not None
        plan["strategy"] = "trend_retest"
        plan["confirmation_progress_pct"] = .0045
        plan["sampled_context"] = {
            "trend5_pct": .0505, "trend15_pct": -.0329,
            "range5_pct": .2191, "three_step_recovery": False,
            "reversal_confirmed": False,
        }
        return plan

    def test_weak_retest_signal_discloses_price_progress_and_45m_conflict(self):
        plan = self.example_plan()
        note = format_entry_alert(plan, "2026-10-16T04:25:02+00:00",
                                  reference_number=5)
        self.assertIn("신호번호: #5", note)
        self.assertIn("실전 진입 추천 아님", note)
        self.assertIn("진입 증거금", note)
        self.assertIn("45분 표본 역추세", note)
        self.assertIn("반전 미확인", note)
        self.assertLessEqual(len(note.splitlines()), 11)

    def test_aligned_or_verified_retest_does_not_show_countertrend_warning(self):
        for context_change, verified in ((.15, False), (-.03, True)):
            plan = self.example_plan()
            plan["sampled_context"]["trend15_pct"] = context_change
            plan["sampled_context"]["reversal_confirmed"] = verified
            note = format_entry_alert(plan, "2026-10-16T04:25:02+00:00")
            self.assertNotIn("45분 표본 역추세", note)
            self.assertIn("실전 진입 추천 아님", note)

    def test_legacy_plan_without_stage_progress_is_compatible(self):
        plan = self.example_plan()
        plan.pop("confirmation_progress_pct")
        note = format_entry_alert(plan, "2026-10-16T04:25:02+00:00")
        self.assertNotIn("2단계 확인 순방향 진행폭", note)

if __name__ == "__main__":
    unittest.main()
