"""Independent artifact/cash checks, not a repeated parameter search."""
import argparse
import gzip
import json
from pathlib import Path

import numpy as np
import pandas as pd

from research.long_history.evaluate import digest


def main(folder):
    p = Path(folder)
    report = json.loads((p / 'report.json').read_text())
    predictions = pd.read_csv(p / 'forecasts.csv.gz')
    trades = pd.read_csv(p / 'daily_and_trades.csv.gz')
    curves = np.load(p / 'forecast_curves.npz')
    assert report['forecast_rows'] == len(predictions) == len(trades) == len(curves['curves'])
    assert np.array_equal(curves['forecast_ids'], predictions.forecast_id)
    assert predictions.forecast_id.is_unique and trades.forecast_id.is_unique
    assert np.array_equal(predictions.forecast_id, trades.forecast_id)
    for name, sha in report['prediction_hashes_before_execution'].items():
        assert digest(p / name) == sha
    maximum_time_error = 0
    for row, curve in zip(predictions.to_dict('records'), curves['curves']):
        i = int(np.argmin(curve))
        j = i + int(np.argmax(curve[i:]))
        assert row['signal'] == (j > i)
        assert row['training_last_day'] < row['day']
        if j > i:
            maximum_time_error = max(maximum_time_error, abs(row['entry_at'] - (row['day'] + 300 * i + 60)), abs(row['exit_at'] - (row['day'] + 300 * j + 60)))
    assert maximum_time_error == 0
    closed = trades[trades.status == 'closed']
    pnl = closed.normal_quantity * (closed.exit_reference - closed.entry_reference) - closed.fee_cash - closed.slippage_cash - closed.funding_cash
    cash_error = float(np.max(np.abs(pnl - closed.net_return)))
    assert cash_error < 1e-12
    assert (closed.gross_return >= closed.net_return).all()
    assert (closed.net_return >= closed.double_return).all()
    unknown = trades[trades.status.str.startswith('unknown_')]
    assert unknown[['gross_return', 'net_return', 'double_return']].isna().all().all()
    years = pd.to_datetime(trades.day, unit='s', utc=True).dt.year
    current = trades[years == 2026]
    assert current.groupby('model').day.nunique().eq(273).all()
    assert current.status.eq('closed').all()
    for name, g in trades.groupby('model'):
        known = g[g.status.isin(['closed', 'cash_no_signal'])]
        assert len(known) == report['summaries'][name]['evaluable_days']
        for label, col in [('gross', 'gross_return'), ('base', 'net_return'), ('double_cost', 'double_return')]:
            compound = (np.prod(1 + known[col]) - 1) * 100
            assert abs(compound - report['summaries'][name]['costs'][label]['observed_subsequence_return_pct']) < 1e-9
    catalog = p / 'input_sources.json'
    compressed = p / 'input_sources.json.gz'
    compressed.write_bytes(gzip.compress(catalog.read_bytes(), mtime=0))
    audit = dict(forecast_rows=len(predictions), closed_model_trade_rows=len(closed), unknown_model_trade_rows=len(unknown),
                 overlapping_models_not_independent_trades=True, max_cash_pnl_abs_error=cash_error,
                 max_scheduled_time_error_seconds=maximum_time_error, predictions_unchanged_after_evaluation=True,
                 unknown_returns_not_imputed=True, all10variants_complete2026days=273,
                 raw_source_catalog_sha256=digest(catalog), compressed_source_catalog_sha256=digest(compressed),
                 report_sha256=digest(p / 'report.json'), audit_code_sha256=digest(__file__),
                 funding_boundary_order='adverse debits inclusive at entry and exit',
                 actual_original_paper_replication=False, native_lbank_execution_verified=False,
                 candidate_decision='reject_this_fixed_component: all10costed scenarios negative,PF<1,expectancy95lower<=0; TP1/R/native/prospective unknown')
    (p / 'validation_audit.json').write_text(json.dumps(audit, indent=2) + '\n')
    print(json.dumps(audit))


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--folder', required=True)
    main(parser.parse_args().folder)
