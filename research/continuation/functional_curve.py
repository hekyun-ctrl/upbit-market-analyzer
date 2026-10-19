"""Causal, published Fmean/FPAR component screen, never an order executor.

Protocol frozen at53ddbda; explicit source ambiguities/adaptations remain.
Predictions and curves are saved before target-price evaluation begins.
"""
import argparse
import hashlib
import json
import platform
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import scipy
from scipy.linalg import svd
from scipy.stats import chi2

from research.long_history.evaluate import digest, load_archives, verify_manifests

DAY = 86400
FEE, SLIP = .0006, .0002
WINDOWS, LAGS = (182, 365), (1, 5, 10, 20)
INPUTS = [('binance-futures-blind-2022', 'integrity_final_2022.json'),
          ('binance-futures-blind-2023', 'integrity_final_2023.json'),
          ('binance-futures-verified', 'integrity_final_2024_2026.json')]


def write_json(path, value):
    Path(path).write_text(json.dumps(value, indent=2, allow_nan=False) + '\n')


def restore_inputs(catalog, root):
    """Restore only fixed SHA sources; never silently adopt updated public bytes."""
    source = json.loads(Path(catalog).read_text())
    allowed = {x[0] for x in INPUTS}
    for block in source['folders']:
        if block['folder'] not in allowed:
            raise ValueError('Unexpected input folder')
        folder = Path(root) / block['folder']
        folder.mkdir(parents=True, exist_ok=True)
        for manifest in block['manifests']:
            if manifest['name'] not in ('manifest-1m.json', 'manifest-5m.json'):
                raise ValueError('Unexpected manifest name')
            for r in manifest['value']['archives']:
                if r['kind'] not in ('klines', 'fundingRate') or r['interval'] not in ('1m', '5m'):
                    raise ValueError('Unexpected source kind/interval')
                dest = folder / r['kind'] / r['interval'] / Path(r['url']).name
                dest.parent.mkdir(parents=True, exist_ok=True)
                if not dest.exists() or digest(dest) != r['sha256']:
                    with urllib.request.urlopen(r['url'], timeout=60) as response:
                        data = response.read()
                    if hashlib.sha256(data).hexdigest() != r['sha256']:
                        raise ValueError('Published bytes changed from frozen SHA: ' + r['url'])
                    dest.write_bytes(data)
            # Original bytes preserve the recorded manifest SHA, including harmless old paths.
            text = manifest['original_text']
            if hashlib.sha256(text.encode()).hexdigest() != manifest['sha256']:
                raise ValueError('Frozen manifest metadata changed')
            (folder / manifest['name']).write_text(text)


def load_inputs(root):
    parts, funds, catalog, original, removed = [], [], [], 0, 0
    research = Path(__file__).resolve().parents[1] / 'long_history'
    for folder_name, audit_name in INPUTS:
        folder = Path(root) / folder_name
        verify_manifests(folder)
        manifests = []
        for interval in ('1m', '5m'):
            p = folder / f'manifest-{interval}.json'
            text = p.read_text()
            manifests.append(dict(name=p.name, sha256=digest(p), original_text=text, value=json.loads(text)))
        audit = research / audit_name
        bad = json.loads(audit.read_text())['quarantined_starts']
        m = load_archives(folder, '1m')
        keep = ~(m.start // 300 * 300).isin(bad)
        original += len(m)
        removed += int((~keep).sum())
        parts.append(m.loc[keep, ['start', 'open', 'high', 'low', 'close']])
        funds.append(load_archives(folder, '5m', 'fundingRate'))
        catalog.append(dict(folder=folder_name, manifests=manifests,
                            quarantine_file=str(audit.relative_to(research.parent.parent)),
                            quarantine_sha256=digest(audit), quarantined_starts=bad))
    one = pd.concat(parts, ignore_index=True).sort_values('start').reset_index(drop=True)
    fund = pd.concat(funds, ignore_index=True).sort_values('calc_time').reset_index(drop=True)
    if one.start.duplicated().any() or fund.calc_time.duplicated().any():
        raise ValueError('Duplicate minute or funding event')
    ft = (fund.calc_time.to_numpy() / 1000).astype('int64')
    expected = np.arange(int(one.start.min() // DAY * DAY), int(one.start.max()), 8 * 3600)
    if not np.array_equal(ft, expected) or not np.isfinite(fund.last_funding_rate.to_numpy()).all():
        raise ValueError('Funding eight-hour source coverage not complete; do not assume zero')
    return one, fund, dict(folders=catalog, original_minutes=original, removed_minutes=removed,
                           remaining_minutes=len(one), funding_rows=len(fund), funding_schedule_verified=True)


def day_curves(one):
    """Training eligibility is determined only after each past UTC day closes."""
    t = one.start.to_numpy(dtype='int64')
    all_days = np.arange(t[0] // DAY * DAY, t[-1] // DAY * DAY + DAY, DAY)
    valid_days, curves = [], []
    for day in all_days:
        left, right = np.searchsorted(t, [day, day + DAY])
        times = t[left:right]
        if len(times) == 1440 and np.array_equal(times, np.arange(day, day + DAY, 60)):
            price = one.open.to_numpy()[left:right:5]
            valid_days.append(int(day))
            curves.append(100 * np.log(price / price[0]))
    return all_days, np.asarray(valid_days, dtype='int64'), np.asarray(curves)


def ljung_box_pvalues(scores, lags=LAGS):
    """Standard Ljung-Box Q with no fitted-AR degrees-of-freedom subtraction."""
    n, k = scores.shape
    centered = scores - scores.mean(axis=0)
    denominator = (centered * centered).sum(axis=0)
    acf = np.zeros((max(lags), k))
    for h in range(1, max(lags) + 1):
        acf[h - 1] = np.divide((centered[h:] * centered[:-h]).sum(axis=0), denominator,
                               out=np.zeros(k), where=denominator > 0)
    q = n * (n + 2) * np.cumsum(acf * acf / (n - np.arange(1, max(lags) + 1))[:, None], axis=0)
    return {h: chi2.sf(q[h - 1], h) for h in lags}


def forecast_components(training, lags=LAGS):
    """Receives only past-day curves; no target-day prices or quality flags."""
    y = np.asarray(training, dtype=float)
    if y.ndim != 2 or len(y) <= max(lags) + 1 or not np.isfinite(y).all():
        raise ValueError('Invalid historical training matrix')
    mean = y.mean(axis=0)
    centered = y - mean
    if np.max(np.abs(centered)) <= 1e-14:
        return {'fmean': mean, **{f'fpar_lag{h}': mean.copy() for h in lags}}, dict(k=0, explained=1., score_tests={})
    u, s, vt = svd(centered, full_matrices=False, check_finite=False, lapack_driver='gesdd')
    variance = s * s
    k = min(len(s), int(np.searchsorted(np.cumsum(variance) / variance.sum(), .9)) + 1)
    scores = u[:, :k] * s[:k]
    pvalues = ljung_box_pvalues(scores, lags)
    x, target = scores[:-1], scores[1:]
    mx, my = x.mean(axis=0), target.mean(axis=0)
    denom = ((x - mx) ** 2).sum(axis=0)
    phi = np.divide(((x - mx) * (target - my)).sum(axis=0), denom,
                    out=np.zeros(k), where=denom > 0)
    intercept = my - phi * mx
    raw_pred = intercept + phi * scores[-1]
    predictions = {'fmean': mean}
    tests = {}
    for h in lags:
        accepted = pvalues[h] < .05
        score_pred = np.where(accepted, raw_pred, 0.)
        predictions[f'fpar_lag{h}'] = mean + score_pred @ vt[:k]
        tests[str(h)] = dict(pvalues=pvalues[h].tolist(), ar1_coefficients=phi.tolist(),
                              predicted_scores=score_pred.tolist(), significant_scores=int(accepted.sum()))
    return predictions, dict(k=k, explained=float(variance[:k].sum() / variance.sum()), score_tests=tests)


def forecast_day(day, valid_days, curves, window):
    stop = int(np.searchsorted(valid_days, day, side='left'))
    if stop < window:
        return None
    days = valid_days[stop - window:stop]
    predicted, details = forecast_components(curves[stop - window:stop])
    details.update(training_first_day=int(days[0]), training_last_day=int(days[-1]),
                   training_days=window, training_calendar_span_days=int((day - days[0]) // DAY))
    if days[-1] >= day:
        raise AssertionError('Target-day leakage')
    return predicted, details


def trade_times(prediction, day):
    curve = np.asarray(prediction)
    if curve.shape != (288,) or not np.isfinite(curve).all():
        raise ValueError('Invalid daily forecast grid')
    entry = int(np.argmin(curve))
    exit_ = entry + int(np.argmax(curve[entry:]))
    if exit_ <= entry:
        return None
    return int(day + entry * 300 + 60), int(day + exit_ * 300 + 60)


def save_forecasts(all_days, valid_days, curves, output):
    rows, arrays = [], []
    for window in WINDOWS:
        for count, day in enumerate(all_days):
            result = forecast_day(day, valid_days, curves, window)
            if result is None:
                continue
            predicted, details = result
            # No actual target-day price or target-day quality is consulted here.
            for name, curve in predicted.items():
                times = trade_times(curve, day)
                row = dict(forecast_id=len(rows), day=int(day), model=f'{name}_s{window}',
                           window=window, component_count=details['k'], explained=details['explained'],
                           training_first_day=details['training_first_day'], training_last_day=details['training_last_day'],
                           training_calendar_span_days=details['training_calendar_span_days'],
                           signal=times is not None, entry_at=None if times is None else times[0],
                           exit_at=None if times is None else times[1],
                           score_test_diagnostics=json.dumps(details['score_tests'].get(name.replace('fpar_lag', ''), {}), separators=(',', ':')))
                rows.append(row)
                arrays.append(curve)
            if count % 120 == 0:
                print(json.dumps(dict(phase='forecast', window=window, day=pd.Timestamp(day, unit='s', tz='UTC').isoformat(), saved_rows=len(rows))), flush=True)
    table = pd.DataFrame(rows)
    table.to_csv(output / 'forecasts.csv.gz', index=False, compression=dict(method='gzip', mtime=0))
    np.savez_compressed(output / 'forecast_curves.npz', curves=np.asarray(arrays), forecast_ids=table.forecast_id.to_numpy())
    write_json(output / 'forecast_checkpoint.json', dict(completed_utc=datetime.now(timezone.utc).isoformat(),
               phase='predictions_complete_before_target_execution_evaluation', rows=len(rows),
               artifacts={n: digest(output / n) for n in ('forecasts.csv.gz', 'forecast_curves.npz')}))
    print(json.dumps(dict(phase='forecasts_saved', rows=len(rows))), flush=True)
    return table


def model_return(entry, exit_, funding_debit_per_unit, scale):
    """Unit cash funded long/cash proxy; quantity includes entry fee budget."""
    entry_fill, exit_fill = entry * (1 + scale * SLIP), exit_ * (1 - scale * SLIP)
    quantity = 1 / (entry_fill * (1 + scale * FEE))
    cash = quantity * (exit_fill * (1 - scale * FEE) - funding_debit_per_unit * scale)
    return dict(return_=float(cash - 1), quantity=float(quantity), entry_fill=float(entry_fill), exit_fill=float(exit_fill),
                fee_cash=float(quantity * scale * FEE * (entry_fill + exit_fill)),
                slippage_cash=float(quantity * scale * SLIP * (entry + exit_)),
                funding_cash=float(quantity * funding_debit_per_unit * scale))


def evaluate_forecast(row, one, funding):
    base = dict(row)
    if not row['signal']:
        return dict(base, status='cash_no_signal', gross_return=0., net_return=0., double_return=0.)
    entry, exit_ = int(row['entry_at']), int(row['exit_at'])
    t = one.start.to_numpy(dtype='int64')
    left, right = np.searchsorted(t, [entry, exit_], side='left')
    needed = np.arange(entry, exit_ + 60, 60)
    actual = t[left:right + 1]
    if right >= len(t) or not np.array_equal(actual, needed):
        return dict(base, status='unknown_missing_execution_or_path', gross_return=None, net_return=None, double_return=None)
    path = one.iloc[left:right]
    ep, xp = float(one.open.iloc[left]), float(one.open.iloc[right])
    # Conservative funding boundary order: debit entry/exit coincidence too.
    ft = (funding.calc_time.to_numpy() / 1000).astype('int64')
    a, b = np.searchsorted(ft, [entry, exit_], side='left')
    b = int(np.searchsorted(ft, exit_, side='right'))
    funding_debit = 0.
    for i in range(int(a), b):
        ti = int(np.searchsorted(t, ft[i]))
        if ti >= len(t) or t[ti] != ft[i]:
            return dict(base, status='unknown_missing_funding_reference', gross_return=None, net_return=None, double_return=None)
        funding_debit += float(one.open.iloc[ti]) * max(float(funding.last_funding_rate.iloc[i]), 0.)
    gross, normal, stress = [model_return(ep, xp, funding_debit, scale) for scale in (0, 1, 2)]
    if min(gross['return_'], normal['return_'], stress['return_']) <= -1:
        raise ValueError('Invalid funded proxy cash; do not hide ruin')
    first5 = path.iloc[:5]
    return dict(base, status='closed', entry_reference=ep, exit_reference=xp,
                gross_return=gross['return_'], net_return=normal['return_'], double_return=stress['return_'],
                fee_cash=normal['fee_cash'], slippage_cash=normal['slippage_cash'], funding_cash=normal['funding_cash'],
                normal_quantity=normal['quantity'], normal_entry_fill=normal['entry_fill'], normal_exit_fill=normal['exit_fill'],
                funding_adverse_reference_debit_per_unit=funding_debit,
                holding_minutes=(exit_ - entry) / 60,
                mae_ref_pct=float(min(0., float(path.low.min()) / ep - 1) * 100),
                mfe_ref_pct=float(max(0., float(path.high.max()) / ep - 1) * 100),
                mae_5min_ref_pct=float(min(0., float(first5.low.min()) / ep - 1) * 100),
                five_minute_path_rows=len(first5))


def block_indices(dates, draws=2000, seed=20261020):
    dates = np.asarray(dates)
    n = len(dates)
    if not n:
        return np.empty((0, 0), dtype='int64')
    ends, end = np.empty(n, dtype=int), n
    for i in range(n - 1, -1, -1):
        if i == n - 1 or dates[i + 1] - dates[i] != DAY:
            end = i + 1
        ends[i] = min(i + 7, end)
    rng = np.random.default_rng(seed)
    sample = np.empty((draws, n), dtype='int64')
    for draw in range(draws):
        selected, count = [], 0
        while count < n:
            first = int(rng.integers(n))
            piece = np.arange(first, ends[first])
            selected.append(piece)
            count += len(piece)
        sample[draw] = np.concatenate(selected)[:n]
    return sample


def return_statistics(returns, closed, indices):
    r = np.asarray(returns, dtype=float)
    eq = np.r_[1., np.cumprod(1 + r)]
    cash_pnl = eq[:-1] * r
    wins, losses = float(r[r > 0].sum()), float(-r[r < 0].sum())
    closed_r = r[closed]
    boot = r[indices]
    trade_counts = closed[indices].sum(axis=1)
    boot_trade = np.divide(boot.sum(axis=1), trade_counts, out=np.full(len(indices), np.nan), where=trade_counts > 0)
    cash_positive, cash_negative = float(cash_pnl[cash_pnl > 0].sum()), float(-cash_pnl[cash_pnl < 0].sum())
    return dict(observed_subsequence_return_pct=float((eq[-1] - 1) * 100),
                observed_daily_drawdown_pct=float(-(eq / np.maximum.accumulate(eq) - 1).min() * 100),
                mean_daily_return=float(r.mean()), mean_closed_trade_return=None if not len(closed_r) else float(closed_r.mean()),
                daily_mean_block95=np.quantile(boot.mean(axis=1), [.025, .975]).tolist(),
                closed_trade_mean_day_block95=None if not np.isfinite(boot_trade).any() else np.nanquantile(boot_trade, [.025, .975]).tolist(),
                pf_trade_returns=None if losses == 0 else wins / losses,
                pf_compounded_cash=None if cash_negative == 0 else cash_positive / cash_negative,
                positive_trade_return_sum=wins, negative_trade_return_abs_sum=losses,
                positive_cash_pnl_sum=cash_positive, negative_cash_pnl_abs_sum=cash_negative,
                closed_trade_win_rate=None if not len(closed_r) else float((closed_r > 0).mean()))


def summarize_model(frame):
    known = frame[frame.status.isin(['closed', 'cash_no_signal'])].sort_values('day')
    closed = known.status.eq('closed').to_numpy()
    dates = known.day.to_numpy(dtype='int64')
    indices = block_indices(dates)
    if not len(known):
        return dict(forecast_days=len(frame), evaluable_days=0)
    levels = {'gross': 'gross_return', 'base': 'net_return', 'double_cost': 'double_return'}
    stats = {name: return_statistics(known[col].to_numpy(), closed, indices) for name, col in levels.items()}
    years = {}
    yy = pd.to_datetime(known.day, unit='s', utc=True).dt.year
    for year in sorted(yy.unique()):
        g = known[yy == year]
        years[str(year)] = dict(observed_days=len(g), closed_trades=int(g.status.eq('closed').sum()),
            **{name: dict(return_pct=float((np.prod(1 + g[col]) - 1) * 100),
                          mean_closed_trade_return=None if not g.status.eq('closed').any() else float(g.loc[g.status.eq('closed'), col].mean()))
               for name, col in levels.items()})
    trades = known[known.status == 'closed']
    n_signals = int(frame.signal.sum())
    return dict(forecast_days=len(frame), first_forecast_day=int(frame.day.min()), last_forecast_day=int(frame.day.max()),
                evaluable_days=len(known), unknown_days=len(frame) - len(known), closed_trades=len(trades),
                signals=n_signals, signals_per_forecast_calendar_day=n_signals / len(frame),
                opportunity_day_fraction=n_signals / len(frame), evaluable_signals_per_evaluable_day=len(trades) / len(known),
                status_counts={str(k): int(v) for k, v in frame.status.value_counts().items()},
                mean_holding_minutes=None if trades.empty else float(trades.holding_minutes.mean()),
                median_mae_5min_ref_pct=None if trades.empty else float(trades.mae_5min_ref_pct.median()),
                price_adverse_within5m_fraction=None if trades.empty else float((trades.mae_5min_ref_pct < 0).mean()),
                worst_trade_mae_ref_pct=None if trades.empty else float(trades.mae_ref_pct.min()),
                five_minute_complete_path_fraction=None if trades.empty else float(trades.five_minute_path_rows.eq(5).mean()),
                costs=stats, years=years,
                uncertainty='Unadjusted exploratory2000resample7day blocks, never cross calendar holes; not an independent or familywise alpha proof.',
                equity_scope='Concatenated evaluable day subsequence. Unknown days have unknown returns, not0; full-calendar account return/drawdown not established.',
                tp1_wilson_lower=None, max_drawdown_r=None, native_urgent_followup_5min_rate=None,
                native_manual_fills_verified=False, forward_evidence=False)


def main(output, data_root=None, restore=None):
    started = datetime.now(timezone.utc).isoformat()
    out = Path(output)
    out.mkdir(parents=True, exist_ok=True)
    root = Path(data_root) if data_root else Path(__file__).resolve().parents[3]
    if restore:
        restore_inputs(restore, root)
    one, fund, catalog = load_inputs(root)
    write_json(out / 'input_sources.json', catalog)
    days, valid, curves = day_curves(one)
    print(json.dumps(dict(phase='verified_inputs', raw_minutes=catalog['original_minutes'], quarantined_minutes=catalog['removed_minutes'],
                          complete_training_days=len(valid), calendar_days=len(days))), flush=True)
    forecasts = save_forecasts(days, valid, curves, out)
    prediction_hashes = {n: digest(out / n) for n in ('forecasts.csv.gz', 'forecast_curves.npz', 'forecast_checkpoint.json')}
    # This barrier is preserved in the report and code: no target prices evaluated before prediction persistence.
    evaluated = pd.DataFrame([evaluate_forecast(r, one, fund) for r in forecasts.to_dict('records')])
    evaluated.to_csv(out / 'daily_and_trades.csv.gz', index=False, compression=dict(method='gzip', mtime=0))
    if prediction_hashes != {n: digest(out / n) for n in prediction_hashes}:
        raise AssertionError('Predictions changed during execution evaluation')
    summary = {str(name): summarize_model(g) for name, g in evaluated.groupby('model', sort=True)}
    protocol = Path(__file__).with_name('functional_curve_component_protocol_20261020.json')
    report = dict(candidate_id='bouri_2021_functional_curve_component_v1', started_utc=started,
        completed_utc=datetime.now(timezone.utc).isoformat(), protocol_sha256=digest(protocol), code_sha256=digest(__file__),
        versions=dict(python=platform.python_version(), numpy=np.__version__, pandas=pd.__version__, scipy=scipy.__version__),
        original_minutes=catalog['original_minutes'], quarantined_minutes=catalog['removed_minutes'],
        calendar_days=len(days), complete_training_days=len(valid), invalid_training_days=sorted(set(days.tolist()) - set(valid.tolist())),
        model_count=len(summary), forecast_rows=len(forecasts), prediction_hashes_before_execution=prediction_hashes,
        summaries=summary, source_catalog_sha256=digest(out / 'input_sources.json'),
        artifacts={p.name: digest(p) for p in out.iterdir() if p.is_file() and p.name != 'report.json'},
        all_data_previously_observed=True, full_original_paper_replication=False,
        unresolved_source_differences=json.loads(protocol.read_text())['source_ambiguities'],
        implementation_details=['Unit funded long/cash return proxy, not margin/liquidation accounting; no leverage.',
          'Funding debits use actual funding-minute original opens, adverse rates only, inclusive entry/exit boundary worst-case order.',
          'Uniform discrete FPCA basis scaling cancels in score reconstruction; OLS AR1 intercept; no coefficient clipping.',
          'No hard stop/TP1/R/native warning contract in source, these promotion metrics remain unknown.'],
        production_changed=False, forward_collection_started=False, profitable_replacement_certified=False)
    write_json(out / 'report.json', report)
    print(json.dumps(dict(phase='finished', model_count=len(summary), results={n:dict(base_pct=v['costs']['base']['observed_subsequence_return_pct'],
        double_pct=v['costs']['double_cost']['observed_subsequence_return_pct'], unknown=v['unknown_days']) for n, v in summary.items()})), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', required=True)
    parser.add_argument('--data-root')
    parser.add_argument('--restore-inputs')
    args = parser.parse_args()
    main(args.output, args.data_root, args.restore_inputs)
