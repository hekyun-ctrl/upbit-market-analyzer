"""Bounded, read-only incident reconstruction; no Telegram, orders or gate writes.

Historical candle publication timestamps were not retained. Reconstructed states
at recorded quote times are NOT an assertion of first real-time availability.
The compressed archive contains public candles/quotes and selected signal-model
fields only, never credentials or user-reported positions.
"""
import base64
import hashlib
import json
import logging
import os
import sqlite3
import tempfile
import time
import zlib
from pathlib import Path

from lbank_native_entry import NativeEntryFeed

LOG = logging.getLogger("lbank_entry_replay")
VERSION = "recorded_quote_native_entry_replay_v1"
BAR_COLUMNS = "period,start,open,high,low,close,volume,turnover,updated,quality"


class ReplayLogMarker(logging.Filter):
    def filter(self, record):
        if record.threadName == "lbank-entry-replay-audit":
            record.msg = "[READ_ONLY_ENTRY_REPLAY] " + str(record.msg)
        return True


def readonly(path):
    db = sqlite3.connect(Path(path).absolute().as_uri()+"?mode=ro", uri=True, timeout=1)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA query_only=ON")
    return db


def capture(performance_path, native_path, now):
    """Snapshot the latest delivered model signal; never create either source DB."""
    db = readonly(performance_path)
    try:
        db.execute("BEGIN")
        row = db.execute("SELECT id,opened,plan FROM signals WHERE policy='levels' "
                         "AND opened<=? ORDER BY opened DESC LIMIT 1", (now,)).fetchone()
        if row is None or now-row["opened"] > 86400:
            return None
        opened = row["opened"]
        plan = json.loads(row["plan"])
        # Only fields needed to reconstruct this public alert, not account data.
        signal = dict(id=row["id"], opened=opened,
                      plan={k: plan.get(k) for k in ("price", "direction", "strategy", "stop",
                          "target1", "target2", "entry_quality", "entry_timing", "data_source")})
        samples = [list(r) for r in db.execute(
            "SELECT ts,price FROM samples WHERE ts>=? AND ts<=? ORDER BY ts LIMIT 120",
            (opened-900, opened))]
        decisions = [dict(ts=r["ts"], detail=json.loads(r["detail"])) for r in db.execute(
            "SELECT ts,detail FROM decisions WHERE ts>=? AND ts<=? ORDER BY ts LIMIT 120",
            (opened-900, opened))]
    finally:
        db.close()
    lower = samples[0][0] if samples else opened-900
    native = readonly(native_path)
    try:
        native.execute("BEGIN")
        rows = [list(r) for r in native.execute(
            f"SELECT {BAR_COLUMNS} FROM native_bars WHERE "
            "(period=60 AND start>=? AND start<?) OR "
            "(period=300 AND start>=? AND start<?) ORDER BY period,start",
            (int(lower//60)*60-360*60, int(opened//60)*60,
             int(lower//300)*300-250*300, int(opened//300)*300))]
    finally:
        native.close()
    if len(rows) > 800:
        raise ValueError("incident snapshot exceeded bounded candle count")
    return dict(version=VERSION, captured_at=now, signal=signal, samples=samples,
                decisions=decisions, bar_columns=BAR_COLUMNS.split(','), native_rows=rows,
                actual_account_data=False, publication_timestamps_recorded=False)


def replay(bundle, classify, confirm_watch):
    signal = bundle["signal"]; original = signal["plan"]
    points = []
    watch_at = None; watch = None; actual_confirmation = None
    original_watch_price = (original.get("entry_quality") or {}).get("watch_price")
    decision_times = {r["ts"] for r in bundle["decisions"] if r["detail"].get("reason") == "qualified"}
    with tempfile.TemporaryDirectory(prefix="lbank-entry-replay-") as directory:
        path = os.path.join(directory, "native.sqlite3")
        db = sqlite3.connect(path)
        try:
            db.execute("CREATE TABLE native_bars(period INTEGER,start INTEGER,open REAL,high REAL,"
                       "low REAL,close REAL,volume REAL,turnover REAL,updated INTEGER,quality TEXT)")
            db.executemany("INSERT INTO native_bars VALUES(?,?,?,?,?,?,?,?,?,?)", bundle["native_rows"])
            db.commit()
        finally:
            db.close()
        feed = NativeEntryFeed(path)
        for ts, price in bundle["samples"]:
            state, native_diag = feed.snapshot(ts, price)
            diagnostic = {}
            proposal = classify(state["prices"], native_state=state, diagnostics=diagnostic) if state else None
            matches = (proposal is not None and proposal["direction"] == original["direction"]
                       and proposal.get("strategy") == original["strategy"])
            point = dict(at=ts, quote=price, native_ready=state is not None,
                         reason=diagnostic.get("reason", native_diag.get("reason")), same_family_qualified=matches)
            if proposal is not None:
                point.update(direction=proposal["direction"], strategy=proposal.get("strategy"))
            if (watch is None and matches and ts in decision_times and original_watch_price is not None
                    and abs(price-original_watch_price) < 1e-6 and 0 <= signal["opened"]-ts <= 240
                    and proposal.get("native_watch_1m_start") ==
                    (original.get("entry_quality") or {}).get("watch_last_closed_1m")):
                watch_at, watch = ts, proposal
            points.append(point)
        # The exact alert quote is independently retained even if its sample write failed.
        state, _ = feed.snapshot(signal["opened"], original["price"])
        if watch is not None and state is not None:
            diag = {}
            confirmed = confirm_watch(state["prices"], watch, signal["opened"]-watch_at,
                                      native_state=state, diagnostics=diag)
            actual_confirmation = dict(watch_at=watch_at, watch_price=watch["price"],
                age_seconds=signal["opened"]-watch_at, reason=diag.get("reason"),
                accepted=confirmed is not None)
            if confirmed is not None:
                actual_confirmation.update(recomputed_price=confirmed["price"],
                    price_matches=abs(confirmed["price"]-original["price"]) < 1e-6,
                    stop_matches=abs(confirmed["stop"]-original["stop"]) < 1e-6,
                    target1_matches=abs(confirmed["target1"]-original["target1"]) < 1e-6,
                    entry_quality=confirmed.get("entry_quality"))
    first = next((p for p in points if p["same_family_qualified"]), None)
    return dict(version=VERSION, signal_id=signal["id"], actual_signal_at=signal["opened"],
                actual_quote=original["price"], first_qualified_recorded_quote=first,
                actual_confirmation_replay=actual_confirmation, points=points,
                complete_tick_replay=False, first_live_qualification_known=False,
                source_publication_times_missing=True, alert_cooldown_replayed=False,
                pnl_computed=False, actual_fills_verified=False,
                interpretation="conditions at recorded quotes only; not an earlier-send or profit claim")


def audit_recent(classify, confirm_watch, model_config):
    """Run once in an independent daemon; a diagnostic failure never gates entries."""
    marker = ReplayLogMarker()
    monitor_log = logging.getLogger("lbank_monitor")
    monitor_log.addFilter(marker)
    try:
        bundle = capture(os.getenv("LBANK_PERFORMANCE_DB", "/data/lbank_performance.sqlite3"),
                         os.getenv("LBANK_NATIVE_CANDLE_DB", "/data/lbank_native_candles.sqlite3"), time.time())
        if bundle is None:
            LOG.info("ENTRY_REPLAY_SKIPPED reason=no_recent_delivered_signal")
            return
        bundle["model_config"] = model_config
        bundle["runtime_source_commit"] = os.getenv("RAILWAY_GIT_COMMIT_SHA")
        bundle["result"] = replay(bundle, classify, confirm_watch)
        raw = json.dumps(bundle, sort_keys=True, ensure_ascii=False, allow_nan=False).encode()
        digest = hashlib.sha256(raw).hexdigest()
        encoded = base64.b64encode(zlib.compress(raw)).decode("ascii")
        chunks = [encoded[i:i+2400] for i in range(0, len(encoded), 2400)]
        LOG.info("ENTRY_REPLAY_RESULT %s", json.dumps(dict(
            archive_sha256=digest, signal_id=bundle["signal"]["id"],
            first_qualified_recorded_quote=bundle["result"]["first_qualified_recorded_quote"],
            actual_confirmation_replay=bundle["result"]["actual_confirmation_replay"],
            first_live_qualification_known=False, pnl_computed=False), sort_keys=True))
        for index, payload in enumerate(chunks):
            LOG.info("ENTRY_REPLAY_ARCHIVE %s", json.dumps(dict(sha256=digest,
                codec="zlib+base64", index=index, chunks=len(chunks), payload=payload), sort_keys=True))
    except Exception as exc:
        LOG.warning("ENTRY_REPLAY_FAILED exception=%s live_gates_unchanged=true", type(exc).__name__)
    finally:
        monitor_log.removeFilter(marker)
