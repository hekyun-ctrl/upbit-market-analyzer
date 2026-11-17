"""Regression checks for completed 5m early-watch discovery only."""
from collections import deque
from datetime import datetime, timezone
import json
import sqlite3

from audit_store import AuditStore
from five_minute_watch import FiveMinuteWatch, proof


def _prior(market_start, price, high, value):
    return [
        dict(start=market_start - (20 - i) * 300, open=price,
             high=high, low=price * 0.999, close=price, value=value)
        for i in range(20)
    ]


def test_brev_first_5m_acceleration_is_observed_not_approved():
    start = 1794938400  # synthetic time anchor, no claim about real order fills
    past = _prior(start, 81.3, 81.8, 353_200_000 / 29)
    trigger = dict(start=start, open=81.3, high=83.5, low=81.2,
                   close=83.0, value=353_200_000)
    evidence = proof(past + [trigger], "KRW-BREV")
    assert evidence is not None
    assert evidence["five_minute_turnover_ratio"] == 29
    assert evidence["breakout_level"] == 81.8
    engine = FiveMinuteWatch()
    engine.bars["KRW-BREV"] = deque(past, maxlen=30)
    alert = engine.completed("KRW-BREV", trigger, start + 300)
    assert alert["signal"] == "leader_volume_acceleration"
    assert alert["internal_only"] and alert["completed_5m_watch"]
    assert not alert.get("entry_approved", False)
    assert engine.completed("KRW-BREV", trigger, start + 300) is None


def test_mmt_first_impulse_qualifies_without_waiting_for_later_spike():
    start = 1794938400
    past = _prior(start, 164.0, 164.0, 62_300_000 / 18.9)
    trigger = dict(start=start, open=164.0, high=171.0, low=163.8,
                   close=170.0, value=62_300_000)
    p = proof(past + [trigger], "KRW-MMT")
    assert p is not None
    assert 18.8 < p["five_minute_turnover_ratio"] < 19
    assert p["change_5m_pct"] > 3.6


def test_false_impulses_stay_silent():
    start = 1794938400
    past = _prior(start, 100, 101, 10_000_000)
    candidate = dict(start=start, open=100, high=104, low=100,
                     close=103, value=50_000_000)
    assert proof(past + [candidate], "KRW-X")
    for column, value in (("value", 9_000_000), ("close", 100.0),
                          ("high", 110.0), ("open", 104.0)):
        mutated = dict(candidate, **{column: value})
        assert proof(past + [mutated], "KRW-X") is None


def test_warm_does_not_replay_historical_alerts_and_reorders_rest_data():
    anchor = 1794938400
    candles = []
    for i in range(125):
        start = anchor - (125-i)*60
        candles.append(dict(candle_date_time_utc=datetime.fromtimestamp(
            start, timezone.utc).isoformat(), opening_price=100.0,
            high_price=101.0, low_price=99.0, trade_price=100.0,
            candle_acc_trade_price=2_000_000.0))
    watcher = FiveMinuteWatch()
    watcher.warm("KRW-X", list(reversed(candles)), anchor)
    assert len(watcher.bars["KRW-X"]) >= 20
    assert not watcher.live and not watcher.last_seq
    assert watcher.bars["KRW-X"][-1]["start"] < anchor
    assert watcher.bars["KRW-X"][-1]["value"] == 10_000_000


def test_duplicate_tick_does_not_inflate_live_turnover():
    engine = FiveMinuteWatch()
    now = 1794938400
    engine.update("KRW-X", 100, 10, now * 1000, 12, now=now)
    engine.update("KRW-X", 100, 1000, now * 1000, 12, now=now)
    assert engine.live["KRW-X"]["value"] == 1000


def test_audit_payload_compression_and_legacy_rows(tmp_path):
    store = AuditStore(str(tmp_path / "audit.db"))
    row = {"market": "KRW-BREV", "data": "repeated-data-" * 300}
    store.append("screening_records", row)
    store.append("screening_records", row)
    stored = store.db.execute("SELECT payload FROM events").fetchone()[0]
    assert stored.startswith("z1:")
    assert store.read("screening_records") == [row]
    legacy = {"market": "KRW-MMT", "legacy": True}
    with store.db:
        store.db.execute("INSERT INTO events (kind,key,payload,stored_at) VALUES (?,?,?,?)",
                         ("screening_records", "legacy-key",
                          json.dumps(legacy), datetime.now(timezone.utc).isoformat()))
    assert store.read("screening_records") == [legacy, row]
