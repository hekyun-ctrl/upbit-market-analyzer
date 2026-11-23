"""Stop-noise regression, including recorded reference-76 volatility inputs."""
import copy
import unittest
from unittest.mock import patch

import lbank_futures_monitor as m
from lbank_entry_refinement import local_stop_floor
from test_lbank_entry_geometry import state
from test_lbank_entry_refinement import complete_minutes


class StopNoiseTests(unittest.TestCase):
    def test_reference76_uses_completed_5m_noise_not_future_rebound(self):
        s = {"quote": 61406.3, "five_bars": []}
        atr5, atr1 = 184.170417042583, (61487.074447733845-61406.3)/1.5
        with patch("lbank_entry_refinement.minute_atr", return_value=atr1), \
             patch("lbank_entry_refinement.atr_wilder", return_value=atr5):
            old = local_stop_floor(s, 1.5*atr5/s["quote"])
            new = local_stop_floor(s, 1.5*atr5/s["quote"], timeframe_noise=True)
        self.assertAlmostEqual(old["floor"]*s["quote"], 80.774447733845, places=6)
        self.assertAlmostEqual(new["floor"]*s["quote"], atr5)
        self.assertEqual(new["basis"], "max_2atr1_1atr5_closed_native_v1")
        self.assertGreater(new["floor"], old["floor"])

    def test_minute_volatility_surge_cannot_be_capped_by_stale_5m_floor(self):
        with patch("lbank_entry_refinement.minute_atr", return_value=150.), \
             patch("lbank_entry_refinement.atr_wilder", return_value=100.):
            result = local_stop_floor({"quote":60000., "five_bars":[]}, .0025,
                                      timeframe_noise=True)
        self.assertAlmostEqual(result["floor"], .005)

    def test_missing_5m_volatility_fails_instead_of_squeezing_stop(self):
        with patch("lbank_entry_refinement.minute_atr", return_value=40.), \
             patch("lbank_entry_refinement.atr_wilder", return_value=None):
            self.assertIsNone(local_stop_floor({"quote":60000., "five_bars":[]}, .003,
                                               timeframe_noise=True))

    def test_both_sides_widen_stop_and_reduce_size_with_identical_loss_budget(self):
        s = complete_minutes(state())
        for b in s["five_bars"]:
            b.update(high=80070., low=79930.)
        for d in (-1, 1):
            for strategy in ("trend_retest", "breakout"):
                setup = dict(strategy=strategy, structure_anchor=80000.-d*90)
                with patch.object(m, "ENTRY_REFINEMENT_MODE", "structure_cost"):
                    old = m.calculate_plan(s["prices"], d, setup, native_state=s)
                with patch.object(m, "ENTRY_REFINEMENT_MODE", "capture"):
                    new = m.calculate_plan(s["prices"], d, setup, native_state=s)
                with self.subTest(direction=d, strategy=strategy):
                    self.assertIsNotNone(old)
                    self.assertIsNotNone(new)
                    self.assertGreater(abs(new["stop"]-new["price"]), abs(old["stop"]-old["price"]))
                    self.assertLess(new["quantity_btc"], old["quantity_btc"])
                    self.assertLessEqual(new["net_loss"], m.REF_BALANCE*m.RISK_FRACTION)
                    self.assertLessEqual(new["margin"], m.REF_BALANCE*m.MAX_MARGIN_FRACTION)
                    self.assertGreaterEqual(new["rr1"], m.MIN_NET_RR-1e-7)
                    self.assertLessEqual(new["stop_distance_pct"], .6)

    def test_large_volatility_rejects_plan_instead_of_truncating_required_stop(self):
        s = complete_minutes(state())
        for b in s["five_bars"]:
            b.update(high=80300., low=79700.)
        with patch.object(m, "ENTRY_REFINEMENT_MODE", "capture"):
            self.assertIsNone(m.calculate_plan(s["prices"], -1,
                dict(strategy="trend_retest", structure_anchor=80100.), native_state=s))

    def test_new_plan_does_not_mutate_existing_reference(self):
        sent = dict(direction=-1, price=61406.3, stop=61487.074447733845)
        original = copy.deepcopy(sent)
        s = complete_minutes(state())
        with patch.object(m, "ENTRY_REFINEMENT_MODE", "capture"):
            m.calculate_plan(s["prices"], -1,
                dict(strategy="trend_retest", structure_anchor=80100.), native_state=s)
        self.assertEqual(sent, original)


if __name__ == "__main__":
    unittest.main()
