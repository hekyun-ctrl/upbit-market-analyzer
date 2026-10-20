"""Pinned source-kernel forensic probes; no model training, orders or market backtest.

External modules are never imported. Only reviewed, manifest-hash-verified pure
function bodies are AST-selected; numba decorators are removed. The live/retrainer
and network main functions cannot execute. Fetching is opt-in, public and pinned.
"""
from __future__ import annotations
import argparse
import ast
import builtins
from contextlib import redirect_stdout
from datetime import datetime, timezone
import hashlib
import io
import json
from pathlib import Path
import platform
from urllib.request import urlopen

import numpy as np
import pandas as pd

HERE = Path(__file__).resolve().parent
MANIFEST = HERE / "lightgbm_source_manifest_20261020.json"
PROTOCOL = HERE / "lightgbm_source_audit_protocol_20261020.json"
RESULTS = HERE / "results" / "lightgbm_source_20261020"


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def blob_sha(raw):
    return hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()


def load_verified(source_dir):
    root = Path(source_dir)
    manifest = json.loads(MANIFEST.read_text())
    protocol = json.loads(PROTOCOL.read_text())
    trees = json.loads((root / "source_tree.json").read_text())
    if sha((root / "source_tree.json").read_bytes()) != manifest["tree"]["sha256"]:
        raise ValueError("Tree metadata hash mismatch")
    if trees["truncated"] or len(trees["tree"]) != manifest["tree"]["entries"]:
        raise ValueError("Incomplete source tree")
    tree_map = {x["path"]: x for x in trees["tree"]}
    source = {}
    for item in manifest["files"]:
        raw = (root / item["path"]).read_bytes()
        if (sha(raw) != item["sha256"] or blob_sha(raw) != item["git_blob_sha"]
                or tree_map[item["path"]]["sha"] != item["git_blob_sha"]):
            raise ValueError("Pinned source mismatch: " + item["path"])
        source[item["path"]] = raw.decode()
    return manifest, protocol, trees, source


def load_kernels(source_dir):
    manifest, protocol, trees, source = load_verified(source_dir)
    safe_names = ("abs", "bool", "dict", "enumerate", "float", "int", "isinstance",
                  "len", "list", "max", "min", "print", "range", "set", "str",
                  "tuple", "zip", "ValueError")
    namespace = {"np": np, "pd": pd, "__builtins__": {
        name: getattr(builtins, name) for name in safe_names}}
    spans = []
    blocked_calls = {"open", "exec", "eval", "compile", "__import__", "input"}
    for relative, names in protocol["allow_functions"].items():
        parsed = ast.parse(source[relative])
        by_name = {n.name: n for n in parsed.body if isinstance(n, ast.FunctionDef)}
        constants = []
        for node in parsed.body:
            if isinstance(node, ast.Assign) and len(node.targets) == 1:
                target = node.targets[0]
                if isinstance(target, ast.Name) and target.id in {
                        "MINUTES_PER_YEAR", "ALPHA001_RANK_WINDOW"}:
                    ast.literal_eval(node.value)
                    constants.append(node)
        functions = []
        for name in names:
            node = by_name[name]
            for child in ast.walk(node):
                if isinstance(child, (ast.Import, ast.ImportFrom, ast.With, ast.AsyncFunctionDef)):
                    raise ValueError("Non-pure node in allowlist")
                if isinstance(child, ast.Call) and isinstance(child.func, ast.Name):
                    if child.func.id in blocked_calls:
                        raise ValueError("Forbidden dynamic/IO call")
                if isinstance(child, ast.Attribute) and child.attr.startswith("__"):
                    raise ValueError("Dunder access rejected")
            node.decorator_list = []
            spans.append({"file": relative, "function": name,
                          "start": node.lineno, "end": node.end_lineno,
                          "AST_body_sha256": sha(ast.dump(node, include_attributes=False).encode())})
            functions.append(node)
        module = ast.Module(body=constants + functions, type_ignores=[])
        ast.fix_missing_locations(module)
        exec(compile(module, "pinned:" + relative, "exec"), namespace)
    return namespace, spans, manifest, protocol, trees, source


def clean(value):
    if value is pd.NA:
        return None
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (np.ndarray, pd.Series, pd.Index)):
        return clean(value.tolist())
    if isinstance(value, (list, tuple)):
        return [clean(v) for v in value]
    if isinstance(value, np.generic):
        return clean(value.item())
    if isinstance(value, float) and not np.isfinite(value):
        return None
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    return value


def fixed_return_case(kernels, fee, stoploss):
    timestamps = pd.date_range("2026-01-15 12:00", periods=4, freq="15min")
    result = kernels["compute_signal_returns"](
        pd.Series([0.0, 1.0, 2.0, -1.0], index=timestamps),
        np.array([0.0, -0.25 if stoploss else 0.0, 0.0, 0.0]),
        timestamps, 100, 100, 90, "15m", fee,
        stoploss=-0.20 if stoploss else None,
        quantile_window=2, quantile_method="rolling",
    )
    valid_times = timestamps[result["valid_mask"]]
    table = kernels["build_trade_table"](
        valid_times, result["gross_arr"], result["net_arr"], result["signal"],
        ["fixed_fixture"] * len(valid_times))
    return {"fee": fee, "predictions": [0.0, 1.0, 2.0, -1.0],
            "target_returns": [0.0, -0.25 if stoploss else 0.0, 0.0, 0.0],
            "quantiles": result["quantiles"],
            "modified_signal": result["signal"], "gross_array": result["gross_arr"],
            "net_array": result["net_arr"], "gross_return": result["gross"],
            "net_return": result["net"], "reported_Total_Trades": result["trades"],
            "closed_table_rows": len(table)}


def run_probes(source_dir):
    k, spans, manifest, protocol, trees, source = load_kernels(source_dir)
    started = datetime.now(timezone.utc).isoformat()
    normal = [fixed_return_case(k, fee, False) for fee in protocol["fixed_inputs"]["fees"]]
    stopped = [fixed_return_case(k, fee, True) for fee in protocol["fixed_inputs"]["fees"]]

    dates = pd.date_range("2026-01-31 23:30", periods=8, freq="15min")
    qs = pd.Series([50.0, 100.0, 95.0, 95.0, 95.0, 95.0, 50.0, 50.0], index=dates)
    month_desired, _ = k["compute_desired_position"](qs, dates, "15m", 100, 90)
    month_held = k["shift_position_for_execution"](month_desired)

    dates2 = pd.date_range("2026-01-15 12:00", periods=4, freq="15min")
    nan_q = pd.Series([100.0, np.nan, 95.0, 50.0], index=dates2)
    nan_desired, nan_valid = k["compute_desired_position"](nan_q, dates2, "15m", 100, 90)
    nan_enter, nan_exit = k["compute_live_level_signals"](nan_desired)

    live_desired = pd.Series([0, 1, 1, 1, 0],
                            index=pd.date_range("2026-01-15", periods=5, freq="15min"))
    held = k["shift_position_for_execution"](live_desired).to_numpy()
    sl_pos, sl_return, sl_flag = k["_apply_stoploss"](
        held, np.array([0.0, 0.0, -0.25, 0.0, 0.0]), -0.20)
    live_enter, _ = k["compute_live_level_signals"](live_desired)

    intrabar_pos, intrabar_ret, intrabar_flags = k["_apply_stoploss"](
        np.array([1, 1, 0]), np.array([0.0, 0.0, 0.0]), -0.20)

    opens = np.array([100.0, 100.0, 90.0, 90.0])
    closes = opens.copy()
    gap_pos, gap_ret, gap_flags = k["_apply_stoploss"](
        np.array([0, 1, 1, 0]), closes / opens - 1.0, -0.20)
    gap_model = float(np.prod(1 + (gap_pos + gap_flags) * gap_ret) - 1)

    t = np.arange(800)
    times = pd.date_range("2026-01-15", periods=800, freq="15min")
    preds = pd.Series(np.sin(t / 7.0) + 0.1 * np.cos(t / 13.0), index=times)
    q_full, _ = k["compute_prediction_quantiles"](preds, 100, window=100)
    q_prefix, _ = k["compute_prediction_quantiles"](preds.iloc[:600], 100, window=100)
    q_changes = int((q_full.iloc[:600].astype("Float64").fillna(-1).to_numpy()
                     != q_prefix.astype("Float64").fillna(-1).to_numpy()).sum())

    base = 100.0 + 2.0 * np.sin(t / 17.0) + 0.01 * t
    oc = base + 0.3 * np.sin(t / 5.0)
    frame = pd.DataFrame({"open": base, "close": oc,
                          "high": np.maximum(base, oc) + 0.5,
                          "low": np.minimum(base, oc) - 0.5,
                          "volume": 50.0 + (t % 11)}, index=times)
    with redirect_stdout(io.StringIO()):
        f_full = k["engineer_features"](frame, interval="15m")
        f_prefix = k["engineer_features"](frame.iloc[:600], interval="15m")
    equal = np.isclose(f_full.iloc[:600].to_numpy(), f_prefix.to_numpy(),
                       rtol=1e-12, atol=1e-12, equal_nan=True)
    feature_changes = int((~equal).sum())
    shift_control = k["shift_position_for_execution"](
        pd.Series([0, 1, 1, 0], index=dates2)).tolist()

    tree_paths = {x["path"] for x in trees["tree"]}
    defaults = {}
    for node in ast.parse(source["src/strategy_contract.py"]).body:
        if isinstance(node, ast.Assign):
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id.startswith("DEFAULT_"):
                    defaults[target.id] = ast.literal_eval(node.value)
    report = {
        "id": protocol["id"], "started_utc": started,
        "completed_utc": datetime.now(timezone.utc).isoformat(),
        "protocol_frozen_utc": protocol["frozen_utc"],
        "source_commit": manifest["commit"],
        "code_sha256": sha(Path(__file__).read_bytes()),
        "manifest_sha256": sha(MANIFEST.read_bytes()),
        "protocol_sha256": sha(PROTOCOL.read_bytes()),
        "environment": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__},
        "source_files_hash_verified": len(manifest["files"]),
        "executed_functions": spans,
        "artifacts": {p: p in tree_paths for p in manifest["required_artifacts"]},
        "code_defaults_not_deployed_artifact": defaults,
        "normal_round_trip_control": normal,
        "entry_bar_stop_fee_and_ledger": {
            "cases": stopped, "economic_entry_and_stop_fee_events": 2,
            "market_prevalence_or_original_profit_impact": None},
        "month_end_new_entry": {
            "timestamps": dates, "quantiles": qs,
            "desired": month_desired, "held_next_bar": month_held,
            "new_month_first_held": int(month_held.iloc[2]),
            "violation": bool(month_desired.iloc[1] == 1)},
        "unknown_quantile_live_exit": {
            "quantiles": nan_q, "valid": nan_valid, "desired": nan_desired,
            "entry_flags": nan_enter, "exit_flags": nan_exit,
            "exit_on_invalid_bar": bool(nan_exit.iloc[1])},
        "stop_lock_vs_live_level_flags": {
            "desired": live_desired, "held_before_stop": held,
            "modified_position": sl_pos, "stop_flags": sl_flag,
            "live_entry_flags": live_enter,
            "next_bar_live_entry_flag_while_model_locked": bool(live_enter.iloc[3] and sl_pos[3] == 0),
            "actual_Freqtrade_order_or_fill_tested": False},
        "intrabar_rebound_approximation": {
            "entry_price": 100.0, "nominal_stop_price": 80.0,
            "fixture_ohlc": {"open": 100.0, "high": 101.0, "low": 75.0, "close": 100.0},
            "source_stop_flags": intrabar_flags,
            "source_uses_low_or_high": False,
            "low_crosses_nominal_stop": True,
            "actual_stop_fill_assumed": False},
        "held_gap_return_omission": {
            "opens": opens, "closes": closes, "positions": [0, 1, 1, 0],
            "source_open_close_product_return": gap_model,
            "continuous_held_entry_to_exit_price_return": float(opens[3] / opens[1] - 1),
            "synthetic_adversarial_only": True},
        "causal_controls": {
            "prediction_rows": 800, "prefix_rows": 600,
            "rolling_quantile_past_changes": q_changes,
            "feature_count": len(f_prefix.columns),
            "feature_past_cells_changed_at_1e_12_tolerance": feature_changes,
            "one_bar_shift": shift_control,
            "not_full_leakage_proof": True},
        "source_review": {
            "funding_slippage_native_latency_fully_modeled": False,
            "public_own_artifact_or_trade_ledger_available": False,
            "TP1_contract_verified": False,
            "stoploss_default_not_user_account_loss_fraction": -0.20,
            "Total_Trades_field_is_position_transitions_not_closed_trades": True,
            "source_documents_only_parity_statistics_not_independent_ledger_validation": True},
        "decision": "exclude_this_release_as_replacement_evidence;missing_artifacts_and_execution_contract_defects;no_salvage_tuning",
        "market_profitability_backtest_performed": False,
        "synthetic_diagnostics_only": True,
        "all_cost_double_profitability": None,
        "net_expectancy_95lower": None, "profit_factor": None, "max_drawdown_R": None,
        "TP1_Wilson_lower": None, "urgent5minute_rate": None, "alerts_per_day": None,
        "native_LBank_fills": False, "forward_started": False, "production_changed": False}
    return clean(report)


def fetch_sources(destination):
    """Recover exact public inputs from persisted URLs; never load any artifacts."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=False)
    manifest = json.loads(MANIFEST.read_text())
    for item in manifest["files"]:
        with urlopen(item["url"], timeout=15) as response:
            raw = response.read(200001)
        if len(raw) > 200000 or sha(raw) != item["sha256"] or blob_sha(raw) != item["git_blob_sha"]:
            raise ValueError("Pinned public source did not match: " + item["path"])
        out = destination / item["path"]
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_bytes(raw)
    tree = RESULTS / "source_tree.json"
    if sha(tree.read_bytes()) != manifest["tree"]["sha256"]:
        raise ValueError("Saved tree mismatch")
    (destination / "source_tree.json").write_bytes(tree.read_bytes())


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", type=Path)
    parser.add_argument("--fetch-sources", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    if args.fetch_sources:
        fetch_sources(args.fetch_sources)
    source_dir = args.source_dir or args.fetch_sources
    if source_dir is None:
        parser.error("Provide --source-dir or --fetch-sources")
    report = run_probes(source_dir)
    rendered = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if args.output:
        with args.output.open("x", encoding="utf-8") as fh:
            fh.write(rendered)
    print(rendered)
