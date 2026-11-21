"""Reproducible chronological smoke comparison on exported Railway price logs.

Not native OHLCV and NOT an out-of-sample profitability certification. Baseline
is pinned to the deployed pre-change commit; no thresholds are optimized here.
Run from repo root: python research/replay_lbank.py INPUT_JSON OUTPUT_JSON
"""
import collections
import datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import subprocess
import sys
import types

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
os.environ["LBANK_REFERENCE_BALANCE_USDT"] = "10000"
os.environ["LBANK_REFERENCE_LEVERAGE"] = "30"
os.environ["LBANK_RISK_PER_TRADE_PCT"] = "0.5"
os.environ["LBANK_MAX_MARGIN_PCT"] = "15"
import lbank_futures_monitor as current
from lbank_performance import PerformanceBook, day_key
from lbank_signal_risk import SignalRiskTracker

BASELINE = "9e0617a6fe366a17d9f802cfc7f1d698532d0f88"


def replay(module, samples):
    obs = collections.deque(maxlen=360)
    state = {"day": None, "count": 0, "last_time": None, "last_side": None}
    watch = None
    book = PerformanceBook(":memory:", 10000)
    tracker = SignalRiskTracker()
    sent, reasons = [], collections.Counter()
    for ts, price in samples:
        book.observe(ts, price)
        recent_prices = [p for _, p in obs][-5:] + [price]
        for signal, reason, exit_price in tracker.check(recent_prices, ts):
            if reason == "early_invalidation":
                book.early_warning(signal.number, signal.sent_at, ts, exit_price)
            tracker.acknowledge(signal, reason)
        obs.append((ts, price))
        prices = [p for _, p in obs]
        recent = list(obs)[-module.MIN_SAMPLES:]
        continuous = len(recent) >= module.MIN_SAMPLES and all(
            20 <= recent[i][0]-recent[i-1][0] <= max(module.POLL*2.5, 150)
            for i in range(1, len(recent)))
        if not continuous:
            watch = None
            reasons["data_gap_or_warmup"] += 1
            continue
        if watch is not None:
            age = ts-watch["time"]
            if age > module.ENTRY_CONFIRM_MAX_AGE_SECONDS:
                watch = None
            else:
                p = module.confirm_watch(prices, watch["plan"], age, observations=list(obs))
                if p is not None:
                    gate = module.alert_gate(ts, p["side"], state, plan=p)
                    if gate == "ok":
                        module.record_alert(ts, p["side"], state, plan=p)
                        signal = tracker.add_sent_signal(p, ts)
                        book.add(signal.number, p, ts)
                        sent.append({"at": ts, "kst_day": day_key(ts), "strategy": p["strategy"],
                                     "direction": p["direction"], "entry": p["price"],
                                     "stop": p["stop"], "target1": p["target1"], "target2": p["target2"]})
                    else:
                        reasons[gate] += 1
                    watch = None
        if watch is None:
            diag = {}
            p = module.classify(prices, observations=list(obs), diagnostics=diag)
            if p is not None:
                gate = module.alert_gate(ts, p["side"], state, plan=p)
                if gate == "ok":
                    watch = {"plan": p, "time": ts}
                else:
                    reasons[gate] += 1
            else:
                reasons[diag.get("reason", "unknown")] += 1
    days = sorted({day_key(t) for t, _ in samples})
    results = {day: book.summary(day) for day in days}
    return {"signals": sent, "total": book.summary(), "daily_signal_cohorts": results,
            "evaluations": dict(reasons), "independent_holdout": False,
            "normalized_reference_balance": 10000, "portfolio_compounding": False}


def main():
    logging.disable(logging.CRITICAL)
    raw = Path(sys.argv[1]).read_bytes()
    logs = json.loads(raw)
    pairs = {}
    for log in logs:
        if isinstance(log, list) and len(log) == 2:
            pairs[float(log[0])] = float(log[1])
            continue
        match = re.search(r"contract=BTCUSDT price=([0-9.]+)", log["message"])
        if match:
            ts = datetime.datetime.fromisoformat(log["timestamp"].replace("Z", "+00:00")).timestamp()
            pairs[ts] = float(match[1])
    samples = sorted(pairs.items())
    baseline = types.ModuleType("baseline_monitor")
    code = subprocess.check_output(["git", "show", BASELINE+":lbank_futures_monitor.py"], text=True)
    exec(compile(code, "baseline_monitor.py", "exec"), baseline.__dict__)
    output = {"baseline_commit": BASELINE, "input_sha256": hashlib.sha256(raw).hexdigest(),
              "sample_count": len(samples), "start_epoch": samples[0][0], "end_epoch": samples[-1][0],
              "limitations": ["ticker log snapshots, not candles or trades",
                  "quote entry/target fills hypothetical; actual fills unknown",
                  "taker 0.06% per leg, exit slippage 0.02%, funding reserve 0.01%",
                  "sample gaps >150s censored; intragap excursions unobservable",
                  "TP1 half, remaining stop at entry; TP2 remaining half",
                  "baseline pins entry logic only; both use current paired exit accounting",
                  "two-hour timeout; independent reference trades, not an executable portfolio",
                  "short diagnostic sample selected after prior losses; no OOS claim"],
              "baseline": replay(baseline, samples), "candidate": replay(current, samples)}
    Path(sys.argv[2]).write_text(json.dumps(output, indent=2, ensure_ascii=False)+"\n")
    print(json.dumps({"samples":len(samples), "hours":(samples[-1][0]-samples[0][0])/3600,
                      "baseline":output["baseline"]["total"],
                      "candidate":output["candidate"]["total"]}, indent=2))


if __name__ == "__main__":
    main()
