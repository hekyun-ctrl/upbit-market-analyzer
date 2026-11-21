"""Regression tests: shadow routing cannot approve live entries."""
import unittest
from lbank_trend_route import rank_entry_routes


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
