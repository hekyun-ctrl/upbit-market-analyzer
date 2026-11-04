"""Bounded before/after comparison on the already-used development archive.

Forward price paths describe observations, not fills or profitable trades.
The baseline remains an immutable saved report; never fit rules to this output.
"""
import argparse
import hashlib
import json
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path

from research.zerobase.video_entry import HORIZON, read_retained_archive, replay

KST = timezone(timedelta(hours=9))


def kst(timestamp):
    return datetime.fromtimestamp(timestamp, KST).isoformat()


def forward_path(event, bars):
    index = next(i for i, b in enumerate(bars) if b["start"] == event["signal_start"])
    later, expected = [], event["signal_start"]+300
    for b in bars[index+1:index+1+HORIZON]:
        if b["start"] != expected:
            break
        later.append(b)
        expected += 300
    price, d = event["signal_close"], event["direction"]
    return dict(
        kind="price_observation_not_trade_pnl", continuous_followup_bars=len(later),
        directional_close_change_pct={str(n): d*(later[n-1]["close"]/price-1)*100
                                      if len(later) >= n else None for n in (1, 3, 6, 12)},
        favorable_excursion_pct=max([0.] + [d*(b["high" if d == 1 else "low"]/price-1)*100
                                           for b in later]) if later else None,
        adverse_excursion_pct=max([0.] + [-d*(b["low" if d == 1 else "high"]/price-1)*100
                                         for b in later]) if later else None)


def compare(archive, baseline):
    digest = hashlib.sha256(archive.read_bytes()).hexdigest()
    old = json.loads(baseline.read_text())
    if old["input_sha256"] != digest:
        raise ValueError("baseline uses different input; cannot compare")
    bars = read_retained_archive(archive)
    new = replay(bars, nominal=True)
    trend = lambda r: [e for e in r["events"] if e["lane"] == "bb_cci_breakout"]
    if trend(old) != trend(new):
        raise ValueError("unexpected trend-lane change")
    by_key = lambda r: {(e["direction"], e["signal_start"]): e for e in r["events"]
                       if e["lane"] == "rsi_reversal"}
    before, after = by_key(old), by_key(new)
    rows = []
    for key in sorted(before.keys() | after.keys(), key=lambda x: (x[1], x[0])):
        a, b = before.get(key), after.get(key)
        event = b or a
        rows.append(dict(change="retained" if a and b else "added" if b else "removed",
                         signal_close_kst=kst(event["signal_start"]+300),
                         direction=event["direction"], before=a, after=b,
                         forward_path=forward_path(event, bars)))
    anchors = []
    for key in sorted({(e["direction"], e["first_start"]) for e in before.values()}
                      & {(e["direction"], e["first_start"]) for e in after.values()}):
        a = sorted(e["signal_start"] for e in before.values()
                   if (e["direction"], e["first_start"]) == key)
        b = sorted(e["signal_start"] for e in after.values()
                   if (e["direction"], e["first_start"]) == key)
        anchors.append(dict(direction=key[0], first_start=key[1],
                            before_candidates=a, after_candidates=b,
                            earliest_candidate_change_minutes=(b[0]-a[0])/60,
                            interpretation="same_extreme_timestamp_not_confirmed_trade_match"))
    return dict(
        policy=new["policy"], baseline_revision="d13a6eefb7ee479444b1822b71acf6042572c7ea",
        input_sha256=digest, baseline_report_sha256=hashlib.sha256(baseline.read_bytes()).hexdigest(),
        source_quality="retained_lbank_derived_5m_not_independently_verified",
        sample_role="previously_used_development_not_out_of_sample",
        receipt_mode="nominal_if_missing", bars=new["bars"],
        evaluated_bars=new["evaluated_bars"], contiguous_runs=new["contiguous_runs"],
        costs=new["assumptions"], trend_lane_identical=True,
        before_candidate_counts=old["candidate_counts"], after_candidate_counts=new["candidate_counts"],
        before_plan_counts=old["plan_counts"], after_plan_counts=new["plan_counts"],
        rsi_signal_changes=dict(Counter(r["change"] for r in rows)),
        same_extreme_timing=anchors, rsi_events=rows,
        after_stage_counts=new["pre_candidate_rejections"],
        actual_profitability_verified=False, screenshot_candles_available=False,
        qualified_trade_accuracy=None, net_profit_improvement=None,
        limitations=["No qualified trade in either policy; no win-rate or net-profit conclusion.",
                     "Forward excursions use future OHLC only for evaluation, never entry decisions.",
                     "Neither actual spread/fills/funding nor the attached screenshot's exact candles are present."])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[2]
    result = compare(root / "research/zerobase/entry_comparison_20261024/archive_chunks.json",
                     root / "research/zerobase/video_entry_replay_20261104.json")
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False)+"\n")


if __name__ == "__main__":
    main()
