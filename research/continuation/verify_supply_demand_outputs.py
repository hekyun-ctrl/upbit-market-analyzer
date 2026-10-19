"""Reconcile preserved source-audit evidence without executing external code."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path


def key_zone(z):
    return z["impulse_idx"], z["type"], z["top"], z["bottom"]


def key_trade(t):
    return t["entry_bar"], *key_zone(t["zone"])


def verify(directory):
    directory = Path(directory)
    report_path = directory / "report.json"
    report = json.loads(report_path.read_text())
    payloads, checked = {}, []
    for item in report["artifacts"]:
        blob = (directory / item["file"]).read_bytes()
        assert len(blob) == item["compressed_bytes"]
        assert hashlib.sha256(blob).hexdigest() == item["sha256"]
        raw = gzip.decompress(blob)
        assert len(raw) == item["uncompressed_bytes"]
        assert hashlib.sha256(raw).hexdigest() == item["uncompressed_sha256"]
        payloads[item["file"]] = raw
        checked.append(item["file"])
    bars = list(csv.reader(io.StringIO(payloads["input_30m.csv.gz"].decode())))[1:]
    assert len(bars) == report["real_price_provenance"]["aggregate_bars"] == 1488
    ledger = json.loads(payloads["diagnostic_ledgers.json.gz"])
    prefix, full = ledger["prefix"], ledger["full"]
    cutoff = report["causality_replay"]["cutoff_bars"]
    counts = {}
    for name, run in (("prefix", prefix), ("full", full)):
        closed = [t for t in run["trades"] if t["result"] in ("win", "loss")]
        c = {"zones": len(run["zones"]), "entries": len(run["trades"]),
             "closed_diagnostic_rows": len(closed), "unresolved_open_rows": len(run["trades"])-len(closed),
             "entry_on_formation_bar": sum(t["entry_bar"] == t["zone"]["impulse_idx"] for t in run["trades"]),
             "round_trip_on_formation_bar": sum(t["entry_bar"] == t["exit_bar"] == t["zone"]["impulse_idx"] for t in closed)}
        for k, v in c.items():
            assert v == report["causality_replay"][name][k], (name, k)
        assert abs(math.fsum(t["rr"] for t in closed) -
                   report["causality_replay"][name]["apparent_source_fee_adjusted_R_invalid_as_alpha"]) < 1e-10
        counts[name] = c
    pz = {key_zone(z): z for z in prefix["zones"]}
    fz = {key_zone(z): z for z in full["zones"] if z["impulse_idx"] < cutoff}
    zone_changes = sum(any(pz[k][c] != fz[k][c] for c in ("strength", "trend", "has_bos", "liq_swept"))
                       for k in pz.keys() & fz.keys())
    pt = {key_trade(t): t for t in prefix["trades"]}
    ft = {key_trade(t): t for t in full["trades"] if t["entry_bar"] < cutoff}
    plan_changes, outcome_changes = [], []
    for k in sorted(pt.keys() & ft.keys()):
        if any(pt[k][c] != ft[k][c] for c in ("entry_price", "sl", "tp", "risk", "rr_target")):
            plan_changes.append({"entry_time_utc": pt[k]["entry_time"], "type": pt[k]["zone_type"],
                                 "target_prefix": pt[k]["tp"], "target_with_future": ft[k]["tp"]})
        if pt[k]["result"] in ("win", "loss") and ft[k]["exit_bar"] < cutoff:
            if any(pt[k][c] != ft[k][c] for c in ("result", "exit_bar", "exit_price", "rr")):
                outcome_changes.append({"entry_time_utc": pt[k]["entry_time"],
                    "prefix_exit_utc": pt[k]["exit_time"], "full_exit_utc": ft[k]["exit_time"],
                    "prefix_result": pt[k]["result"], "full_result": ft[k]["result"],
                    "prefix_R": pt[k]["rr"], "with_future_R": ft[k]["rr"]})
    expected = report["causality_replay"]
    assert zone_changes == expected["common_past_zone_changes"]
    assert len(pt.keys() ^ ft.keys()) == expected["past_entry_key_difference"]
    assert len(plan_changes) == expected["common_past_trade_plan_changes"]
    assert len(outcome_changes) == expected["fully_inside_prefix_outcome_changes"]
    return {"status": "causality_defects_and_artifacts_independently_reconciled",
            "report_sha256": hashlib.sha256(report_path.read_bytes()).hexdigest(),
            "artifacts_verified": checked, "input_bars": len(bars), "counts": counts,
            "changed_past_zones": zone_changes, "past_entry_key_difference": len(pt.keys() ^ ft.keys()),
            "changed_past_plans": plan_changes, "changed_closed_outcomes": outcome_changes,
            "market_profitability_certified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    rendered = json.dumps(verify(args.directory), indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered)
