"""Hash-pinned source causality audit with a bounded real-price replay."""
from __future__ import annotations

import argparse
import ast
import gzip
import hashlib
import io
import json
import platform
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from urllib.request import urlopen

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
PROTOCOL_PATH = ROOT / "supply_demand_audit_protocol_20261020.json"


def sha(payload):
    return hashlib.sha256(payload).hexdigest()


def protocol():
    return json.loads(PROTOCOL_PATH.read_text())


def recover_sources(directory, download=False):
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    p = protocol()
    out = []
    for item in p["executed_source_files"]:
        path = directory / item["path"]
        url = ("https://raw.githubusercontent.com/unreadable/trading-farm/"
               f"{p['source_commit']}/{item['path']}")
        if not path.exists() and download:
            with urlopen(url, timeout=45) as response:
                content = response.read(200001)
            if len(content) > 200000:
                raise ValueError("Unexpected source size")
            path.write_bytes(content)
        content = path.read_bytes()
        if len(content) != item["bytes"] or sha(content) != item["sha256"]:
            raise ValueError(f"Refuse changed source: {path}")
        out.append({**item, "url": url})
    return out


def constant_value(node, values):
    if isinstance(node, ast.Constant):
        return node.value
    if isinstance(node, ast.Name):
        return values[node.id]
    if isinstance(node, (ast.List, ast.Tuple)):
        v = [constant_value(n, values) for n in node.elts]
        return tuple(v) if isinstance(node, ast.Tuple) else v
    if isinstance(node, ast.Dict):
        return {constant_value(k, values): constant_value(v, values) for k, v in zip(node.keys, node.values)}
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -constant_value(node.operand, values)
    raise ValueError(f"Unsupported constant expression: {ast.dump(node)}")


def load_source(directory):
    recover_sources(directory)
    directory = Path(directory)
    values = {}
    for node in ast.parse((directory / "config.py").read_text()).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            name = node.targets[0].id
            if name != "OUTPUT_DIR":
                values[name] = constant_value(node.value, values)
    namespace = {"pd": pd, "np": np, **values}
    # Exact source was inspected. This is an allowlist loader,not a general sandbox.
    for filename in ("structure.py", "zones.py", "backtest.py"):
        tree = ast.parse((directory / filename).read_text())
        functions = [n for n in tree.body if isinstance(n, ast.FunctionDef)]
        exec(compile(ast.Module(body=functions, type_ignores=[]), filename, "exec"), namespace)
    return namespace


def encode(value):
    if isinstance(value, (pd.Timestamp, datetime)):
        return value.isoformat()
    if isinstance(value, np.generic):
        return value.item()
    raise TypeError(type(value).__name__)


def json_bytes(value):
    return (json.dumps(value, indent=2, default=encode, allow_nan=False) + "\n").encode()


def frame(count=31):
    return pd.DataFrame({"open": 101., "high": 101.2, "low": 100.8,
                         "close": 101., "volume": 100.},
                        index=pd.date_range("2000-01-01", periods=count, freq="30min", tz="UTC"))


def known_zone():
    return {"type": "demand", "top": 100., "bottom": 99., "mid": 99.5,
            "impulse_idx": 19, "strength": 3., "timeframe": "30m"}


def known_run(ns, mode):
    df = frame()
    df.iloc[20] = [100., 100.5, 99.5, 100., 100.]
    if mode == "gap":
        df.iloc[21] = [95., 96., 94., 95., 100.]
    if mode in ("both_touched", "close_suppresses"):
        df.iloc[20] = [100., 106., 97., 98. if mode == "close_suppresses" else 100., 100.]
    trades, equity = ns["walkforward_backtest"](
        df, [known_zone()], pd.Series(1., index=df.index), liq_levels=[], params=ns["OPTIMAL_PARAMS"])
    return trades, equity


def probes(ns):
    z = known_zone()
    future_level = {"type": "equal_highs", "price": 108., "first_idx": 90, "last_idx": 100}
    fallback = ns["find_dynamic_tp"](z, 100., 5., [z], [], ns["OPTIMAL_PARAMS"])
    target = ns["find_dynamic_tp"](z, 100., 5., [z], [future_level], ns["OPTIMAL_PARAMS"])
    levels = [{"type": "equal_lows", "price": 100., "first_idx": 5, "last_idx": 10}]
    base = frame(50)
    base.loc[:, ["open", "high", "low", "close"]] = [100.2, 100.3, 100.1, 100.2]
    altered = base.copy()
    altered.iloc[45, altered.columns.get_loc("low")] = 99.9
    swept_before = ns["check_liquidity_swept"](base, levels, z)
    swept_after = ns["check_liquidity_swept"](altered, levels, z)
    peak = frame(40)
    peak.iloc[25] = [101., 104., 100.8, 103., 100.]
    sh_full, _ = ns["detect_swing_points"](peak, 5)
    sh_prefix, _ = ns["detect_swing_points"](peak.iloc[:27], 5)
    impulse = frame()
    impulse.loc[:, ["open", "high", "low", "close"]] = [100., 100.1, 99.9, 100.]
    impulse.iloc[20] = [100., 102.1, 99.9, 102., 100.]
    zs, _ = ns["detect_zones"](impulse, "30m", no_lookahead=True)
    born, _ = ns["walkforward_backtest"](impulse, zs, ns["compute_atr"](impulse),
                                         liq_levels=[], params=ns["OPTIMAL_PARAMS"])
    gap, _ = known_run(ns, "gap")
    both, _ = known_run(ns, "both_touched")
    suppressed, _ = known_run(ns, "close_suppresses")
    unresolved, _ = known_run(ns, "open")
    gt = gap[0]
    expected_fee = gt["position_size"] * (gt["entry_price"] * ns["FEE_RATE_MAKER"] +
                   gt["exit_price"] * (ns["FEE_RATE_TAKER"] + ns["SL_SLIPPAGE_PCT"]))
    result = {
        "future_target": {"fallback": fallback, "with_future_level": target,
                           "level_first_bar": 90, "level_last_bar": 100,
                           "entry_zone_known_at_bar": 19},
        "future_sweep": {"before": swept_before, "after_future_low_change": swept_after,
                          "zone_impulse_bar": 19, "only_changed_bar": 45,
                          "historical_prefix_unchanged": bool(base.iloc[:20].equals(altered.iloc[:20]))},
        "unconfirmed_swing": {"swing25_in_full": any(t[0] == 25 for t in sh_full),
                              "swing25_in_prefix_ending26": any(t[0] == 25 for t in sh_prefix),
                              "first_confirmation_bar": 30},
        "forming_bar_round_trip": [{"entry_bar": t["entry_bar"], "exit_bar": t["exit_bar"],
                                     "impulse_bar": t["zone"]["impulse_idx"],
                                     "result": t["result"], "entry_price": t["entry_price"],
                                     "tp": t["tp"]} for t in born],
        "gap_stop": {"exit_price": gt["exit_price"], "bar_open": 95., "bar_high": 96.,
                     "bar_low": 94., "source_net_R": gt["rr"]},
        "closing_freshness": {"unchanged_OHL_close100_trades": len(both),
                              "same_OHL_close98_trades": len(suppressed),
                              "first_result_with_close100": both[0]["result"]},
        "both_touched_stop_first": {"result": both[0]["result"], "exit": both[0]["exit_price"]},
        "unresolved_data_end": {"result": unresolved[0]["result"],
                                "reported_R": unresolved[0]["rr"], "completed_trade": False},
        "fee_arithmetic_control": {"recorded_fee": gt["fee"], "unrounded_price_fee": expected_fee,
                                   "source_gross_R": gt["rr_gross"],
                                   "base_R_from_same_fills": gt["rr_gross"]-expected_fee/gt["risk_amount"],
                                   "double_fee_R_same_fills": gt["rr_gross"]-2*expected_fee/gt["risk_amount"],
                                   "actual_funding_included": False},
    }
    return result


def market_input(archive, download=False):
    p = protocol()["real_price_replay"]
    archive = Path(archive)
    if not archive.exists() and download:
        archive.parent.mkdir(parents=True, exist_ok=True)
        with urlopen(p["url"], timeout=60) as response:
            payload = response.read(p["zip_bytes"] + 1)
        archive.write_bytes(payload)
    payload = archive.read_bytes()
    if len(payload) != p["zip_bytes"] or sha(payload) != p["sha256"]:
        raise ValueError("Raw monthly archive differs from frozen official SHA")
    with zipfile.ZipFile(io.BytesIO(payload)) as z:
        names = [n for n in z.namelist() if n.endswith(".csv")]
        if len(names) != 1:
            raise ValueError("Unexpected archive members")
        content = z.read(names[0])
    first = content.splitlines()[0].split(b",")[0]
    raw = pd.read_csv(io.BytesIO(content), header=None, skiprows=0 if first.isdigit() else 1)
    times = pd.to_numeric(raw[0]).to_numpy(dtype="int64")
    expected = np.arange(p["start_ms"], p["end_exclusive_ms"], 60000, dtype="int64")
    if not np.array_equal(times, expected):
        raise ValueError("Missing,duplicate or out-of-order minute")
    if not np.array_equal(pd.to_numeric(raw[6]).to_numpy(dtype="int64"), times + 59999):
        raise ValueError("Invalid minute close times")
    minute = raw.iloc[:, 1:6].astype(float)
    minute.columns = ["open", "high", "low", "close", "volume"]
    minute.index = pd.to_datetime(times, unit="ms", utc=True)
    if not np.isfinite(minute.to_numpy()).all() or (minute.volume < 0).any():
        raise ValueError("Invalid numbers/volume")
    if ((minute.low > minute[["open", "close"]].min(axis=1)) |
        (minute.high < minute[["open", "close"]].max(axis=1))).any():
        raise ValueError("Invalid OHLC envelope")
    counts = minute.close.resample("30min").count()
    if not (counts == 30).all():
        raise ValueError("Partial30m interval")
    bars = minute.resample("30min").agg({"open": "first", "high": "max", "low": "min",
                                        "close": "last", "volume": "sum"})
    assert len(bars) == p["bars"]
    return bars, {"url": p["url"], "checksum_url": p["checksum_url"], "zip_sha256": sha(payload),
                  "zip_bytes": len(payload), "csv_bytes_sha256": sha(content), "minute_rows": len(minute),
                  "missing_minutes": 0, "duplicate_minutes": 0, "bad_OHLC_or_volume": 0,
                  "quarantined_minutes_in_frozen_month": 0, "aggregate_bars": len(bars),
                  "first": str(bars.index[0]), "last": str(bars.index[-1]),
                  "previously_observed": True, "native_LBank": False}


def replay(ns, bars):
    zones, structure = ns["detect_zones"](bars, "30m", no_lookahead=True)
    trades, equity = ns["walkforward_backtest"](bars, zones, ns["compute_atr"](bars),
        liq_levels=structure["liquidity_levels"], params=ns["OPTIMAL_PARAMS"])
    return {"zones": zones, "trades": trades, "equity": equity}


def zone_key(z):
    return (z["impulse_idx"], z["type"], z["top"], z["bottom"])


def trade_key(t):
    return (t["entry_bar"], *zone_key(t["zone"]))


def compare_replays(prefix, full, cutoff):
    pz = {zone_key(z): z for z in prefix["zones"]}
    fz = {zone_key(z): z for z in full["zones"] if z["impulse_idx"] < cutoff}
    changes = []
    for key in sorted(pz.keys() & fz.keys()):
        fields = {k: [pz[key][k], fz[key][k]] for k in ("strength", "trend", "has_bos", "liq_swept")
                  if pz[key][k] != fz[key][k]}
        if fields:
            changes.append({"zone": key, "changes": fields})
    pt = {trade_key(t): t for t in prefix["trades"]}
    ft = {trade_key(t): t for t in full["trades"] if t["entry_bar"] < cutoff}
    plan_changes, outcome_changes = [], []
    for key in sorted(pt.keys() & ft.keys()):
        fields = {k: [pt[key][k], ft[key][k]] for k in ("entry_price", "sl", "tp", "risk", "rr_target")
                  if pt[key][k] != ft[key][k]}
        if fields:
            plan_changes.append({"trade": key, "changes": fields})
        # Do not flag a prefix-open trade merely resolved by a future candle.
        if pt[key]["result"] != "open" and ft[key]["exit_bar"] < cutoff:
            fields = {k: [pt[key][k], ft[key][k]] for k in ("result", "exit_bar", "exit_price", "rr")
                      if pt[key][k] != ft[key][k]}
            if fields:
                outcome_changes.append({"trade": key, "changes": fields})
    return {"common_zones": len(pz.keys() & fz.keys()), "past_zone_changes": changes,
            "past_zone_key_difference": sorted(pz.keys() ^ fz.keys()),
            "past_entry_key_difference": sorted(pt.keys() ^ ft.keys()),
            "common_past_trades": len(pt.keys() & ft.keys()), "past_trade_plan_changes": plan_changes,
            "fully_inside_prefix_outcome_changes": outcome_changes}


def diagnostic_counts(run):
    ts = run["trades"]
    closed = [t for t in ts if t["result"] != "open"]
    return {"zones": len(run["zones"]), "entries": len(ts), "closed_diagnostic_rows": len(closed),
            "unresolved_open_rows": len(ts)-len(closed),
            "entry_on_formation_bar": sum(t["entry_bar"] == t["zone"]["impulse_idx"] for t in ts),
            "round_trip_on_formation_bar": sum(t["entry_bar"] == t["exit_bar"] == t["zone"]["impulse_idx"]
                                               and t["result"] != "open" for t in ts),
            "apparent_source_fee_adjusted_R_invalid_as_alpha": sum(t["rr"] for t in closed),
            "market_profitability_validated": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--archive", type=Path)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    started = datetime.now(timezone.utc).isoformat()
    sources = recover_sources(args.sources, args.download)
    ns = load_source(args.sources)
    args.output.mkdir(parents=True, exist_ok=True)
    small = probes(ns)
    counts = None
    artifacts = []
    raw_info = None
    if args.archive:
        bars, raw_info = market_input(args.archive, args.download)
        cutoff = protocol()["real_price_replay"]["fixed_prefix_bars"]
        prefix_input, full_input = bars.iloc[:cutoff].copy(), bars.copy()
        assert prefix_input.equals(full_input.iloc[:cutoff])
        prefix = replay(ns, prefix_input)
        full = replay(ns, full_input)
        comparison = compare_replays(prefix, full, cutoff)
        counts = {"prefix": diagnostic_counts(prefix), "full": diagnostic_counts(full),
                  "cutoff_bars": cutoff, "prefix_identical": bool(prefix_input.equals(full_input.iloc[:cutoff])),
                  "common_past_zone_changes": len(comparison["past_zone_changes"]),
                  "past_entry_key_difference": len(comparison["past_entry_key_difference"]),
                  "common_past_trade_plan_changes": len(comparison["past_trade_plan_changes"]),
                  "fully_inside_prefix_outcome_changes": len(comparison["fully_inside_prefix_outcome_changes"])}
        for filename, payload in [
            ("input_30m.csv.gz", bars.to_csv(float_format="%.17g").encode()),
            ("diagnostic_ledgers.json.gz", json_bytes({"prefix": prefix, "full": full, "comparison": comparison}))
        ]:
            compressed = gzip.compress(payload, mtime=0)
            (args.output / filename).write_bytes(compressed)
            artifacts.append({"file": filename, "compressed_bytes": len(compressed), "sha256": sha(compressed),
                              "uncompressed_bytes": len(payload), "uncompressed_sha256": sha(payload)})
    report = {"candidate_id": protocol()["candidate_id"], "started_utc": started,
              "completed_utc": datetime.now(timezone.utc).isoformat(),
              "protocol_sha256": sha(PROTOCOL_PATH.read_bytes()), "code_sha256": sha(Path(__file__).read_bytes()),
              "versions": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__},
              "source_commit": protocol()["source_commit"], "sources": sources,
              "fixed_OPTIMAL_PARAMS": ns["OPTIMAL_PARAMS"], "small_probes": small,
              "real_price_provenance": raw_info, "causality_replay": counts, "artifacts": artifacts,
              "interpretation": "Source defects and real-price replay diagnostics only;no corrected strategy or native LBank profitability test.",
              "decision": "reject_this_source_version_for_causality_and_execution",
              "unverified_promotion_gates": ["net_expectancy95lower", "netPF", "same_signal_double_cost_profit",
                    "TP1Wilson", "DDinitialR", "native5min_urgent", "manual_native_fill_latency", "forward30days100closed"],
              "production_changed": False, "forward_collection_started": False}
    (args.output / "report.json").write_bytes(json_bytes(report))
    print(json.dumps({"report": str(args.output / "report.json"), "probes": small,
                      "causality_replay": counts}, indent=2, default=encode, allow_nan=False))


if __name__ == "__main__":
    main()
