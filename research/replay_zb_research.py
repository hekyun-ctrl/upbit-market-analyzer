"""Same-snapshot comparison on the retained LBank candles and recorded prices.

Run from the repository root with: python -m research.replay_zb_research
The first archive window is diagnostic. Later windows are held out by time.
The source lacks historical candle receipt times and executable order books.
"""
import argparse
import json
import logging
import tempfile
from collections import Counter
from pathlib import Path
from statistics import mean
from unittest.mock import patch

from research.compare_entry_refinement import DATA, available_state, load_archives, make_feed, timestamp
from research.compare_entry_refinement import monitor
from research.zb_counterfactual_replay import ReplaySignal, replay_one
from research.zb_signal_research import COSTS, MAIN_STOP_CAP, MAX_CHASE, MIN_NET_RR, PIVOT_BUFFER, STUDY_STOP_CAP
from research.zb_signal_research import evaluate_snapshot

DELAY_SECONDS = 15
HOLD_BARS = 60
BASELINE_COMMIT = "080093b22bb3fdd10cc01e6914188298edd96f91"
BASELINE_MODE = "capture"
TERMINAL = ("target_first", "stop_first")
POLICIES = ("existing_at_native_threshold", "existing_common_2r", "modified_060", "modified_080")


def _candidate_key(row):
    return row["minute_start"], row["direction"], row["lane"], row.get("basis")


def _archive_windows(bundles):
    windows = set()
    for bundle in bundles:
        minute_starts = [b[1] for b in bundle["native_rows"] if b[0] == 60]
        windows.add((min(minute_starts), max(minute_starts)))
    return sorted(windows)


def _shadow_rows(snapshot, at, close, quote):
    result = []
    directions = snapshot["timeframes"]
    for item in snapshot["candidates"]:
        for basis, plan in item["plans"].items():
            result.append(dict(at=at, minute_start=snapshot["minute_start"], close=close, quote=quote,
                              lane=item["lane"], direction=item["direction"], trigger=item["trigger"],
                              signal_basis=item["signal_basis"], basis=basis, plan=plan,
                              trend={x:directions[x]["direction"] for x in ("1m","5m","15m","1h","4h")},
                              main_bias=directions["main_bias"], conflict=directions["higher_conflict"]))
    return result


def evaluate_window(feed, quotes):
    old, old_confirmed, latest, fresh = {}, [], {}, []
    evaluations, confirms, gates, denied = Counter(), Counter(), Counter(), Counter()
    contexts = {key:Counter() for key in ("1m","5m","15m","1h","4h")}
    watch = stale = None
    alert = dict(day=None, count=0, last_time=None, last_side=None)
    latest_minute = None
    ready = 0
    for at, price in quotes:
        state, reason = available_state(feed, at, price, DELAY_SECONDS)
        if state is None:
            denied[reason] += 1
            watch = None
            continue
        ready += 1
        minute = state["minute_bars"][-1]["start"]
        close = state["prices"][-1]
        if latest_minute != minute:
            latest_minute = minute
            for period, rows in ((60, state["minute_bars"]), (300, state["five_bars"])):
                for bar in rows:
                    bar["received_at"] = max(bar["updated"], bar["start"]+period+DELAY_SECONDS)
            snapshot = evaluate_snapshot(state, at)
            for key, count in contexts.items():
                count[snapshot["timeframes"][key]["direction"]] += 1
            rows = _shadow_rows(snapshot, at, close, price)
            for row in rows:
                latest[_candidate_key(row)] = row
            if stale and stale["minute_start"] < minute:
                if at-stale["at"] <= 120:
                    fresh.append(dict(denied_at=stale["at"], observed_at=at, denied_stage=stale["stage"],
                        denied_quote=stale["quote"], quote=price, minute_start=minute,
                        status=snapshot["status"], candidates=len({r["lane"] for r in rows}),
                        passed_060=len({r["lane"] for r in rows if r["plan"]["status"] == "qualified_shadow"})))
                stale = None
        if watch:
            age = at-watch["at"]
            if age > monitor.ENTRY_CONFIRM_MAX_AGE_SECONDS:
                confirms["expired"] += 1
                watch = None
            elif age >= 30:
                diag = {}
                plan = monitor.confirm_watch(state["prices"], watch["plan"], age,
                    native_state=state, diagnostics=diag)
                confirms[diag.get("reason", "unknown")] += 1
                if diag.get("reason") == "price_deviation" and stale is None:
                    stale = dict(at=at, quote=price, minute_start=minute, stage="confirmation")
                if plan:
                    gate = monitor.alert_gate(at, plan["side"], alert, plan=plan)
                    gates[gate] += 1
                    if gate == "ok":
                        old_confirmed.append(dict(at=at, watch_at=watch["at"], minute_start=minute,
                            quote=price, lane=plan["strategy"], direction=plan["direction"],
                            trigger=plan.get("level") if plan["strategy"] == "breakout" else None,
                            plan=dict(entry=plan["price"], stop=plan["stop"], target=plan["target1"],
                                      net_rr=plan["rr1"], status="confirmed")))
                        monitor.record_alert(at, plan["side"], alert, plan=plan)
                    watch = None
                elif diag.get("reason") == monitor.BREAKOUT_CONFIRMATION_REJECT:
                    watch = None
        if watch is not None:
            continue
        diag = {}
        proposal = monitor.classify(state["prices"], native_state=state, diagnostics=diag)
        evaluations[diag["reason"]] += 1
        for item in diag.get("candidates", []):
            side, strategy = item["direction"], item["strategy"]
            level = None
            if strategy == "breakout":
                evidence = monitor.breakout_evidence(state["prices"], state, monitor.ema, adaptive=True)
                level = evidence["prior_high" if side == 1 else "prior_low"]
            row = dict(at=at, minute_start=minute, quote=price, lane=strategy,
                       direction=side, trigger=level, reason=item["reason"])
            if proposal and (proposal["direction"], proposal["strategy"]) == (side,strategy):
                row["plan"] = dict(entry=proposal["price"], stop=proposal["stop"],
                                   target=proposal["target1"], net_rr=proposal["rr1"])
            old.setdefault(_candidate_key(row), row)
        if diag["reason"] == "price_deviation" and stale is None:
            stale = dict(at=at, quote=price, minute_start=minute, stage="setup")
        if proposal:
            gate = monitor.alert_gate(at, proposal["side"], alert, plan=proposal)
            if gate == "ok":
                watch = dict(at=at, plan=proposal)
    return dict(quotes=len(quotes), comparable_quotes=ready, latest_minutes=sum(contexts["1m"].values()),
                existing_candidates=list(old.values()), existing_confirmed=old_confirmed,
                modified_plans=list(latest.values()), fresh_price_reviews=fresh,
                input_unavailable=dict(denied), existing_evaluations=dict(evaluations),
                existing_watch_confirmation=dict(confirms), existing_confirmation_gates=dict(gates),
                trend_coverage={key:dict(value) for key,value in contexts.items()})


def _simulate(row, bars, cap=None, rr=None, *, target=None):
    plan = row["plan"]
    signal = ReplaySignal(row["at"], row["direction"], plan["stop"], plan["target"] if target is None else target,
                          row["lane"], plan.get("status", ""), trigger=row.get("trigger"))
    outcome = replay_one(signal, bars, max_bars=HOLD_BARS, min_net_rr=rr, max_stop_fraction=cap,
                         max_chase_fraction=MAX_CHASE if row.get("basis") else None, **COSTS)
    if outcome["classification"] in TERMINAL:
        outcome["pnl_r"] = outcome["net_usdt_per_btc"]/outcome["geometry"]["net_loss_per_btc"]
    return outcome


def _portfolio(rows, bars, cap, rr, rank=False):
    # One pending or open trade at a time with the same ex-ante loss unit.
    rows = sorted(rows, key=lambda r: (r["at"], -r["plan"]["net_rr"] if rank else 0.))
    accepted, dropped = [], []
    busy_until = 0
    for row in rows:
        if row["at"] < busy_until:
            dropped.append(dict(row, outcome={"classification":"overlapping_position"}))
            continue
        outcome = _simulate(row, bars, cap, rr)
        event = dict(row, outcome=outcome)
        if outcome["classification"] in TERMINAL:
            busy_until = outcome["exit_bar_start"]+60
            accepted.append(event)
        elif (outcome["classification"] in ("unresolved", "data_gap_or_invalid_candle")
              and outcome.get("entry_at") is not None):
            busy_until = outcome["entry_at"]+HOLD_BARS*60
            accepted.append(event)
        else:
            dropped.append(event)
    return dict(accepted=accepted, excluded=dropped)


def summarize(events):
    resolved = [e for e in events if e["outcome"]["classification"] in TERMINAL]
    pnl = [e["outcome"]["pnl_r"] for e in resolved]
    profit, losses = sum(x for x in pnl if x > 0), -sum(x for x in pnl if x < 0)
    balance = high = drawdown = 0.
    for value in pnl:
        balance += value
        high = max(high, balance)
        drawdown = max(drawdown, high-balance)
    return dict(accepted=len(events), resolved=len(resolved),
        target_first=sum(e["outcome"]["classification"] == "target_first" for e in resolved),
        stop_first=sum(e["outcome"]["classification"] == "stop_first" for e in resolved),
        unresolved=len(events)-len(resolved), win_rate=(sum(x>0 for x in pnl)/len(pnl) if pnl else None),
        net_r=sum(pnl) if pnl else None, profit_factor=profit/losses if losses else None,
        max_closed_trade_drawdown_r=drawdown if pnl else None)


def measure(record, bars):
    research = record["modified_plans"]
    existing = record["existing_confirmed"]
    record["policies"] = {
        "existing_at_native_threshold": _portfolio(existing, bars, MAIN_STOP_CAP, None),
        "existing_common_2r": _portfolio(existing, bars, MAIN_STOP_CAP, MIN_NET_RR),
    }
    for name, cap, status in (("modified_060", MAIN_STOP_CAP, "status"),
                              ("modified_080", STUDY_STOP_CAP, "cap_080_status")):
        eligible = [r for r in research if r["plan"][status] == "qualified_shadow"]
        record["policies"][name] = _portfolio(eligible, bars, cap, MIN_NET_RR, rank=True)
    for result in record["policies"].values():
        result["metrics"] = summarize(result["accepted"])
    exact_same_breaks = []
    for row in research:
        if row["basis"] != "1m" or row["signal_basis"] != "first_closed_minute_break":
            continue
        matches = [b for b in record["existing_candidates"] if b["lane"] == "breakout"
                   and b["direction"] == row["direction"] and b["trigger"] == row["trigger"]
                   and abs(b["at"]-row["at"]) <= 300]
        if matches:
            first = min(matches, key=lambda b:b["at"])
            exact_same_breaks.append(dict(old_at=first["at"], new_at=row["at"],
                level=row["trigger"], lane=row["lane"], lead_seconds=first["at"]-row["at"]))
    record["paired_breaks"] = exact_same_breaks
    record["lead_seconds_mean"] = mean(r["lead_seconds"] for r in exact_same_breaks) if exact_same_breaks else None
    # Probe the primary 1m stop on rejected observations, without pretending
    # they were approved or assuming a target absent at decision time.
    for row in research:
        plan = row["plan"]
        if row["basis"] == "1m" and plan.get("target") and plan.get("stop"):
            row["counterfactual"] = _simulate(row, bars)
            if plan.get("far_target") is not None:
                far_exit = plan["far_target"]*(1-row["direction"]*PIVOT_BUFFER)
                row["extension_replay"] = _simulate(row, bars, target=far_exit)
    record["missed_by_existing"] = [
        r for r in record["policies"]["modified_060"]["accepted"]
        if r["outcome"]["classification"] == "target_first"
        and r["outcome"]["pnl_r"] > 0
        and not any(b["direction"] == r["direction"] and abs(b["at"]-r["at"]) <= 300
                    for b in existing)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    logging.disable(logging.CRITICAL)
    bundles, source = load_archives(DATA/"archive_chunks.json")
    quote_rows = json.loads((DATA/"recorded_quotes.json").read_text())
    quotes = sorted({(timestamp(row["logged_at"]), row["price"]) for row in quote_rows})
    results = []
    with tempfile.TemporaryDirectory() as work, patch.object(monitor, "ENTRY_REFINEMENT_MODE", BASELINE_MODE):
        feed = make_feed(work, source)
        for index, (start, end) in enumerate(_archive_windows(bundles)):
            selected = [(at, p) for at,p in quotes if start <= at < end+60]
            bars = [source[60,t] for t in range(start,end+1,60) if (60,t) in source]
            record = evaluate_window(feed, selected)
            measure(record, bars)
            results.append(dict(record, start=start, end=end, minutes=len(bars),
                                sample="diagnostic" if index == 0 else "time_holdout"))
    report = dict(windows=results, baseline_commit=BASELINE_COMMIT, baseline_mode=BASELINE_MODE,
        baseline_source_rr=monitor.MIN_NET_RR, common_rr=MIN_NET_RR, stop_caps=[MAIN_STOP_CAP,STUDY_STOP_CAP],
        nominal_visibility_delay_seconds=DELAY_SECONDS, costs=COSTS, horizon_minutes=HOLD_BARS,
        fills="modeled", actual_receipts=False, independent_venue_5m=False,
        actual_account_data=False)
    result = json.dumps(report, ensure_ascii=False, allow_nan=False, indent=2)+"\n"
    if args.output:
        args.output.write_text(result)
    else:
        print(result, end="")


if __name__ == "__main__":
    main()
