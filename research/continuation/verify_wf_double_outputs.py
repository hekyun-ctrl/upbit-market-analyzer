"""Independent scalar/CSV reconciliation of preserved published WF evidence."""
import argparse
import csv
import gzip
import hashlib
import io
import json
import math
from pathlib import Path
import pandas as pd
from audit_wf_double_source import read_xts  # numeric RDS decoding only


def verify(directory):
    directory = Path(directory)
    report = json.loads((directory/"report.json").read_text())
    for item in report["artifacts"]:
        b = (directory/item["path"]).read_bytes()
        assert len(b) == item["bytes"]
        assert hashlib.sha256(b).hexdigest() == item["sha256"]
    checks = []
    for name, result in report["results"].items():
        frame = read_xts((directory/(name+"_09_wf_test_evalued.rds")).read_bytes())
        for scope, df in [("full", frame), ("paper_clip", frame.loc["2019-11-07T00:59:00Z":"2021-08-22T23:59:00Z"])]:
            expected = result[scope]
            source, base, double = [], [], []
            mismatches, missing, extra, flips = 0, 0, 0, []
            gaps = []
            for i in range(1, len(df)):
                a, b = df.iloc[i-1], df.iloc[i]
                seconds = (df.index[i]-df.index[i-1]).total_seconds()
                if seconds != 3600:
                    gaps.append({"previous_utc": df.index[i-1].isoformat(), "next_utc": df.index[i].isoformat(), "seconds": seconds})
                    continue
                sides = abs(b.positions-a.positions)
                charged = round((b.positions*b.Asset_returns-b["return"])/.001)
                mismatches += int(sides != charged)
                missing += max(0, sides-charged)
                extra += max(0, charged-sides)
                if sides:
                    flips.append(df.index[i])
                source.append(b["return"])
                # Derive via incremental correction to stored source PnL,not main audit's gross formula.
                base.append(b["return"]-.001*(sides-charged))
                double.append(b["return"]+.001*charged-.002*sides)
            assert mismatches == expected["selected_path_fee_disagreements"]
            assert missing == expected["missing_charged_sides"] and extra == expected["extra_charged_sides"]
            assert len(flips) == expected["position_flips"]
            assert len(gaps) == expected["nonhourly_edges"]
            for col, values in [("source", source), ("fee_reconciled", base), ("double_fee", double)]:
                assert abs(math.fsum(values)-expected["return_models"][col]["sum_log_return"]) < 1e-12
            b = gzip.decompress((directory/(name+"_"+scope+"_closed.csv.gz")).read_bytes())
            rows = list(csv.DictReader(io.StringIO(b.decode())))
            net, stress = [], []
            for row in rows:
                ratio = float(row["exit_price"])/float(row["entry_price"])
                gross = float(row["direction"])*(ratio-1)
                for key, fee, target in [("net_pct", .001, net), ("double_fee_net_pct", .002, stress)]:
                    value = gross-fee*(1+ratio)
                    assert abs(value-float(row[key])) < 1e-14
                    target.append(value)
            assert len(rows) == expected["closed_diagnostic_segments"]
            for key, vals in [("closed_equal_entry_notional_fee_only", net), ("closed_equal_entry_notional_double_fee_only", stress)]:
                gain = math.fsum(x for x in vals if x > 0)
                loss = -math.fsum(x for x in vals if x < 0)
                assert abs(gain-expected[key]["gains"]) < 1e-12
                assert abs(loss-expected[key]["losses"]) < 1e-12
            checks.append({"dataset": name, "scope": scope, "rows": len(df),
                "cost_disagreements": mismatches, "missing_charged_sides": missing,
                "actual_flips": len(flips), "gaps": gaps, "closed_segments_checked": len(rows),
                "source_and_two_fee_sums_reconciled": True})
    return {"status": "artifact_hashes_scalar_cost_and_closed_cash_ledgers_reconciled",
        "report_sha256": hashlib.sha256((directory/"report.json").read_bytes()).hexdigest(),
        "checks": checks, "scope": "Separate arithmetic path,same researcher;no external institutional validation or bootstrap re-estimation.",
        "market_profitability_certified": False}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory", type=Path)
    p.add_argument("--output", type=Path)
    a = p.parse_args()
    result = json.dumps(verify(a.directory), indent=2, allow_nan=False)+"\n"
    if a.output: a.output.write_text(result)
    print(result)
