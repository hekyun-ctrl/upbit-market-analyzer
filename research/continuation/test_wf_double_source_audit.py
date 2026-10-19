import unittest
import numpy as np
import pandas as pd
from audit_wf_double_source import actual_turnover, audit_frame, closed_segments, drawdown


def fixture():
    prices = np.array([100, 101, 102, 101, 99, 98, 97], dtype=float)
    positions = np.array([1, 1, -1, -1, 1, 1, -1], dtype=float)
    ret = np.r_[0, np.diff(np.log(prices))]
    sides = np.nan_to_num(actual_turnover(positions))
    return pd.DataFrame({"Asset": prices, "positions": positions,
        "Asset_returns": ret, "return": positions*ret-.001*sides,
        "test_start": [1, 1, 1, 1, 5, 5, 5]},
        index=pd.date_range("2020-01-01", periods=len(prices), freq="h", tz="UTC"))


class ReconciliationTests(unittest.TestCase):
    def test_inherited_start_unknown(self):
        a = actual_turnover([1, 1, -1])
        self.assertTrue(np.isnan(a[0]))
        self.assertEqual(list(a[1:]), [0, 2])

    def test_missing_boundary_fee_detected(self):
        d = fixture()
        d.loc[d.index[4], "return"] += .002
        r, _ = audit_frame(d)
        self.assertEqual(r["selected_path_fee_disagreements"], 1)
        self.assertEqual(r["disagreements_on_WF_boundaries"], 1)
        self.assertEqual(r["missing_charged_sides"], 2)

    def test_extra_fee_detected(self):
        d = fixture()
        d.loc[d.index[3], "return"] -= .002
        r, _ = audit_frame(d)
        self.assertEqual(r["extra_charged_sides"], 2)

    def test_no_gap_liquidation(self):
        d = fixture().drop(fixture().index[1])
        self.assertEqual(len(closed_segments(d)), 1)

    def test_closed_segment_price_and_cost(self):
        ts = closed_segments(fixture())
        self.assertEqual(len(ts), 2)
        self.assertEqual(ts[0]["entry_price"], 101)
        self.assertEqual(ts[0]["exit_price"], 101)
        self.assertAlmostEqual(ts[0]["net_pct"], -.002)
        self.assertAlmostEqual(ts[0]["double_fee_net_pct"], -.004)

    def test_same_positions_stress_cost(self):
        r, _ = audit_frame(fixture())
        base = r["return_models"]["fee_reconciled"]["sum_log_return"]
        stress = r["return_models"]["double_fee"]["sum_log_return"]
        self.assertAlmostEqual(base-stress, .006)

    def test_out_of_range_implied_cost_rejected(self):
        d = fixture()
        d.loc[d.index[1], "return"] -= .001
        with self.assertRaises(AssertionError): audit_frame(d)

    def test_drawdown_includes_initial_equity(self):
        self.assertAlmostEqual(drawdown(np.log([.9, 1.2])), .1)


if __name__ == "__main__": unittest.main()
