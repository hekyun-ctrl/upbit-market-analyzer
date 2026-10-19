"""Causality, source gaps, timing and cash-budget tests for published components."""
import unittest
import numpy as np
import pandas as pd
from research.continuation.functional_curve import (
    DAY, forecast_components, forecast_day, trade_times, day_curves,
    evaluate_forecast, model_return, return_statistics, block_indices,
)


class FunctionalCurveTests(unittest.TestCase):
    def minutes(self, count=60):
        start = 1640995200 + np.arange(count) * 60
        price = 100 + np.arange(count) / 10
        return pd.DataFrame(dict(start=start, open=price, high=price + .05,
                                 low=price - .05, close=price))

    def no_funding(self):
        return pd.DataFrame(dict(calc_time=np.array([], dtype='int64'),
                                 last_funding_rate=np.array([], dtype=float)))

    def signal(self, one):
        return dict(signal=True, day=int(one.start.iloc[0] // DAY * DAY),
                    entry_at=int(one.start.iloc[1]), exit_at=int(one.start.iloc[11]))

    def test_exact_ar1_single_component_forecast(self):
        basis = np.sin(np.arange(288) / 287 * np.pi)
        latent = np.arange(60, dtype=float)
        training = latent[:, None] * basis
        predictions, details = forecast_components(training)
        self.assertEqual(details['k'], 1)
        self.assertAlmostEqual(details['explained'], 1.)
        np.testing.assert_allclose(predictions['fmean'], 29.5 * basis, atol=1e-10)
        for lag in (1, 5, 10, 20):
            self.assertEqual(details['score_tests'][str(lag)]['significant_scores'], 1)
            np.testing.assert_allclose(predictions[f'fpar_lag{lag}'], 60 * basis, atol=1e-10)

    def test_zero_variance_returns_mean_without_spurious_pca(self):
        y = np.zeros((30, 288))
        predictions, details = forecast_components(y)
        self.assertEqual(details['k'], 0)
        for p in predictions.values():
            np.testing.assert_array_equal(p, np.zeros(288))
            self.assertIsNone(trade_times(p, 0))

    def test_future_curves_and_future_validity_cannot_change_forecast(self):
        rng = np.random.default_rng(10)
        curves = rng.normal(size=(40, 288))
        days = np.arange(40) * DAY
        old, details = forecast_day(30 * DAY, days, curves, 24)
        altered = curves.copy()
        altered[30:] = 1e8
        fresh, details2 = forecast_day(30 * DAY, days[:35], altered[:35], 24)
        for name in old:
            np.testing.assert_array_equal(old[name], fresh[name])
        self.assertEqual(details, details2)
        self.assertLess(details['training_last_day'], 30 * DAY)

    def test_training_pipeline_future_minute_changes_are_causal(self):
        count = 32 * 1440
        t = 1640995200 + np.arange(count) * 60
        price = 100 + np.sin(np.arange(count) / 300)
        one = pd.DataFrame(dict(start=t, open=price))
        _, valid, curves = day_curves(one)
        day = int(t[28 * 1440])
        old, _ = forecast_day(day, valid, curves, 24)
        altered = one.copy()
        altered.loc[altered.start >= day, 'open'] *= 1000
        altered = altered.drop(index=28 * 1440 + 12)
        _, valid2, curves2 = day_curves(altered)
        fresh, _ = forecast_day(day, valid2, curves2, 24)
        for name in old:
            np.testing.assert_array_equal(old[name], fresh[name])

    def test_training_day_missing_one_minute_never_interpolated(self):
        n = 2 * 1440
        t = 1640995200 + np.arange(n) * 60
        one = pd.DataFrame(dict(start=t, open=np.ones(n) * 100)).drop(index=42)
        days, valid, curves = day_curves(one)
        self.assertEqual(len(days), 2)
        self.assertEqual(valid.tolist(), [1640995200 + DAY])
        self.assertEqual(curves.shape, (1, 288))

    def test_minimum_then_later_maximum_and_one_minute_fills(self):
        curve = np.zeros(288)
        curve[0] = 100
        curve[3] = curve[4] = -2
        curve[8] = curve[9] = 2
        self.assertEqual(trade_times(curve, 0), (3 * 300 + 60, 8 * 300 + 60))
        curve[:] = 1
        curve[-1] = -1
        self.assertIsNone(trade_times(curve, 0))

    def test_delayed_actual_minute_prices_used(self):
        one = self.minutes()
        result = evaluate_forecast(self.signal(one), one, self.no_funding())
        self.assertEqual(result['status'], 'closed')
        self.assertEqual(result['entry_reference'], 100.1)
        self.assertEqual(result['exit_reference'], 101.1)
        self.assertEqual(result['five_minute_path_rows'], 5)
        self.assertGreater(result['gross_return'], result['net_return'])
        self.assertGreater(result['net_return'], result['double_return'])

    def test_missing_execution_or_middle_path_remains_unknown(self):
        one = self.minutes()
        for removed in (1, 5, 11):
            with self.subTest(removed=removed):
                result = evaluate_forecast(self.signal(one), one.drop(index=removed), self.no_funding())
                self.assertEqual(result['status'], 'unknown_missing_execution_or_path')
                self.assertIsNone(result['net_return'])

    def test_missing_outside_trade_does_not_retroactively_cancel(self):
        one = self.minutes()
        result = evaluate_forecast(self.signal(one), one.drop(index=[0, 50]), self.no_funding())
        self.assertEqual(result['status'], 'closed')

    def test_cash_budget_cost_reconciliation(self):
        for scale in (0, 1, 2):
            result = model_return(100, 105, .2, scale)
            pnl = result['quantity'] * 5 - result['fee_cash'] - result['slippage_cash'] - result['funding_cash']
            self.assertAlmostEqual(result['return_'], pnl, places=13)
            spent = result['quantity'] * result['entry_fill'] * (1 + .0006 * scale)
            self.assertAlmostEqual(spent, 1, places=13)

    def test_adverse_funding_both_boundary_events_and_no_positive_credit(self):
        one = self.minutes()
        signal = self.signal(one)
        funding = pd.DataFrame(dict(calc_time=np.array([signal['entry_at'], signal['entry_at'] + 60, signal['exit_at']]) * 1000,
                                     last_funding_rate=[.001, -.1, .002]))
        result = evaluate_forecast(signal, one, funding)
        expected = 100.1 * .001 + 101.1 * .002
        self.assertAlmostEqual(result['funding_adverse_reference_debit_per_unit'], expected)

    def test_cash_no_signal_is_known_zero_not_unknown(self):
        result = evaluate_forecast(dict(signal=False), self.minutes(), self.no_funding())
        self.assertEqual(result['status'], 'cash_no_signal')
        self.assertEqual(result['net_return'], 0.)

    def test_profit_factor_drawdown_and_closed_trade_mean(self):
        returns = np.array([.1, -.05, 0.])
        closed = np.array([True, True, False])
        stats = return_statistics(returns, closed, block_indices(np.arange(3) * DAY))
        self.assertAlmostEqual(stats['observed_subsequence_return_pct'], 4.5)
        self.assertAlmostEqual(stats['observed_daily_drawdown_pct'], 5.)
        self.assertAlmostEqual(stats['pf_trade_returns'], 2.)
        self.assertAlmostEqual(stats['pf_compounded_cash'], .1 / .055)
        self.assertAlmostEqual(stats['mean_closed_trade_return'], .025)


if __name__ == '__main__':
    unittest.main()
