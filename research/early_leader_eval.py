"""Read-only, pre-registered replay audit for UPBIT EARLY LEADER V1.

Input: JSONL of raw Railway RAW_SIGNAL_OUTCOME objects (or their log lines).
No orders, no alerts, no hindsight-based market selection. A signal's features
must be those captured at original signal time, NOT after its outcome.
The comparison is a historical *screening* audit, not executable PnL.
"""
import argparse
import json
from collections import Counter
from datetime import datetime, timezone

OUTCOMES = {"target_first", "stop_first", "expired"}
RULES = {
    "baseline_all_raw": lambda x: True,
    "relative_strength_top10": lambda x: (
        isinstance(x.get("relative_strength_percentile"), (int, float))
        and 0 <= x["relative_strength_percentile"] <= 10
    ),
    "top10_and_5m_up1": lambda x: (
        isinstance(x.get("relative_strength_percentile"), (int, float))
        and 0 <= x["relative_strength_percentile"] <= 10
        and isinstance(x.get("momentum_5m_pct"), (int, float))
        and x["momentum_5m_pct"] >= 1
    ),
}

def unpack(line):
    """Consume only outcome records, never mistake a screening for an outcome."""
    if "RAW_SIGNAL_OUTCOME " in line:
        line = line.split("RAW_SIGNAL_OUTCOME ", 1)[1]
    elif not line.lstrip().startswith("{"):
        return None
    try:
        row = json.loads(line)
    except json.JSONDecodeError:
        return None
    if not isinstance(row, dict) or row.get("result") not in OUTCOMES:
        return None
    return row

def load(lines):
    """Dedupe by signal id; differing outcomes for same id are an audit error."""
    by_id = {}
    for line in lines:
        row = unpack(line)
        if row is None:
            continue
        sid = row.get("signal_id")
        if not isinstance(sid, str) or not sid:
            raise ValueError("outcome without stable signal_id")
        if sid in by_id and by_id[sid] != row:
            raise ValueError("conflicting duplicate outcome: " + sid)
        by_id[sid] = row
    return list(by_id.values())

def date_utc(row):
    """Split only by original signal time; never by later outcome close."""
    raw = row.get("signal_time_utc")
    if not isinstance(raw, str):
        raise ValueError("missing original signal_time_utc")
    dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError("signal_time_utc must carry timezone")
    return dt.astimezone(timezone.utc).date().isoformat()

def tally(rows, round_trip_cost_pct=0.2):
    n = len(rows)
    win = sum(x["result"] == "target_first" for x in rows)
    loss = sum(x["result"] == "stop_first" for x in rows)
    unresolved = n - win - loss
    resolved = win + loss
    return {
        "signals": n, "target_first": win, "stop_first": loss,
        "expired": unresolved,
        "target_first_rate_resolved_pct": round(100 * win / resolved, 3) if resolved else None,
        "hypothetical_gross_pct_per_resolved": round((5 * win - 3 * loss) / resolved, 4) if resolved else None,
        "hypothetical_net_pct_per_resolved": round((5 * win - 3 * loss) / resolved - round_trip_cost_pct, 4) if resolved else None,
        "estimated_fee_slippage_pct": round_trip_cost_pct,
        "approved_trade_count": sum(x.get("approved_candidate") is True for x in rows),
    }

def compare(records, train_date, holdout_date, round_trip_cost_pct=0.2):
    """Frozen rules: never tune on holdout or count multiple outcomes for one ID."""
    if train_date == holdout_date:
        raise ValueError("holdout must be a distinct UTC signal date")
    cohorts = {train_date: [], holdout_date: []}
    for x in records:
        d = date_utc(x)
        if d in cohorts:
            cohorts[d].append(x)
    if not cohorts[train_date] or not cohorts[holdout_date]:
        raise ValueError("both dates require actual outcome records")
    results = {}
    for name, rule in RULES.items():
        results[name] = {}
        for day, rows in cohorts.items():
            selected = [x for x in rows if rule(x)]
            results[name][day] = tally(selected, round_trip_cost_pct)
    return {
        "train_date": train_date, "holdout_date": holdout_date,
        "rules_frozen_before_holdout": list(RULES),
        "results": results,
        "important": [
            "Log outcome reports are not fills or executable performance.",
            "Overlapping signals remain correlated; no independent-trade claim.",
            "Missing original-time depth, spread, slippage and rejected-candidate timing.",
            "No production promotion based on this audit alone.",
        ],
        "production_approval": False,
    }

def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("jsonl", help="Locally exported outcome log; never fetch private keys")
    parser.add_argument("--train-date", default="2026-11-17")
    parser.add_argument("--holdout-date", default="2026-11-18")
    parser.add_argument("--round-trip-cost-pct", type=float, default=0.2)
    args = parser.parse_args()
    if not 0 <= args.round_trip_cost_pct <= 5:
        parser.error("cost must be between zero and five percent")
    with open(args.jsonl, encoding="utf-8") as f:
        report = compare(load(f), args.train_date, args.holdout_date,
                         args.round_trip_cost_pct)
    print(json.dumps(report, ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
