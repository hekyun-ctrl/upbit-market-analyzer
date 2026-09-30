"""Paired missed-winner/blocked-loser audit, without treating scores as odds."""

from collections import defaultdict
from datetime import datetime

REJECTIONS = {"rejected", "survival_rejected", "dispatch_rejected"}


def filter_audit(outcomes, screenings):
    screenings_by_id = defaultdict(list)
    for row in screenings:
        if row.get("signal_id") and row.get("decision") in REJECTIONS:
            screenings_by_id[row["signal_id"]].append(row)
    rows = defaultdict(
        lambda: {"missed_target_first": 0, "blocked_stop_first": 0, "expired": 0}
    )
    paired = 0
    for outcome in outcomes:
        key = {
            "target_first": "missed_target_first",
            "stop_first": "blocked_stop_first",
            "expired": "expired",
        }.get(outcome.get("result"))
        if outcome.get("approved_candidate") or not key or not outcome.get("signal_id"):
            continue
        reasons = set(outcome.get("last_rejections", []))
        for row in screenings_by_id.get(outcome["signal_id"], []):
            # Do not attribute a decision made after the measured outcome.
            if row.get("time_utc") and outcome.get("completed_at_utc"):
                try:
                    if datetime.fromisoformat(row["time_utc"]) > datetime.fromisoformat(
                        outcome["completed_at_utc"]
                    ):
                        continue
                except (ValueError, TypeError):
                    continue
            reasons.update(row.get("reasons", []))
        reasons = {reason.split("(", 1)[0].strip() for reason in reasons if reason}
        if not reasons:
            continue
        paired += 1
        for reason in reasons:
            rows[reason][key] += 1
    return {
        "paired_unapproved_signals": paired,
        "by_reason": [
            {"reason": reason, **counts} for reason, counts in sorted(rows.items())
        ],
        "definition": "미전송 원시 신호의 +5%/-3% 최초 도달; 실제 후보 적중률·계좌 수익이 아님",
        "overlapping_reasons": True,
        "causal_attribution": False,
        "automatic_threshold_tuning": False,
        "restart_scoped": True,
    }
