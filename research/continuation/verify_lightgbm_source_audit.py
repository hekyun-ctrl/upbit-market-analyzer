"""Independent saved-output arithmetic checks; stdlib only, no source imports."""
import argparse
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import json
from pathlib import Path


def verify(path):
    path = Path(path)
    r = json.loads(path.read_text())
    checks = []
    for row in r["normal_round_trip_control"]:
        fee = Decimal(str(row["fee"]))
        expected = (Decimal(1) - fee) ** 2 - 1
        assert abs(Decimal(str(row["net_return"])) - expected) < Decimal("1e-12")
        assert row["reported_Total_Trades"] == 2 and row["closed_table_rows"] == 1
        checks.append("normal_round_trip_two_fee_events_" + str(fee))
    stopped = r["entry_bar_stop_fee_and_ledger"]["cases"]
    for row in stopped:
        assert abs(row["gross_return"] + .2) < 1e-12
        assert row["gross_array"] == row["net_array"]
        assert row["reported_Total_Trades"] == 0 and row["closed_table_rows"] == 0
        assert row["modified_signal"] == [0, 0, 0]
    assert stopped[0]["net_array"] == stopped[1]["net_array"]
    # Fees from the economic unmodified position path, not its post-stop mask.
    economic = [1, 1, 0]
    prev = [0] + economic[:-1]
    expected_events = sum(abs(a-b) for a,b in zip(economic, prev))
    assert expected_events == 2
    checks.append("entry_bar_stop_loss_retained_but_two_fee_events_and_closed_row_missing")
    month = r["month_end_new_entry"]
    assert month["timestamps"][1].startswith("2026-01-31T23:45")
    assert month["desired"][1] == 1 and month["held_next_bar"][2] == 1
    checks.append("month_end_flat_can_open_new_trade")
    missing = r["unknown_quantile_live_exit"]
    assert missing["quantiles"][1] is None and missing["valid"][1] is False
    assert missing["desired"][1] == 0 and missing["exit_flags"][1] is True
    checks.append("unknown_quantile_becomes_exit_flag")
    retry = r["stop_lock_vs_live_level_flags"]
    assert retry["modified_position"][3] == 0 and retry["live_entry_flags"][3] is True
    assert retry["actual_Freqtrade_order_or_fill_tested"] is False
    checks.append("locked_model_and_persistent_entry_flag_differ_only")
    intrabar = r["intrabar_rebound_approximation"]
    assert intrabar["fixture_ohlc"]["low"] < intrabar["nominal_stop_price"]
    assert sum(intrabar["source_stop_flags"]) == 0
    checks.append("open_close_only_kernel_omits_low_breach")
    gap = r["held_gap_return_omission"]
    price_return = Decimal(str(gap["opens"][3])) / Decimal(str(gap["opens"][1])) - 1
    assert price_return == Decimal("-.1") and gap["source_open_close_product_return"] == 0
    checks.append("held_gap_price_leg_omitted")
    controls = r["causal_controls"]
    assert controls["rolling_quantile_past_changes"] == 0
    assert controls["feature_past_cells_changed_at_1e_12_tolerance"] == 0
    assert controls["one_bar_shift"] == [0, 0, 1, 1]
    checks.append("prefix_and_shift_controls_reconciled")
    assert not any(r["artifacts"].values())
    for name in ("profit_factor","net_expectancy_95lower","all_cost_double_profitability",
                 "TP1_Wilson_lower","urgent5minute_rate","alerts_per_day","max_drawdown_R"):
        assert r[name] is None
    assert not r["market_profitability_backtest_performed"]
    checks.append("missing_artifacts_and_all_unassessed_gates_are_not_passes")
    return {"status": "saved_scalar_Decimal_and_event_checks_passed",
            "completed_utc": datetime.now(timezone.utc).isoformat(),
            "report_sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "check_count": len(checks), "checks": checks,
            "independence": "Same researcher, separate stdlib scalar/Decimal implementation;not external institutional validation.",
            "profitability_verified": False, "forward_started": False}


if __name__ == "__main__":
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("report", type=Path)
    p.add_argument("--output", type=Path)
    a = p.parse_args()
    out = json.dumps(verify(a.report), indent=2, allow_nan=False) + "\n"
    if a.output:
        with a.output.open("x") as f:
            f.write(out)
    print(out)
