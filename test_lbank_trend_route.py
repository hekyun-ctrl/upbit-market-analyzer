"""Regression tests: shadow routing cannot approve live entries."""
import unittest
from lbank_trend_route import rank_entry_routes


# LBank BTCUSDT perpetual, 2026-11-21 11:20–13:40 UTC candle starts.
# NATIVE_CANDLE_CLOSED records from the production monitor's 1m-to-5m aggregation.
NOV21_FIVE_MINUTE_OHLC = (
    (57866.3, 57901.2, 57851.2, 57884.3),
    (57884.3, 57926.5, 57873.8, 57926.4),
    (57926.4, 58610.9, 57926.4, 58291.5),
    (58291.5, 58323.9, 58122.2, 58174.6),
    (58174.6, 58250.0, 58113.6, 58132.0),
    (58132.0, 58258.3, 58091.6, 58120.8),
    (58120.8, 58191.7, 58077.0, 58132.1),
    (58132.1, 58205.5, 58098.1, 58178.1),
    (58178.1, 58250.9, 58106.5, 58138.5),
    (58138.5, 58185.8, 58110.0, 58156.5),
    (58156.5, 58268.0, 58139.0, 58262.5),
    (58262.5, 58507.7, 58248.6, 58470.1),
    (58470.1, 58498.6, 58338.0, 58483.3),
    (58483.3, 58570.0, 58438.9, 58442.3),
    (58442.3, 58528.3, 58420.6, 58528.2),
    (58528.2, 59140.0, 58521.1, 58808.5),
    (58808.5, 59013.8, 58714.6, 58942.1),
    (58942.1, 58945.0, 58789.0, 58804.0),
    (58804.0, 58857.8, 58750.0, 58798.2),
    (58798.2, 58865.4, 58789.7, 58832.4),
    (58832.4, 58979.5, 58770.0, 58895.2),
    (58895.2, 59494.0, 58880.4, 59247.7),
    (59247.7, 59336.5, 59203.1, 59259.8),
    (59259.8, 59300.0, 59044.9, 59184.7),
    (59184.7, 59636.7, 59141.6, 59411.4),
    (59411.4, 59776.0, 59402.0, 59585.0),
    (59585.0, 59949.9, 59505.0, 59774.9),
    (59774.9, 60880.9, 59770.0, 60634.9),
    (60634.9, 60635.0, 60266.4, 60385.6),
)


def candles(period, count, start_price, step):
    result = []
    for i in range(count):
        o = start_price + i * step
        c = o + step * .8
        result.append({"start": i * period, "open": o,
                       "high": max(o, c) + 3, "low": min(o, c) - 3,
                       "close": c, "quality": "stream_closed"})
    return result


def state(step5=70, step1=10, bars5=60):
    return {"five_bars": candles(300, bars5, 57000, step5),
            "minute_bars": candles(60, 80, 57000, step1),
            "quote": 59000.0}


class TrendRouteShadowTests(unittest.TestCase):
    def test_bull_impulse_prioritizes_breakout(self):
        x = rank_entry_routes(state())
        self.assertEqual(x["regime"], "impulse")
        self.assertEqual(x["direction"], 1)
        self.assertEqual(x["routes"], ["breakout", "continuation", "pullback"])
        self.assertFalse(x["approval"])
        self.assertIsNone(x["entry_prices"])

    def test_bear_impulse_prioritizes_breakout(self):
        x = rank_entry_routes(state(step5=-70, step1=-10))
        self.assertEqual(x["regime"], "impulse")
        self.assertEqual(x["direction"], -1)
        self.assertEqual(x["routes"][0], "breakout")

    def test_flat_does_not_claim_trend(self):
        x = rank_entry_routes(state(step5=0, step1=0))
        self.assertEqual(x["direction"], 0)
        self.assertEqual(x["regime"], "range_or_transition")

    def test_incomplete_and_missing_fail_closed(self):
        for x in (None, {}, state(bars5=10)):
            self.assertFalse(rank_entry_routes(x)["ready"])

    def test_gap_does_not_synthesize_bars(self):
        x = state()
        x["five_bars"][-2]["start"] += 300
        self.assertEqual(rank_entry_routes(x)["reason"], "gap_in_completed_bars")

    def test_forming_bar_rejected(self):
        x = state()
        x["minute_bars"][-1]["quality"] = "forming"
        self.assertEqual(rank_entry_routes(x)["reason"], "unverified_candle")

    def test_no_mutation(self):
        x = state()
        original = [dict(b) for b in x["five_bars"]]
        rank_entry_routes(x)
        self.assertEqual(x["five_bars"], original)

    def _staircase_state(self, prices):
        # Synthetic regression scenario modeled on the 2026-11-21 screenshot,
        # NOT a replay of unprovided historical exchange candle data.
        x = state(step5=0)
        for i, close in enumerate(prices):
            bar = x["five_bars"][-len(prices) + i]
            bar["open"] = close - 20
            bar["close"] = close
            bar["high"] = close + 30
            bar["low"] = close - 50
        return x

    def test_shallow_pullback_staircase_like_nov21_chart(self):
        x = self._staircase_state(
            [59200, 59310, 59450, 59530, 59650, 59790, 59900, 60100, 60374])
        result = rank_entry_routes(x)
        pattern = result["shallow_pullback_continuation"]
        self.assertTrue(pattern["detected"])
        self.assertEqual(pattern["reason"], "shallow_pullback_staircase")
        self.assertEqual(result["routes"][0], "breakout")
        self.assertTrue(pattern["completed_bar_breakout"])
        self.assertFalse(result["approval"])
        self.assertIsNone(result["entry_prices"])

    def test_nov21_completed_bars_switch_from_continuation_to_breakout(self):
        replay = [
            {"start": 1795260000 + i * 300, "open": o, "high": h, "low": l,
             "close": c, "quality": "stream_closed"}
            for i, (o, h, l, c) in enumerate(NOV21_FIVE_MINUTE_OHLC)
        ]
        # One-minute bars provide readiness and diagnostic momentum. Only the
        # recorded five-minute bars determine the route in this replay.
        x = state()
        for count, route, basis, broke_level in (
            (25, "continuation", "shallow_pullback_staircase", False),  # 22:25 KST
            (26, "continuation", "shallow_pullback_staircase", False),
            (27, "continuation", "shallow_pullback_staircase", False),
            (28, "breakout", "impulse", True),  # 22:40 KST
        ):
            x["five_bars"] = replay[:count]
            with self.subTest(count=count):
                result = rank_entry_routes(x)
                self.assertEqual(result["direction"], 1)
                self.assertEqual(result["routes"][0], route)
                self.assertEqual(result["priority_basis"], basis)
                pattern = result["shallow_pullback_continuation"]
                self.assertTrue(pattern["detected"])
                self.assertEqual(pattern["completed_bar_breakout"], broke_level)
                self.assertFalse(result["approval"])
                self.assertIsNone(result["entry_prices"])
        x["five_bars"] = replay
        result = rank_entry_routes(x)  # 22:45 KST, after the first sharp setback.
        self.assertFalse(result["shallow_pullback_continuation"]["detected"])
        self.assertEqual(result["priority_basis"], "trend_pause")

    def test_staircase_cannot_override_conflicting_hour(self):
        x = self._staircase_state(
            [59200, 59310, 59450, 59530, 59650, 59790, 59900, 60100, 60374])
        x["five_bars"][-13].update(open=61000, high=61020, low=60980, close=61000)
        result = rank_entry_routes(x)
        self.assertTrue(result["shallow_pullback_continuation"]["detected"])
        self.assertEqual(result["regime"], "conflicted")
        self.assertEqual(result["routes"][0], "pullback")

    def test_large_countertrend_candle_disqualifies_staircase(self):
        x = self._staircase_state(
            [59200, 59500, 59700, 59200, 59650, 59790, 59900, 60100, 60374])
        self.assertFalse(rank_entry_routes(x)["shallow_pullback_continuation"]["detected"])

    def test_no_auto_confirmation_from_unfinished_high_wick(self):
        x = self._staircase_state(
            [59200, 59310, 59450, 59530, 59650, 59790, 59900, 60100, 60374])
        x["five_bars"][-1]["quality"] = "forming"
        result = rank_entry_routes(x)
        self.assertFalse(result["ready"])
        self.assertEqual(result["reason"], "unverified_candle")

    def test_shadow_never_approves_if_price_far_from_close(self):
        x = self._staircase_state(
            [59200, 59310, 59450, 59530, 59650, 59790, 59900, 60100, 60374])
        x["quote"] = 65000.
        result = rank_entry_routes(x)
        self.assertFalse(result["approval"])
        self.assertIsNone(result["entry_prices"])
        self.assertFalse(result["shallow_pullback_continuation"]["uses_quote_for_trigger"])

    def test_four_hour_not_fabricated(self):
        x = rank_entry_routes(state(bars5=25))
        self.assertIsNone(x["momentum_pct"]["4h"])
        self.assertEqual(x["higher_timeframes"], "derived_5m_not_exchange_verified")


if __name__ == "__main__":
    unittest.main()
