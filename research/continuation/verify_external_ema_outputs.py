"""Independently reconcile preserved forensic ledgers; not alpha certification."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path


def verify(directory):
    directory = Path(directory)
    report = json.loads((directory / "report.json").read_text())
    checked = []
    for item in report["data_artifacts"]:
        path = directory / item["file"]
        payload = path.read_bytes() if path.exists() else gzip.decompress(Path(str(path) + ".gz").read_bytes())
        assert len(payload) == item["bytes"], item["file"]
        assert hashlib.sha256(payload).hexdigest() == item["sha256"], item["file"]
        checked.append(item["file"])
    with (directory / "source_synthetic_trades.csv").open() as handle:
        trades = list(csv.DictReader(handle))
    with (directory / "source_entry_diagnostics.csv").open() as handle:
        entries = list(csv.DictReader(handle))
    gross = [(float(t["exit_price"]) - float(t["entry_price"])) / float(t["entry_price"]) *
             (1 if t["type"] == "BUY" else -1) for t in trades]
    total = math.fsum(gross)
    pf = math.fsum(v for v in gross if v > 0) / -math.fsum(v for v in gross if v < 0)
    prebreak = sum(float(e["entry"]) < float(e["trigger"]) - .0001 if e["side"] == "BUY"
                   else float(e["entry"]) > float(e["trigger"]) + .0001 for e in entries)
    nonpositive = sum(float(e["initial_risk"]) <= 0 for e in entries)
    duplicates = len(entries) - len({(e["setup_time"], e["side"]) for e in entries})
    s = report["synthetic_reproduction"]
    assert len(trades) == len(entries) == s["total_trades"]
    assert abs(total - s["total_return"]) < 1e-12
    assert abs(pf - s["profit_factor"]) < 1e-12
    assert prebreak == s["entries_on_pretrigger_side"]
    assert nonpositive == s["entries_nonpositive_initial_risk"]
    assert duplicates == s["reused_setup_entries"]
    return {"status": "forensic_artifacts_reconciled_not_market_alpha",
            "report_sha256": hashlib.sha256((directory / "report.json").read_bytes()).hexdigest(),
            "artifact_hashes_verified": checked, "closed_synthetic_rows": len(trades),
            "gross_simple_sum": total, "gross_PF": pf,
            "strict_pre_rejection_breakout_entries": prebreak,
            "nonpositive_initial_risk_entries": nonpositive, "reused_setups": duplicates,
            "max_metric_abs_difference": max(abs(total-s["total_return"]), abs(pf-s["profit_factor"])),
            "market_net_profit_certified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("directory", type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = verify(args.directory)
    rendered = json.dumps(result, indent=2, allow_nan=False) + "\n"
    if args.output:
        args.output.write_text(rendered)
    print(rendered)
