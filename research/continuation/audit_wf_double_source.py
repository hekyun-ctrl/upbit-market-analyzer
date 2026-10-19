"""Reconcile published, fixed BTC WF ledgers. Never execute external R code."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import math
import sys
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen
import zipfile

import numpy as np
import pandas as pd
import rdata
import yaml

PROTOCOL = Path(__file__).with_name("wf_double_source_audit_protocol_20261020.json")


def digest(b):
    return hashlib.sha256(b).hexdigest()


def read_xts(blob):
    obj = rdata.parser.parse_data(blob, extension=".rds").object
    attrs = rdata.conversion.convert(obj.attributes)
    dims = tuple(map(int, attrs["dim"]))
    values = np.asarray(obj.value, dtype=float).reshape(dims, order="F")
    names = list(map(str, attrs["dimnames"][1]))
    idx = pd.to_datetime(np.asarray(attrs["index"], dtype=float), unit="s", utc=True)
    return pd.DataFrame(values, index=idx, columns=names)


def read_params(blob):
    obj = rdata.parser.parse_data(blob, extension=".rds")
    return rdata.conversion.convert(obj)


def actual_turnover(positions, valid_edges=None):
    p = np.asarray(positions, dtype=float)
    out = np.zeros(len(p), dtype=float)
    out[1:] = np.abs(np.diff(p))
    if valid_edges is not None:
        out[~valid_edges] = np.nan
    out[0] = np.nan  # inherited exposure,initial opening unknown
    return out


def drawdown(logs):
    curve = np.exp(np.r_[0.0, np.cumsum(logs)])
    return float(np.max(1.0 - curve / np.maximum.accumulate(curve)))


def bootstrap_days(df, column):
    daily = df[column].groupby(df.index.floor("D")).sum().to_numpy()
    rng = np.random.default_rng(20261020)
    n = len(daily)
    block = min(7, n)
    means = []
    for _ in range(2000):
        starts = rng.integers(0, n, size=math.ceil(n/block))
        indexes = (starts[:, None]+np.arange(block)[None, :]) % n
        means.append(float(np.mean(daily[indexes.ravel()[:n]])))
    return {"days": n, "mean_daily_log_return": float(np.mean(daily)),
            "block_days": block, "replicates": 2000, "seed": 20261020,
            "ci95": np.quantile(means, [0.025, 0.975]).tolist()}


def closed_segments(df, fee=0.001):
    gaps = df.index.to_series().diff().dt.total_seconds().ne(3600).to_numpy()
    p = df.positions.to_numpy()
    flips = np.flatnonzero(np.r_[False, p[1:] != p[:-1]])
    trades = []
    for start, end in zip(flips[:-1], flips[1:]):
        if np.any(gaps[start:end+1]):
            continue  # no artificial gap liquidation
        ep, xp = float(df.Asset.iloc[start-1]), float(df.Asset.iloc[end-1])
        side = int(p[start])
        gross = side*(xp/ep-1)
        sides = 1+xp/ep
        trades.append({"entry_utc": df.index[start-1].isoformat(),
            "exit_utc": df.index[end-1].isoformat(), "direction": side,
            "entry_price": ep, "exit_price": xp, "gross_pct": gross,
            "base_fee_pct": fee*sides, "net_pct": gross-fee*sides,
            "double_fee_net_pct": gross-2*fee*sides})
    return trades


def profit_factor(values):
    gain = math.fsum(x for x in values if x > 0)
    loss = -math.fsum(x for x in values if x < 0)
    return {"gains": gain, "losses": loss, "PF": gain/loss if loss else None,
            "mean": float(np.mean(values)) if values else None}


def audit_frame(df):
    assert df.index.is_monotonic_increasing and df.index.is_unique
    assert np.isfinite(df.to_numpy()).all()
    assert (df.Asset > 0).all() and set(df.positions.unique()) <= {-1.0, 1.0}
    gap = df.index.to_series().diff().dt.total_seconds()
    valid = gap.eq(3600).to_numpy()
    actual = actual_turnover(df.positions, valid)
    gross = df.positions.to_numpy()*df.Asset_returns.to_numpy()
    implied = (gross-df["return"].to_numpy())/0.001
    rounded = np.rint(implied)
    assert np.max(np.abs(implied-rounded)) < 1e-8
    assert set(rounded) <= {0.0, 2.0}
    known = np.isfinite(actual)
    delta = actual-rounded
    boundary = df.test_start.diff().ne(0).to_numpy()
    mismatch = known & (np.abs(delta) > 1e-8)
    direct_rets = np.r_[np.nan, np.diff(np.log(df.Asset.to_numpy()))]
    price_error = np.max(np.abs(direct_rets[known]-df.Asset_returns.to_numpy()[known]))
    assert price_error < 1e-10
    fixed = gross-0.001*np.nan_to_num(actual)
    stress = gross-0.002*np.nan_to_num(actual)
    # Exclude unknown entry/gap rows from all compared aggregates,do not fake cost-free entries.
    work = pd.DataFrame({"source": df["return"].to_numpy()[known],
                         "fee_reconciled": fixed[known], "double_fee": stress[known]},
                        index=df.index[known])
    trades = closed_segments(df)
    flips = (actual == 2)
    day_set = set(df.index.floor("D"))
    opportunity_days = len(set(df.index[flips].floor("D")))
    full = {"rows": len(df), "known_return_rows": int(known.sum()),
        "first_utc": df.index[0].isoformat(), "last_utc": df.index[-1].isoformat(),
        "observed_days": len(day_set), "nonhourly_edges": int((~valid[1:]).sum()),
        "duplicate_timestamps": int(df.index.duplicated().sum()),
        "source_price_return_max_error": float(price_error),
        "position_flips": int(flips.sum()), "flips_per_day": float(flips.sum()/len(day_set)),
        "opportunity_days": opportunity_days, "opportunity_day_fraction": opportunity_days/len(day_set),
        "selected_path_fee_disagreements": int(mismatch.sum()),
        "disagreements_on_WF_boundaries": int((mismatch & boundary).sum()),
        "missing_charged_sides": float(np.nansum(np.maximum(delta, 0))),
        "extra_charged_sides": float(np.nansum(np.maximum(-delta, 0))),
        "inherited_start_and_unresolved_end_are_not_closed": True,
        "closed_diagnostic_segments": len(trades),
        "closed_equal_entry_notional_fee_only": profit_factor([t["net_pct"] for t in trades]),
        "closed_equal_entry_notional_double_fee_only": profit_factor([t["double_fee_net_pct"] for t in trades]),
        "return_models": {}}
    for col in work:
        vals = work[col].to_numpy()
        full["return_models"][col] = {"sum_log_return": float(math.fsum(vals)),
            "exp_log_return_pct_diagnostic": float(np.expm1(math.fsum(vals))*100),
            "max_drawdown_pct_diagnostic": drawdown(vals)*100,
            "daily_uncertainty": bootstrap_days(work, col)}
    examples = []
    for i in np.flatnonzero(mismatch):
        examples.append({"utc": df.index[i].isoformat(),
            "previous_position": int(df.positions.iloc[i-1]), "current_position": int(df.positions.iloc[i]),
            "source_charged_sides": int(rounded[i]), "actual_sides": int(actual[i]),
            "WF_boundary": bool(boundary[i]), "source_net_log_return": float(df["return"].iloc[i])})
    full["cost_disagreement_records"] = examples
    return full, trades


def recover_archive(path, download=False):
    p = json.loads(PROTOCOL.read_text())["data"]
    path = Path(path)
    if not path.exists() and download:
        with urlopen(p["download_url"], timeout=55) as response:
            content = response.read()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
    b = path.read_bytes()
    if len(b) != p["zip_bytes"] or digest(b) != p["zip_sha256"]:
        raise ValueError("Author ZIP differs from frozen archive;do not silently substitute")
    return path


def run(archive, output):
    started = datetime.now(timezone.utc).isoformat()
    protocol = json.loads(PROTOCOL.read_text())
    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    results, manifest, artifacts = {}, [], []
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            if not item.is_dir():
                b = z.read(item)
                manifest.append({"path": item.filename, "bytes": len(b), "sha256": digest(b)})
        for ds in protocol["data"]["datasets"]:
            name = ds["name"]
            prefix = "unseen_period_results/dvc-exps/"+name+"/"
            for fname, hash_key in [("09_wf_test_evalued.rds", "ledger_sha256"),
                                     ("08_wf_best_params.rds", "best_params_sha256"),
                                     ("metrics.yaml", "metrics_sha256")]:
                b = z.read(prefix+fname)
                assert digest(b) == ds[hash_key], fname
                dest = output/(name+"_"+fname)
                dest.write_bytes(b)
                artifacts.append({"path": dest.name, "bytes": len(b), "sha256": digest(b)})
            frame = read_xts(z.read(prefix+"09_wf_test_evalued.rds"))
            windows = read_params(z.read(prefix+"08_wf_best_params.rds"))
            for col in ["train_start", "train_end", "test_start", "test_end"]:
                windows[col] = pd.to_numeric(windows[col])
            assert ((windows.train_end < windows.test_start) &
                    (windows.train_start <= windows.train_end) &
                    (windows.test_start <= windows.test_end)).all()
            assert np.all(np.diff(windows.test_start.to_numpy(dtype=float)) > 0)
            full, trades = audit_frame(frame)
            a, b = protocol["data"]["fixed_paper_clip_utc"]
            clipped, clipped_trades = audit_frame(frame.loc[a:b])
            for scope, ts in [("full", trades), ("paper_clip", clipped_trades)]:
                buf = io.StringIO()
                cols = ["entry_utc", "exit_utc", "direction", "entry_price", "exit_price", "gross_pct", "base_fee_pct", "net_pct", "double_fee_net_pct"]
                writer = csv.DictWriter(buf, fieldnames=cols)
                writer.writeheader(); writer.writerows(ts)
                blob = gzip.compress(buf.getvalue().encode(), mtime=0)
                dest = output/(name+"_"+scope+"_closed.csv.gz")
                dest.write_bytes(blob)
                artifacts.append({"path": dest.name, "bytes": len(blob), "sha256": digest(blob)})
            results[name] = {"full": full, "paper_clip": clipped,
                "window_count": len(windows), "train_end_before_test_start": True,
                "source_parameter_selection_not_reoptimized": True,
                "stored_source_metrics": yaml.safe_load(z.read(prefix+"metrics.yaml")),
                "no_SL_TP1_contract": True}
    (output/"zip_members.json").write_text(json.dumps(manifest, indent=2)+"\n")
    report = {"candidate_id": protocol["candidate_id"], "started_utc": started,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_sha256": digest(PROTOCOL.read_bytes()), "code_sha256": digest(Path(__file__).read_bytes()),
        "versions": {"python": sys.version.split()[0], "numpy": np.__version__, "pandas": pd.__version__, "rdata": rdata.__version__},
        "results": results, "artifacts": artifacts,
        "limitations": ["Source .1% aggregatefee model;actual slippage/spread/funding not separately observed.",
            "Log-short return approximation and equal-entry-notional closed segments are diagnostic models,not a native account replay.",
            "Author prices processed with imputation;hourly-grid checks do not validate original raw observations.",
            "No initial-R,TP1,5minurgent,manualfilllatency,forward30days100closed evidence."],
        "decision": "not_qualified_as_standalone_manual_alert",
        "valid_native_profitability_backtest": False, "production_changed": False, "forward_started": False}
    (output/"report.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    return report


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--archive", type=Path, required=True)
    p.add_argument("--output", type=Path, required=True)
    p.add_argument("--download", action="store_true")
    a = p.parse_args()
    report = run(recover_archive(a.archive, a.download), a.output)
    print(json.dumps({"candidate_id": report["candidate_id"], "decision": report["decision"],
        "datasets": {k: {s: {m: v[s][m] for m in ["rows", "position_flips", "flips_per_day", "selected_path_fee_disagreements"]}
        for s in ["full", "paper_clip"]} for k, v in report["results"].items()}}, indent=2))
