"""Pure 5m entry rules and offline replay. No network or order hooks."""
from __future__ import annotations

import argparse
import base64
import hashlib
import json
import math
import zlib
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from statistics import mean, median, pstdev

from research.zerobase.rsi_sequence import POLICY, sequence_series
from lbank_native_entry import wilder_rsi_series
from lbank_structural_target_plan import available_barriers, entry_bound, rounded

BAR_SECONDS = 300
WARMUP = 40
HORIZON = 12
SOURCES = ("lxz7Csyg9bk", "tQadAOEVbFA")


@dataclass(frozen=True)
class Costs:
    fee: float = .0006
    stop_slippage: float = .0002
    funding_reserve: float = .0001
    min_rr: float = 1.5
    max_stop_fraction: float = .006
    tick: float = .1

    def __post_init__(self):
        if (not all(math.isfinite(x) for x in asdict(self).values())
                or not all(0 <= x < 1 for x in (self.fee, self.stop_slippage,
                                                self.funding_reserve))
                or not 0 < self.max_stop_fraction < 1
                or self.min_rr <= 0 or self.tick <= 0):
            raise ValueError("invalid cost assumptions")

    def pnl(self, direction, entry, exit_price, *, exit_kind="stop"):
        execution = exit_price * (1 - direction * self.stop_slippage
                                  if exit_kind == "stop" else 1)
        return (direction * (execution - entry) - self.fee * (entry + execution)
                - self.funding_reserve * entry)


def _validate(bars, nominal):
    """Validate every row; gaps become boundaries, never synthetic candles."""
    ready = []
    for i, b in enumerate(bars):
        start = b["start"]
        if (type(start) is not int or start <= 0 or start % BAR_SECONDS
                or (i and start <= bars[i - 1]["start"])
                or b["quality"] != "stream_closed"):
            raise ValueError("expected ordered unique completed 5-minute bars")
        if "period" in b and b["period"] != BAR_SECONDS:
            raise ValueError("expected 5-minute bars")
        o, h, l, c, v = (b[k] for k in ("open", "high", "low", "close", "volume"))
        if (not all(type(x) in (int, float) and math.isfinite(x) for x in (o, h, l, c, v))
                or l <= 0 or not l <= min(o, c) <= max(o, c) <= h or v < 0):
            raise ValueError("invalid OHLCV")
        end = start + BAR_SECONDS
        at = b.get("received_at")
        if at is None and nominal:
            at = end
        if type(at) not in (int, float) or not math.isfinite(at) or at < end:
            raise ValueError("missing or invalid final receipt; use --nominal for archives")
        if ready and at < ready[-1]:
            raise ValueError("out-of-order receipt sequence")
        ready.append(at)
    return ready


def _series(bars):
    closes = [b["close"] for b in bars]
    rsis = [None] * min(14, len(bars)) + wilder_rsi_series(closes)
    tp = [(b["high"] + b["low"] + b["close"]) / 3 for b in bars]
    tr = [max(b["high"] - b["low"],
              abs(b["high"] - closes[max(i - 1, 0)]),
              abs(b["low"] - closes[max(i - 1, 0)]))
          for i, b in enumerate(bars)]
    indicators = []
    for i in range(len(bars)):
        if i < 19:
            indicators.append(None)
            continue
        window = closes[i-19:i+1]
        center, spread = mean(window), 2 * pstdev(window)
        typical = tp[i-19:i+1]
        tm = mean(typical)
        dev = mean(abs(v - tm) for v in typical)
        indicators.append(dict(rsi=rsis[i], center=center, lower=center-spread,
                               upper=center+spread, width=2*spread/center,
                               atr=mean(tr[i-13:i+1]),
                               cci=(tp[i]-tm)/(.015*dev) if dev else 0.))
    return indicators



def _trend(bars, series, i, d):
    cur, prev, bb = bars[i], bars[i-1], series[i]
    key = "high" if d == 1 else "low"
    anchor = "low" if d == 1 else "high"
    breakout = d * max(d*b[key] for b in bars[i-6:i])
    if not d*(prev["close"]-breakout) <= 0 < d*(cur["close"]-breakout):
        return None, None
    volume_base = median(b["volume"] for b in bars[i-20:i])
    if volume_base <= 0:
        return None, "volume_baseline_missing"
    multiple = cur["volume"]/volume_base
    if multiple < 1.5:
        return None, "volume_below_threshold"
    if (bb["atr"] <= 0 or d*(bb["center"]-series[i-3]["center"]) < .15*bb["atr"]
            or d*(cur["close"]-bb["center"]) <= 0 or d*bb["cci"] <= 0):
        return None, "center_or_cci_not_aligned"
    if (bb["width"] <= series[i-1]["width"]
            and bb["width"] <= median(x["width"] for x in series[i-20:i])):
        return None, "narrow_band_not_expanding"
    return dict(lane="bb_cci_breakout", direction=d, trigger=breakout,
                anchor=d*min(d*b[anchor] for b in bars[i-3:i+1]),
                cci=bb["cci"], volume_multiple=multiple, retest_required=False), None


def judge_entry(plan, price, costs):
    d, stop, target = (plan[k] for k in ("direction", "stop", "target"))
    if d*(price-stop) <= 0 or d*(target-price) <= 0:
        return "outside_stop_target", None
    if plan["lane"] == "bb_cci_breakout" and d*(price-plan["trigger"]) <= 0:
        return "breakout_lost", None
    if abs(price-stop)/price > costs.max_stop_fraction:
        return "stop_too_far", None
    gain = costs.pnl(d, price, target, exit_kind="take_profit")
    loss = -costs.pnl(d, price, stop)
    rr = gain/loss if loss > 0 else None
    return ("qualified" if rr is not None and rr >= costs.min_rr else
            "insufficient_net_rr"), rr


def _plan(setup, bars, series, ready, i, costs, nominal):
    d, bb, price = setup["direction"], series[i], bars[i]["close"]
    # Only bars already present at the decision are passed into P1.
    history = bars[max(0, i-239):i+1]
    barriers = available_barriers(history, d, price, ready[i],
                                  nominal_delay=0 if nominal else None)
    levels = [(x["price"], "confirmed_pivot", x["available_at"]) for x in barriers]
    if setup["lane"] == "rsi_reversal":
        # A reclaimed center is not a failed reversal. If already behind price,
        # use the nearest still-unbreached structural barrier ahead. Never invent
        # a 3R target or ignore a closer barrier to make the economics pass.
        if d*(bb["center"]-price) > 0:
            levels.append((bb["center"], "signal_bar_bb_center", ready[i]))
    if not levels:
        return dict(setup, status="no_known_target")
    level, basis, known = min(levels, key=lambda x: d*x[0])
    stop = rounded(setup["anchor"]-d*max(costs.tick, .1*bb["atr"]), costs.tick, up=d == -1)
    target = rounded(level-d*max(costs.tick, price*.00015), costs.tick, up=d == -1)
    if min(stop, target) <= 0 or d*(target-stop) <= 0:
        return dict(setup, status="invalid_geometry")
    bound = entry_bound(d, stop, target, costs.pnl, costs.min_rr)
    if bound is not None:
        risk_bound = stop / (1-d*costs.max_stop_fraction)
        bound = d * min(d*bound, d*risk_bound)
    plan = dict(setup, stop=stop, target=target, target_basis=basis, barrier=level,
                barrier_available_at=known,
                entry_bound=rounded(bound, costs.tick, up=d == -1) if bound else None,
                bound_kind="max_long" if d == 1 else "min_short")
    status, rr = judge_entry(plan, price, costs)
    return dict(plan, status=status, net_rr_at_signal_close=rr)


def _outcome(plan, later, detected_at, costs):
    if plan["status"] != "qualified":
        return {"status": "no_model_entry"}
    if not later:
        return {"status": "no_followup"}
    opening = later[0]
    if opening["start"] < detected_at:
        return {"status": "open_precedes_receipt"}
    price = opening["open"]
    verdict, rr = judge_entry(plan, price, costs)
    if verdict != "qualified":
        return {"status": "next_open_" + verdict}
    d, stop, target = (plan[k] for k in ("direction", "stop", "target"))
    row = dict(entry=price, entry_start=opening["start"], entry_net_rr=rr)
    for b in later[:HORIZON]:
        # An opening gap is ordered before the rest of that candle.
        if d*(b["open"]-stop) <= 0:
            return dict(row, status="stop", at=b["start"],
                        model_pnl_per_btc=costs.pnl(d, price, b["open"]))
        if d*(b["open"]-target) >= 0:
            return dict(row, status="target", at=b["start"],
                        model_pnl_per_btc=costs.pnl(d, price, target, exit_kind="take_profit"))
        stop_hit = b["low"] <= stop if d == 1 else b["high"] >= stop
        target_hit = b["high"] >= target if d == 1 else b["low"] <= target
        if stop_hit and target_hit:
            return dict(row, status="same_bar_unknown", at=b["start"])
        if stop_hit or target_hit:
            return dict(row, status="stop" if stop_hit else "target", at=b["start"],
                        model_pnl_per_btc=costs.pnl(d, price, stop if stop_hit else target,
                                                   exit_kind="stop" if stop_hit else "take_profit"))
    return dict(row, status="horizon_unresolved" if len(later) >= HORIZON else "no_followup")


def _detect(bars, series, ready, i, costs, nominal, rejected, sequences):
    for d in (1, -1):
        state = sequences[d][i]
        reversal = state["event"]
        if reversal is None:
            rejected[f"rsi_{'long' if d == 1 else 'short'}_{state['stage']}"] += 1
        trend, reason = _trend(bars, series, i, d)
        if reason:
            rejected[reason] += 1
        for setup in (reversal, trend):
            if setup is not None:
                plan = _plan(setup, bars, series, ready, i, costs, nominal)
                yield dict(plan, signal_start=bars[i]["start"], detected_at=ready[i],
                           signal_close=bars[i]["close"])


def evaluate_latest(bars, *, costs=Costs()):
    """Decide on the newest completed bar using recorded receipts only."""
    ready = _validate(bars, nominal=False)
    if len(bars) < WARMUP:
        return [], {"warmup": 1}
    if any(b["start"] - a["start"] != BAR_SECONDS for a, b in zip(bars, bars[1:])):
        raise ValueError("latest-bar input must be contiguous")
    rejected = Counter()
    indicators = _series(bars)
    sequences = {d: sequence_series(bars, indicators, d) for d in (1, -1)}
    events = list(_detect(bars, indicators, ready, len(bars)-1, costs, False, rejected, sequences))
    return events, dict(rejected)


def replay(bars, *, nominal=False, costs=Costs(), with_outcomes=True):
    ready = _validate(bars, nominal)
    starts = [0] if bars else []
    starts.extend(i for i in range(1, len(bars))
                  if bars[i]["start"]-bars[i-1]["start"] != BAR_SECONDS)
    events, rejected, evaluated = [], Counter(), 0
    for a, b in zip(starts, starts[1:]+[len(bars)]):
        run, at = bars[a:b], ready[a:b]
        if len(run) < WARMUP:
            continue
        indicators = _series(run)
        sequences = {d: sequence_series(run, indicators, d) for d in (1, -1)}
        for i in range(WARMUP-1, len(run)):
            evaluated += 1
            for plan in _detect(run, indicators, at, i, costs, nominal, rejected, sequences):
                if with_outcomes:
                    plan["outcome"] = _outcome(plan, run[i+1:i+1+HORIZON], at[i], costs)
                events.append(plan)
    return dict(scope="offline_research_only", sources=SOURCES, policy=POLICY, production_ready=False,
                receipt_mode="nominal_if_missing" if nominal else "recorded",
                assumptions=asdict(costs), bars=len(bars), evaluated_bars=evaluated,
                contiguous_runs=len(starts),
                candidate_counts=dict(Counter(p["lane"] for p in events)),
                plan_counts=dict(Counter(p["status"] for p in events)),
                pre_candidate_rejections=dict(rejected),
                outcome_counts=dict(Counter(p["outcome"]["status"] for p in events
                                            if "outcome" in p)), events=events)


def read_retained_archive(path):
    """Read existing development windows, checking each archived payload hash."""
    groups, bars = {}, {}
    for item in json.loads(path.read_text()):
        part = item["archive"]
        group = groups.setdefault(part["sha256"], {})
        if group.setdefault(part["index"], part) != part:
            raise ValueError("conflicting archive chunk")
    for digest, group in groups.items():
        n = len(group)
        if set(group) != set(range(n)) or any(p["chunks"] != n for p in group.values()):
            raise ValueError("incomplete archive")
        raw = zlib.decompress(base64.b64decode("".join(group[i]["payload"] for i in range(n))))
        if hashlib.sha256(raw).hexdigest() != digest:
            raise ValueError("archive checksum mismatch")
        archive = json.loads(raw)
        for values in archive["native_rows"]:
            row = dict(zip(archive["bar_columns"], values, strict=True))
            if row["period"] == BAR_SECONDS and bars.setdefault(row["start"], row) != row:
                raise ValueError("conflicting retained bar")
    return [bars[t] for t in sorted(bars)]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("bars", type=Path, help="Ordered JSON list of completed 5m OHLCV bars")
    parser.add_argument("--nominal", action="store_true", help="Assume bar close for absent receipts")
    parser.add_argument("--retained-archive", action="store_true",
                        help="Read the repository's archived development samples")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    bars = (read_retained_archive(args.bars) if args.retained_archive
            else json.loads(args.bars.read_text()))
    report = replay(bars, nominal=args.nominal)
    report["input_sha256"] = hashlib.sha256(args.bars.read_bytes()).hexdigest()
    if args.retained_archive:
        report["source_quality"] = "retained_lbank_derived_5m_not_independently_verified"
    out = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)+"\n"
    if args.output:
        args.output.write_text(out, encoding="utf-8")
    else:
        print(out, end="")


if __name__ == "__main__":
    main()
