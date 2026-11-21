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

    def test_four_hour_not_fabricated(self):
        x = rank_entry_routes(state(bars5=25))
        self.assertIsNone(x["momentum_pct"]["4h"])
        self.assertEqual(x["higher_timeframes"], "derived_5m_not_exchange_verified")


if __name__ == "__main__":
    unittest.main()
