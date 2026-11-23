"""Paired public-data incident replay; no Telegram, orders or PnL claim.

Input is a decoded ENTRY_CONTEXT_WINDOW_ARCHIVE JSON checkpoint. Historical
first-arrival timestamps are required; missing timestamps are not invented.
Only retained quote observations are evaluated, so this is not an exact
reconstruction of the higher-frequency live scheduler or actual execution.
"""
import copy
import json
import logging
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import lbank_futures_monitor as m
from lbank_native_entry import NativeEntryFeed, native_context_gate
from lbank_sealed_breakout import arm_stopped_retry
from lbank_signal_thesis import observed_stop_touch


def run(window, corrected):
    config_keys = dict(fee="TAKER_FEE", slippage="STOP_SLIPPAGE", funding_buffer="FUNDING_BUFFER",
                       reference_balance="REF_BALANCE", risk_fraction="RISK_FRACTION",
                       leverage="LEVERAGE", max_margin_fraction="MAX_MARGIN_FRACTION")
    for key, variable in config_keys.items():
        if key in window.get("model_config", {}):
            setattr(m, variable, float(window["model_config"][key]))
    bars = window["native_rows"]
    columns = window["bar_columns"]
    arrivals = {(r[0], r[1]): r[2] for r in window["arrivals"]}
    result = dict(qualified=[], signals=[], unavailable=0, examined=0,
                  real_fills=False, profitability_verified=False)
    alert = dict(day=None, count=0, last_time=None, last_side=None)
    watch, active = None, None
    with tempfile.TemporaryDirectory() as directory:
        dbpath = directory+"/native.sqlite3"
        db = sqlite3.connect(dbpath)
        db.execute("CREATE TABLE native_bars(period INTEGER,start INTEGER,open REAL,high REAL,"
                   "low REAL,close REAL,volume REAL,turnover REAL,updated INTEGER,quality TEXT)")
        db.executemany("INSERT INTO native_bars VALUES(?,?,?,?,?,?,?,?,?,?)", bars)
        db.commit()
        db.close()
        feed = NativeEntryFeed(dbpath)
        def context(state, d, strategy, **kwargs):
            return native_context_gate(state, d, strategy,
                **(kwargs if corrected else {}))
        local_floor = m.local_stop_floor
        def stop_floor(state, legacy, **kwargs):
            return local_floor(state, legacy,
                timeframe_noise=corrected and kwargs.get("timeframe_noise", False))
        with patch.object(m, "ENTRY_REFINEMENT_MODE", "capture"), \
             patch.object(m, "native_context_gate", side_effect=context), \
             patch.object(m, "local_stop_floor", side_effect=stop_floor), \
             patch.object(m, "stopped_retry_allowed", wraps=m.stopped_retry_allowed) as retry:
            if not corrected:
                retry.return_value = False
                retry.side_effect = lambda *args, **kwargs: False
            for ts, price in window["samples"]:
                state, diag = feed.snapshot(ts, price)
                if state is None or any(
                        (period, b["start"]) not in arrivals
                        or arrivals[(period, b["start"])] > ts
                        for period, name in ((60, "minute_bars"), (300, "five_bars"))
                        for b in state[name]):
                    result["unavailable"] += 1
                    continue
                result["examined"] += 1
                if active:
                    p, sent = active
                    d = p["direction"]
                    touch = observed_stop_touch(state, d, p["stop"], sent, ts)
                    if d*(price-p["stop"]) <= 0 or touch:
                        if corrected:
                            arm_stopped_retry(alert, SimpleNamespace(
                                sent_at=sent, direction=d), "stop_breached", ts)
                        active = None
                if watch:
                    p, started = watch
                    if ts-started > m.ENTRY_CONFIRM_MAX_AGE_SECONDS:
                        watch = None
                    elif ts-started >= 30:
                        detail = {}
                        confirmed = m.confirm_watch(state["prices"], p, ts-started,
                            native_state=state, diagnostics=detail)
                        if confirmed and m.alert_gate(ts, confirmed["side"], alert, confirmed) == "ok":
                            result["signals"].append(dict(at=ts, price=confirmed["price"],
                                direction=confirmed["direction"], stop=confirmed["stop"],
                                target1=confirmed["target1"], target2=confirmed["target2"]))
                            m.record_alert(ts, confirmed["side"], alert, confirmed)
                            active, watch = (confirmed, ts), None
                        elif detail.get("reason") in ("price_deviation", m.BREAKOUT_CONFIRMATION_REJECT):
                            watch = None
                diag = {}
                plan = m.classify(state["prices"], native_state=state, diagnostics=diag)
                if plan:
                    result["qualified"].append(dict(at=ts, price=plan["price"],
                        direction=plan["direction"], route=plan.get("entry_route")))
                    if watch is None and m.alert_gate(ts, plan["side"], alert, plan) == "ok":
                        watch = (copy.deepcopy(plan), ts)
    result["initial_state"] = "empty cooldown; earlier unobserved alerts unknown"
    result["sampling"] = "retained quote observations and recorded first arrivals only"
    return result


if __name__ == "__main__":
    logging.disable(logging.CRITICAL)
    window = json.loads(Path(sys.argv[1]).read_text())
    print(json.dumps({"before": run(window, False), "after": run(window, True)},
                     ensure_ascii=False, indent=2))
