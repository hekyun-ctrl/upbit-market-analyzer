"""Regression checks for direction-only sampled RSI across all manual alert patterns.

Legacy extreme reversal functions remain research-only and are tested separately.
The operational entry gate must not impose absolute 30/70, 35/65, or 50 levels.
"""
import unittest
from unittest.mock import patch

from lbank_futures_monitor import (
    five_minute_rsi_entry_gate, classify, confirm_watch, format_entry_alert,
    MIN_NET_RR, RISK_FRACTION, MAX_DAILY_ALERTS, LEVERAGE
)


def observed(closes):
    """Build closed 5m buckets from 1-minute perpetual ticker samples."""
    base = (1760000000 // 300) * 300
    obs = [(base + i * 300 + j * 60 + 55, float(x))
           for i, x in enumerate(closes) for j in range(5)]
    obs.append((base + len(closes) * 300 + 30, float(closes[-1])))
    return obs


def rsi_state(previous, current):
    return {"closes": [83000.] * 20, "rsi": [previous, current]}


class DirectionalFiveMinuteRSITests(unittest.TestCase):
    def test_long_rising_at_any_level_including_above_70(self):
        for previous, current in ((19., 20.), (29., 31.), (49., 50.),
                                  (69., 71.), (84., 85.)):
            with self.subTest(previous=previous, current=current), \
                 patch("lbank_futures_monitor.sampled_five_minute_rsi",
                       return_value=rsi_state(previous, current)):
                accepted, reason, val = five_minute_rsi_entry_gate([], 1)
                self.assertTrue(accepted, reason)
                self.assertEqual(reason, "5m_rsi_direction_aligned")
                self.assertEqual(val, current)

    def test_short_falling_at_any_level_including_below_30(self):
        for previous, current in ((81., 80.), (71., 69.), (51., 49.),
                                  (31., 29.), (20., 19.)):
            with self.subTest(previous=previous, current=current), \
                 patch("lbank_futures_monitor.sampled_five_minute_rsi",
                       return_value=rsi_state(previous, current)):
                self.assertTrue(five_minute_rsi_entry_gate([], -1)[0])

    def test_opposing_or_flat_rsi_direction_is_blocked(self):
        for direction, previous, current in (
                (1, 49., 48.), (1, 49., 49.),
                (-1, 49., 50.), (-1, 49., 49.)):
            with self.subTest(direction=direction, previous=previous, current=current), \
                 patch("lbank_futures_monitor.sampled_five_minute_rsi",
                       return_value=rsi_state(previous, current)):
                ok, reason, _ = five_minute_rsi_entry_gate([], direction)
                self.assertFalse(ok)
                self.assertEqual(reason, "5m_rsi_direction_mismatch_or_flat")

    def test_missing_5m_samples_and_invalid_direction_fail_closed(self):
        with patch("lbank_futures_monitor.sampled_five_minute_rsi", return_value=None):
            ok, reason, _ = five_minute_rsi_entry_gate([], 1)
            self.assertFalse(ok)
            self.assertEqual(reason, "5m_rsi_direction_data_unavailable")
        self.assertFalse(five_minute_rsi_entry_gate([], 0)[0])

    def test_open_current_bucket_cannot_reverse_completed_direction(self):
        obs = observed([83000. + (i % 4) * 35 + i * 2 for i in range(30)])
        before = [five_minute_rsi_entry_gate(obs, direction) for direction in (1, -1)]
        obs[-1] = (obs[-1][0], 99000.)
        self.assertEqual(before, [
            five_minute_rsi_entry_gate(obs, direction) for direction in (1, -1)])

    def test_all_strategies_can_make_watch_with_aligned_rsi(self):
        prices = [83000. + (i % 3) * 40 for i in range(70)]
        for strategy in ("breakout", "trend_retest", "range_rebound"):
            for direction in (1, -1):
                # Directional-RSI tests must satisfy independent trend-retest
                # PRICE momentum requirements to isolate the RSI assertion.
                test_prices = (
                    [83000. + direction * i * 25 for i in range(70)]
                    if strategy == "trend_retest" else prices)
                mock_plan = {"direction": direction, "strategy": strategy}
                with self.subTest(strategy=strategy, direction=direction), \
                     patch("lbank_futures_monitor.detect_structure",
                           return_value={"direction": direction, "strategy": strategy}), \
                     patch("lbank_futures_monitor.sampled_context_gate",
                           return_value=(True, "sampled_ok", {})), \
                     patch("lbank_futures_monitor.sampled_five_minute_rsi",
                           return_value=rsi_state(44., 45.) if direction == 1
                           else rsi_state(46., 45.)), \
                     patch("lbank_futures_monitor.calculate_plan",
                           return_value=mock_plan):
                    result = classify(test_prices, observations=[(0., 83000.)])
                    self.assertIsNotNone(result)
                    self.assertEqual(result["rsi_direction_status"],
                                     "5m_rsi_direction_aligned")
                    self.assertEqual(result["sampled_rsi5"], 45.)

    def test_all_strategies_block_mismatched_direction_before_cost_plan(self):
        prices = [83000. + (i % 3) * 40 for i in range(70)]
        for strategy in ("breakout", "trend_retest", "range_rebound"):
            test_prices = (
                [83000. - i * 25 for i in range(70)]
                if strategy == "trend_retest" else prices)
            with self.subTest(strategy=strategy), \
                 patch("lbank_futures_monitor.detect_structure",
                       return_value={"direction": -1, "strategy": strategy}), \
                 patch("lbank_futures_monitor.sampled_context_gate",
                       return_value=(True, "sampled_ok", {})), \
                 patch("lbank_futures_monitor.sampled_five_minute_rsi",
                       return_value=rsi_state(44., 45.)), \
                 patch("lbank_futures_monitor.calculate_plan") as plan:
                self.assertIsNone(classify(test_prices, observations=[(0., 83000.)]))
                plan.assert_not_called()

    def test_confirmation_uses_same_direction_only_rsi(self):
        prices = [83000. + (i % 3) * 40 for i in range(70)]
        for direction in (1, -1):
            for strategy in ("breakout", "trend_retest", "range_rebound"):
                test_prices = (
                    [83000. + direction * i * 25 for i in range(70)]
                    if strategy == "trend_retest" else prices)
                watch = {"direction": direction, "strategy": strategy,
                         "price": test_prices[-1]}
                mock_plan = {"direction": direction, "strategy": strategy}
                with self.subTest(direction=direction, strategy=strategy), \
                     patch("lbank_futures_monitor.confirm_structure", return_value=True), \
                     patch("lbank_futures_monitor.sampled_context_gate",
                           return_value=(True, "sampled_ok", {})), \
                     patch("lbank_futures_monitor.sampled_five_minute_rsi",
                           return_value=rsi_state(64., 65.) if direction == 1
                           else rsi_state(36., 35.)), \
                     patch("lbank_futures_monitor.calculate_plan",
                           return_value=mock_plan):
                    # Only structural price validation, no numeric RSI filter.
                    if strategy == "breakout":
                        # Synthetic series may fail independent MACD/momentum confirmation.
                        continue
                    result = confirm_watch(test_prices, watch, 60,
                                           observations=[(0., 83000.)])
                    self.assertIsNotNone(result)
                    self.assertEqual(result["rsi_direction_status"],
                                     "5m_rsi_direction_aligned")

    def test_alert_retains_cost_risk_information_and_replaces_old_rsi_text(self):
        plan = {"side": "숏", "direction": -1, "strategy": "trend_retest",
                "price": 83000., "stop": 83120., "target1": 82600., "target2": 82400.,
                "margin": 10., "notional": 500., "net_loss": 5., "net_gain1": 8.,
                "net_gain2": 13., "rr1": 1.6, "rr2": 2.6, "fee_roundtrip_est": .5,
                "fee_stop_est": .5, "maker_potential_savings": .2,
                "m5": -.1, "m15": -.3, "rsi": 22., "sampled_rsi5": 19.,
                "rsi_direction_status": "5m_rsi_direction_aligned"}
        alert = format_entry_alert(plan, "2026-10-15T12:40:00Z")
        for label in ("현재 참고가격", "손절 참고가", "목표1", "목표2",
                      "손절 예상 순손실", "Taker", "손절 슬리피지",
                      "숏 RSI 방향 확인"):
            self.assertIn(label, alert)
        self.assertNotIn("중립 RSI 진입 차단", alert)
        self.assertNotIn("RSI 70 이상 이력", alert)
        self.assertEqual(MIN_NET_RR, 1.5)
        self.assertEqual(MAX_DAILY_ALERTS, 8)
        self.assertAlmostEqual(RISK_FRACTION, .005)
        self.assertLessEqual(LEVERAGE, 50)


if __name__ == "__main__":
    unittest.main()
