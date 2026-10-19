"""Audit one hash-pinned external implementation, never its startup or plotting.

This reproduces synthetic source behavior, not an investable strategy result.
No third-party source is vendored. Recover it from immutable URLs in protocol.
"""
from __future__ import annotations

import argparse
import ast
import contextlib
import hashlib
import io
import json
import platform
import warnings
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.request import urlopen

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent
PROTOCOL = ROOT / "ema_source_audit_protocol_20261020.json"
NOW = datetime(2026, 3, 30, 8, 54, 15, 576895)


class FrozenDatetime(datetime):
    @classmethod
    def now(cls, tz=None):
        return NOW if tz is None else NOW.replace(tzinfo=timezone.utc).astimezone(tz)


def sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def recover_sources(directory, download=False):
    protocol = json.loads(PROTOCOL.read_text())
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    records = []
    for item in protocol["files"]:
        path = directory / item["path"]
        url = ("https://raw.githubusercontent.com/tradewithbhai7777-creator/"
               f"ema-pullback-momentum-strategy/{protocol['source_commit']}/{item['path']}")
        if not path.exists() and download:
            # Exact immutable public files only; never credentials or config.
            with urlopen(url, timeout=45) as response:
                payload = response.read(200_001)
            if len(payload) > 200_000:
                raise ValueError("Unexpected source size")
            path.write_bytes(payload)
        if not path.exists():
            raise FileNotFoundError(f"Missing {path}; use --download to recover pinned files")
        actual = {"path": item["path"], "url": url, "bytes": path.stat().st_size,
                  "sha256": sha256(path)}
        if any(actual[k] != item[k] for k in ("bytes", "sha256")):
            raise ValueError(f"Source integrity mismatch: {path}")
        records.append(actual)
    return records


def load_inspected_source(path):
    """Hash guard plus AST allowlist; not a general sandbox for arbitrary code."""
    expected = json.loads(PROTOCOL.read_text())["files"][0]["sha256"]
    if sha256(path) != expected:
        raise ValueError("Refuse source other than manually inspected pinned SHA")
    tree = ast.parse(Path(path).read_text())
    wanted = {"EMAPullbackMomentum", "generate_realistic_crypto_data"}
    nodes = [n for n in tree.body if isinstance(n, (ast.ClassDef, ast.FunctionDef))
             and n.name in wanted]
    if {n.name for n in nodes} != wanted:
        raise ValueError("Missing inspected definitions")
    namespace = {"pd": pd, "np": np, "datetime": FrozenDatetime,
                 "timedelta": timedelta, "Dict": Dict, "List": List,
                 "Tuple": Tuple, "Optional": Optional}
    exec(compile(ast.Module(body=nodes, type_ignores=[]), str(path), "exec"), namespace)
    return namespace["EMAPullbackMomentum"], namespace["generate_realistic_crypto_data"], tree


def signal(side="BUY"):
    return {"type": side, "setup_time": pd.Timestamp("2000-01-01T08:00:00"),
            "rejection_candle": pd.Timestamp("2000-01-01T08:00:00"),
            "rejection_high": 105., "rejection_low": 95.,
            "rejection_close": 104., "touched_ema": 100., "setup_price": 104.}


def frame(o=100., h=106., l=99., c=105., ema=94.):
    return pd.DataFrame({"open": [o], "high": [h], "low": [l], "close": [c],
                         "ema_20": [ema], "volume": [1.]},
                        index=pd.DatetimeIndex(["2000-01-01T09:00:00"]))


def position(side="BUY", stop=95., target=105.):
    return {"trade_id": 0, "type": side, "entry_price": 100.,
            "entry_time": pd.Timestamp("2000-01-01T08:00:00"),
            "stop_loss": stop, "take_profit": target, "status": "open", "size": 1.}


def direct_probes(cls, tree):
    output = []
    for name, side, candle in [
        ("buy_prebreak_open", "BUY", frame()),
        ("sell_prebreak_open", "SELL", frame(h=101., l=94., c=95.)),
        ("buy_gap_untraded_fill", "BUY", frame(o=110., h=112., l=109., c=111.)),
        ("sell_gap_untraded_fill", "SELL", frame(o=90., h=91., l=88., c=89.)),
        ("buy_negative_initial_risk", "BUY", frame(o=94., h=106., l=93., c=105.)),
        ("sell_negative_initial_risk", "SELL", frame(o=106., h=107., l=94., c=95.)),
    ]:
        obj = cls({})
        obj.pending_signals = [signal(side)]
        trade = obj.check_breakout_entry(candle, 0)
        entry = float(trade["entry_price"])
        threshold = 105.0001 if side == "BUY" else 94.9999
        output.append({"id": name, "entry": entry, "trigger": threshold,
                       "range_low": float(candle.low.iloc[0]),
                       "range_high": float(candle.high.iloc[0]),
                       "entry_outside_range": not candle.low.iloc[0] <= entry <= candle.high.iloc[0],
                       "entry_on_pretrigger_side": entry < threshold if side == "BUY" else entry > threshold,
                       "initial_risk": float(entry - trade["stop_loss"] if side == "BUY"
                                             else trade["stop_loss"] - entry),
                       "pending_after_trigger": len(obj.pending_signals)})
    for name, candle in [
        ("intrabar_long_stop_ignored", frame(h=103., l=94., c=101.)),
        ("intrabar_long_target_ignored", frame(h=106., l=99., c=101.)),
        ("gap_stop_fill_above_entire_range", frame(o=90., h=92., l=88., c=89.)),
    ]:
        obj = cls({})
        obj.positions = {0: position()}
        obj.update_trades(candle, 0)
        output.append({"id": name, "position_still_open": bool(obj.positions),
                       "closed": len(obj.trades),
                       "exit": float(obj.trades[0]["exit_price"]) if obj.trades else None,
                       "range_low": float(candle.low.iloc[0]),
                       "range_high": float(candle.high.iloc[0])})
    obj = cls({})
    obj.pending_signals = [signal()]
    first = obj.check_breakout_entry(frame(), 0)
    second = obj.check_breakout_entry(frame(), 0)
    output.append({"id": "consumed_signal_reused", "first_trigger": bool(first),
                   "second_trigger_without_new_setup": bool(second),
                   "pending_remaining": len(obj.pending_signals)})
    idx = pd.date_range("2000-01-01", periods=50, freq="h")
    close = pd.Series(100 + np.arange(50) * .1, index=idx)
    adx = cls({}).calculate_adx(close + 1, close - 1, close, 14)
    output.append({"id": "datetime_adx_alignment", "input_rows": len(close),
                   "output_rows": len(adx), "finite_assigned_rows": int(np.isfinite(adx.reindex(idx)).sum())})
    raw = pd.DataFrame({"high": [1., 2., 3., 2., 1.], "low": [0., 1., 2., 1., 0.]})
    changed = raw.copy()
    changed.loc[3, "high"] = 4.
    before, _ = cls({}).find_swing_highs_lows(raw, 1)
    after, _ = cls({}).find_swing_highs_lows(changed, 1)
    strategy = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == "EMAPullbackMomentum")
    active = {"check_buy_setup", "check_sell_setup", "check_breakout_entry", "update_trades"}
    swing_reads = sum(isinstance(n, ast.Constant) and n.value in ("swing_high", "swing_low")
                      for method in strategy.body if isinstance(method, ast.FunctionDef) and method.name in active
                      for n in ast.walk(method))
    output.append({"id": "centered_swing_future_dependency", "past_value_changes": bool(before.iloc[2] != after.iloc[2]),
                   "swing_field_reads_in_entry_exit_methods": swing_reads,
                   "demonstrated_active_signal_leak": False})
    output.append({"id": "flat_trade_has_no_cost", "source_pnl": float(cls({}).calculate_pnl(
        {"type": "BUY", "entry_price": 100., "exit_price": 100.})),
                   "net_model_exists": False})
    return output


def synthetic_audit(cls, generator, directory):
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        samples = {s: generator(s, days=3) for s in ("BTCUSDT", "ETHUSDT", "SOLUSDT")}
        columns = ["open", "high", "low", "close"]
        norm = {s: data[columns].to_numpy() / data.close.iloc[0] for s, data in samples.items()}
        cross_error = max(float(np.max(np.abs(v - norm["BTCUSDT"]))) for v in norm.values())
        data = generator("BTCUSDT", days=365)
    normalized_sample = samples["BTCUSDT"].copy()
    normalized_sample.to_csv(directory / "synthetic_72h_sample.csv", float_format="%.17g")
    # Hooks only observe; none modifies trade decisions or source calculation.
    obj = cls({})
    entries, touched = [], {"stop_touched_but_close_did_not_trigger": 0,
                            "target_touched_but_close_did_not_trigger": 0}
    original_entry, original_update = obj.check_breakout_entry, obj.update_trades

    def observe_entry(prices, i):
        trade = original_entry(prices, i)
        if trade:
            bar, entry = prices.iloc[i], trade["entry_price"]
            long = trade["type"] == "BUY"
            trigger = trade["signal"]["rejection_high"] + .0001 if long else trade["signal"]["rejection_low"] - .0001
            entries.append({"entry_time": str(trade["entry_time"]),
                            "setup_time": str(trade["signal"]["setup_time"]),
                            "side": trade["type"], "entry": float(entry), "trigger": float(trigger),
                            "initial_stop": float(trade["stop_loss"]),
                            "initial_risk": float(entry - trade["stop_loss"] if long else trade["stop_loss"] - entry),
                            "outside_range": bool(not bar.low <= entry <= bar.high),
                            "pretrigger_side": bool(entry < trigger if long else entry > trigger)})
        return trade

    def observe_update(prices, i):
        bar = prices.iloc[i]
        for trade in obj.positions.values():
            long = trade["type"] == "BUY"
            if long:
                missed_stop = bar.low <= trade["stop_loss"] < bar.close
                missed_target = bar.high >= trade["take_profit"] > bar.close
            else:
                missed_stop = bar.high >= trade["stop_loss"] > bar.close
                missed_target = bar.low <= trade["take_profit"] < bar.close
            touched["stop_touched_but_close_did_not_trigger"] += int(missed_stop)
            touched["target_touched_but_close_did_not_trigger"] += int(missed_target)
        return original_update(prices, i)

    obj.check_breakout_entry, obj.update_trades = observe_entry, observe_update
    with warnings.catch_warnings(), contextlib.redirect_stdout(io.StringIO()):
        warnings.simplefilter("ignore")
        result = obj.backtest(data, initial_capital=10_000.)
    ledger = result["trades"].drop(columns=["signal"], errors="ignore")
    ledger.to_csv(directory / "source_synthetic_trades.csv", index=False, float_format="%.17g")
    result["equity_curve"].to_csv(directory / "source_synthetic_equity.csv", float_format="%.17g")
    pd.DataFrame(entries).to_csv(directory / "source_entry_diagnostics.csv", index=False, float_format="%.17g")
    entry_df = pd.DataFrame(entries)
    # Price-only source quantities deliberately labeled gross/synthetic.
    result = {key: float(result[key]) if not isinstance(result[key], int) else result[key]
              for key in ("total_trades", "winning_trades", "losing_trades", "win_rate",
                          "profit_factor", "total_return", "max_drawdown", "sharpe_ratio", "avg_trade_duration")}
    result.update({"data_kind": "synthetic_not_exchange_data", "bars": len(data),
                   "frozen_clock": NOW.isoformat(), "clock_is_audit_fixture_not_verified_original": True,
                   "generated_start": str(data.index[0]), "generated_end": str(data.index[-1]),
                   "assigned_adx_finite": int(np.isfinite(data.adx).sum()),
                   "entries_outside_range": int(entry_df.outside_range.sum()),
                   "entries_on_pretrigger_side": int(entry_df.pretrigger_side.sum()),
                   "entries_nonpositive_initial_risk": int((entry_df.initial_risk <= 0).sum()),
                   "reused_setup_entries": int(entry_df.duplicated(["setup_time", "side"]).sum()),
                   "short_sample_normalized_3symbols_max_abs_error": cross_error,
                   "closed_trades_per_generated_day": len(ledger) / 365,
                   "market_net_performance_validated": False, **touched})
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sources", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--probes-only", action="store_true")
    args = parser.parse_args()
    started = datetime.now(timezone.utc).isoformat()
    sources = recover_sources(args.sources, args.download)
    cls, generator, tree = load_inspected_source(args.sources / "ema_pullback_momentum.py")
    args.output.mkdir(parents=True, exist_ok=True)
    probes = direct_probes(cls, tree)
    synthetic = None if args.probes_only else synthetic_audit(cls, generator, args.output)
    files = [{"file": p.name, "bytes": p.stat().st_size, "sha256": sha256(p)}
             for p in sorted(args.output.iterdir()) if p.is_file() and p.name != "report.json"]
    report = {"candidate_id": "external_ema_pullback_momentum_265209c_forensic_v1",
              "started_utc": started, "completed_utc": datetime.now(timezone.utc).isoformat(),
              "code_sha256": sha256(__file__), "protocol_sha256": sha256(PROTOCOL),
              "versions": {"python": platform.python_version(), "numpy": np.__version__, "pandas": pd.__version__},
              "sources": sources, "direct_probes": probes, "synthetic_reproduction": synthetic,
              "data_artifacts": files, "decision": "reject_source_version_as_replacement_evidence",
              "decision_basis": ["synthetic not actual venue observations", "invalid breakout fill model",
                                 "price-only no net costs", "intrabar stop/target not checked"],
              "scope_limit": "Forensic audit only; no corrected real-market backtest; no claim all EMA strategies fail.",
              "promotion_gates": {k: None for k in ("net_expectancy_95lower", "net_PF", "double_cost_return",
                                                   "drawdown_R", "TP1_Wilson_lower", "native_5m_emergency_rate",
                                                   "native_manual_fill_latency", "qualified_forward_days")},
              "production_changed": False, "forward_collection_started": False}
    (args.output / "report.json").write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(json.dumps({"report": str(args.output / "report.json"), "direct_probes": len(probes),
                      "synthetic_reproduction": synthetic}, indent=2, allow_nan=False))


if __name__ == "__main__":
    main()
