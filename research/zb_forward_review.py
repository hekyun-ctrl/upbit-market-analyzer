"""Read-only prospective quote MODEL. No fills, notifications or unlocks.

Quotes are recorded after signal receipt, without impossible next-open fills.
The partial entry candle cannot prove touch ordering; ambiguous touches and
gaps are excluded from win rate and explicitly prevent readiness claims.
"""
from collections import Counter, defaultdict
from contextlib import closing
import json
from pathlib import Path
import sqlite3

from research.zerobase.video_entry import Costs, _validate, judge_entry
from research.zb_trade_review import load_native, model_summary

POLICY = "locked_quote_forward_v1"
HORIZON = 12


def quote_outcome(model, bars, costs):
    plan, entry, at = model["plan"], model["entry"], model["entered_at"]
    verdict, _ = judge_entry(plan, entry, costs)
    if verdict != "qualified":
        return {"status": "invalid_recorded_entry"}
    start = int(at//300)*300
    base = dict(entry_start=start, entry=entry, quote_at=at)
    d, stop, target = plan["direction"], plan["stop"], plan["target"]
    expected = start
    for b in bars:
        if b["start"] < start:
            continue
        if b["start"] != expected:
            return dict(base, status="data_gap", at=expected)
        stop_hit = b["low"] <= stop if d == 1 else b["high"] >= stop
        target_hit = b["high"] >= target if d == 1 else b["low"] <= target
        if b["start"] == start and at > start and (stop_hit or target_hit):
            return dict(base, status="entry_bar_order_unknown", at=start)
        # Subsequent opens precede intrabar extremes. A gapped stop fills at
        # the worse open; target uses the limit, without optimistic gap gains.
        open_stop = d*(b["open"]-stop) <= 0
        open_target = d*(b["open"]-target) >= 0
        if b["start"] > start and (open_stop or open_target):
            kind = "stop" if open_stop else "target"
            exit_price = b["open"] if open_stop else target
        elif stop_hit and target_hit:
            return dict(base, status="same_bar_unknown", at=b["start"])
        elif stop_hit or target_hit:
            kind = "stop" if stop_hit else "target"
            exit_price = stop if stop_hit else target
        else:
            expected += 300
            if expected >= start+HORIZON*300:
                return dict(base, status="horizon_unresolved", at=b["start"])
            continue
        return dict(base, status=kind, at=b["start"], exit=exit_price,
                    model_pnl_per_btc=costs.pnl(d, entry, exit_price, exit_kind=kind))
    return dict(base, status="awaiting_completed_bars")


def summarize_forward(records, bars):
    _validate(bars, nominal=False)
    cohorts = defaultdict(list)
    excluded = Counter()
    for record in records:
        e = record["evidence"]
        model = e.get("forward_model")
        if not model:
            excluded["no_forward_quote_record"] += 1
            continue
        book = e.get("book", {})
        costs = Costs(**e["costs"])
        expected_price = book.get("ask" if model["plan"]["direction"] == 1 else "bid")
        at = model["entered_at"]
        if (model.get("policy") != POLICY or model.get("actual_fill") is not False
                or model.get("delivery_authorized") is not False
                or book.get("status") != "usable" or book.get("source") not in ("rest","websocket")
                or expected_price != model["entry"] or book.get("received_at") != at
                or not record["bar_start"]+300 <= model["plan"]["detected_at"] <= at
                or not record["evaluated_at"] <= at <= min(record["evaluated_at"]+5,
                                                           record["bar_start"]+330)):
            excluded["invalid_quote_provenance"] += 1
            continue
        key = json.dumps([model["policy"],model["revision"],e["costs"]],sort_keys=True)
        cohorts[key].append(dict(status="qualified", signal_start=record["bar_start"],
            lane=model["plan"]["lane"], direction=model["plan"]["direction"],
            setup_id=model["plan"].get("setup_id"),
            plan=model["plan"], outcome=quote_outcome(model,bars,costs)))
    result=[]
    for key, events in sorted(cohorts.items()):
        summary=model_summary({"events":events})
        summary["qualification_note"]="Recorded executable-side quote model; no order/fill or depth verification"
        unresolved=Counter(e["outcome"]["status"] for e in events
                           if e["outcome"]["status"] not in ("stop","target"))
        blockers=["actual_fill_and_depth_slippage_unverified", "independent_review_required"]
        if summary["resolved_nonoverlap"] < 30:
            blockers.append("fewer_than_30_resolved_models")
        if summary["net_return_sum"] <= 0:
            blockers.append("nonpositive_model_net")
        if unresolved:
            blockers.append("unresolved_or_ambiguous_models")
        result.append(dict(cohort=json.loads(key),summary=summary,events=events,
                           unresolved_reasons=dict(unresolved),blockers=blockers))
    return dict(policy=POLICY,cohorts=result,excluded_records=dict(excluded),
                no_candidates=not result,actual_fills_verified=False,
                profitability_verified=False,unlock_authorized=False,
                note="30 trades is a review threshold, not statistical proof. Never pools revisions/cost policies.")


def forward_report(ledger_path, native_path):
    rows=[]
    ledger_path=Path(ledger_path)
    if ledger_path.is_file():
        with closing(sqlite3.connect(ledger_path.resolve().as_uri()+"?mode=ro",timeout=2,uri=True)) as db:
            db.row_factory=sqlite3.Row
            for row in db.execute("SELECT bar_start,evaluated_at,evidence FROM video_alert_decisions ORDER BY bar_start"):
                rows.append(dict(bar_start=row["bar_start"], evaluated_at=row["evaluated_at"],
                                 evidence=json.loads(row["evidence"])))
    return summarize_forward(rows,load_native(native_path))
