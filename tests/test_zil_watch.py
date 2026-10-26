import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from audit_store import AuditStore
from zil_watch import ZilWatcher, detect, end_at, message, run_zil_watch, STATUS, volume_reference

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp() + 3


@pytest.fixture(autouse=True)
def fixed_references(monkeypatch):
    for minutes in (1, 5):
        monkeypatch.setenv(f"ZIL_WATCH_REFERENCE_{minutes}M_VOLUME", "500000")
        monkeypatch.setenv(f"ZIL_WATCH_REFERENCE_{minutes}M_START", str(int(NOW - 3 - 86400)))


def row(start, volume=100000, value=1000000, close=3):
    return dict(candle_date_time_utc=datetime.fromtimestamp(start, timezone.utc).replace(tzinfo=None).isoformat(),
                candle_acc_trade_volume=volume, candle_acc_trade_price=value,
                opening_price=3, high_price=max(3, close), low_price=min(3, close), trade_price=close)


def bars(minutes=1, close=3.3):
    start = NOW - 3 - minutes * 60
    return [row(start - i * minutes * 60) for i in range(20, 0, -1)] + [row(start, 1000000, 40000000, close)]


@pytest.mark.parametrize("minutes", [1, 5])
@pytest.mark.parametrize("close", [2.7, 3, 3.3])
def test_both_directions_and_flat_volume_alert(minutes, close):
    hits = detect(bars(minutes, close), minutes, NOW)
    assert len(hits) == 1 and hits[0]["ratio"] == 2


def test_incomplete_future_old_and_below_fixed_volume_excluded():
    b = bars()
    assert not detect(b, 1, NOW - 5)
    assert not detect(b, 1, NOW + 180)
    b[-1]["candle_acc_trade_volume"] = 749999.99999999
    assert not detect(b, 1, NOW)


def test_rolling_median_and_sparse_history_do_not_lower_fixed_reference():
    b = bars()
    b[0] = row(NOW - 7200)
    assert detect(b, 1, NOW)[0]["reference_volume"] == "500000"
    for r in b[:-1]:
        r["candle_acc_trade_volume"] = 0
    assert detect(b, 1, NOW)[0]["ratio"] == 2
    b[-1]["candle_acc_trade_volume"] = 700000
    assert not detect(b, 1, NOW)


def test_prior_only_and_duplicate_rows():
    b = bars()
    assert detect(b + [b[-1], row(NOW + 60, 1e10, 1e10)], 1, NOW)[0]["ratio"] == 2


def make_watcher(tmp_path):
    audit = AuditStore(str(tmp_path / "audit.sqlite3"))
    client = AsyncMock()
    client.candles.side_effect = lambda market, interval, count: bars() if interval == "minute1" else bars(5) if interval == "minute5" else []
    client.ticker.return_value = {"trade_price": 3.3, "timestamp": NOW * 1000}
    client.orderbook.return_value = {"orderbook_units": []}
    dispatcher = AsyncMock()
    dispatcher.mode = "telegram"
    return ZilWatcher(dispatcher, audit, client), dispatcher


def test_persistent_dedup_and_combines_intervals(tmp_path):
    watch, d = make_watcher(tmp_path)
    assert asyncio.run(watch.tick(NOW))
    assert d._deliver_telegram.await_count == 1
    restored = ZilWatcher(d, AuditStore(str(tmp_path / "audit.sqlite3")), watch.client)
    asyncio.run(restored.tick(NOW + 60))
    assert d._deliver_telegram.await_count == 1
    assert len(watch.audit.get("zil_watch_sent")) == 2


def test_failure_retries_without_marking_sent(tmp_path):
    watch, d = make_watcher(tmp_path)
    d._deliver_telegram.side_effect = RuntimeError("test")
    with pytest.raises(RuntimeError):
        asyncio.run(watch.tick(NOW))
    assert watch.audit.get("zil_watch_sent") is None
    d._deliver_telegram.side_effect = None
    asyncio.run(watch.tick(NOW + 60))
    assert watch.audit.get("zil_watch_sent")


def test_expiry_and_stale_ticker_prevent_send(tmp_path):
    watch, d = make_watcher(tmp_path)
    assert not asyncio.run(watch.tick(end_at()))
    watch.client.candles.assert_not_called()
    watch.client.ticker.return_value["timestamp"] = (NOW - 500) * 1000
    with pytest.raises(ValueError):
        asyncio.run(watch.tick(NOW))
    d._deliver_telegram.assert_not_called()


def test_text_separates_observation_and_structure():
    text = message(detect(bars(), 1, NOW), {"trade_price": 3.3}, NOW)
    assert "매수 신호가 아닙니다" in text
    assert "고정 기준 1.5배 이상" in text and "500,000.00 ZIL" in text
    for old in ("상승 동반", "하락 동반", "방향 불분명", "기존 저점", "1시간 흐름"):
        assert old not in text


@pytest.mark.parametrize("minutes", [1, 5])
def test_threshold_is_inclusive_and_volume_only(minutes):
    b = bars(minutes)
    b[-1]["candle_acc_trade_volume"] = "750000.00000000"
    b[-1]["candle_acc_trade_price"] = 1
    hit = detect(b, minutes, NOW)[0]
    assert hit["ratio"] == 1.5 and hit["threshold"] == "750000.0"
    b[-1]["candle_acc_trade_volume"] = "749999.99999999"
    assert not detect(b, minutes, NOW)


@pytest.mark.parametrize("bad", ["0", "-1", "NaN", "Infinity", "invalid", ""])
def test_invalid_reference_blocks_without_legacy_fallback(monkeypatch, bad):
    monkeypatch.setenv("ZIL_WATCH_ENABLED", "true")
    monkeypatch.setenv("ZIL_WATCH_REFERENCE_1M_VOLUME", bad)
    dispatcher = AsyncMock()
    asyncio.run(run_zil_watch(dispatcher, object()))
    assert STATUS["state"] == "blocked_invalid_volume_reference"
    dispatcher._deliver_telegram.assert_not_called()


def test_missing_reference_and_future_reference_fail_closed(monkeypatch):
    monkeypatch.delenv("ZIL_WATCH_REFERENCE_1M_VOLUME")
    with pytest.raises(ValueError):
        volume_reference(1)
    monkeypatch.setenv("ZIL_WATCH_REFERENCE_1M_VOLUME", "500000")
    monkeypatch.setenv("ZIL_WATCH_REFERENCE_1M_START", str(int(NOW - 3)))
    assert not detect(bars(), 1, NOW)


def test_disabled_watch_never_fetches_or_sends(monkeypatch):
    monkeypatch.setenv("ZIL_WATCH_ENABLED", "false")
    dispatcher = AsyncMock()
    asyncio.run(run_zil_watch(dispatcher, object()))
    assert STATUS["state"] == "disabled"
    dispatcher._deliver_telegram.assert_not_called()


def test_restart_keeps_reference_and_stricter_interval_separate(tmp_path, monkeypatch):
    monkeypatch.setenv("ZIL_WATCH_REFERENCE_5M_VOLUME", "1000000")
    watch, dispatcher = make_watcher(tmp_path)
    asyncio.run(watch.tick(NOW))
    assert watch.references[1]["threshold"] == "750000.0"
    assert watch.references[5]["threshold"] == "1500000.0"
    assert list(watch.audit.get("zil_watch_sent")) == [f"KRW-ZIL:1:{int(NOW - 63)}"]
    restored = ZilWatcher(dispatcher, watch.audit, watch.client)
    assert restored.references == watch.references


@pytest.mark.parametrize("bad", ["NaN", "Infinity", "-1"])
def test_invalid_observed_volume_never_alerts(bad):
    b = bars()
    b[-1]["candle_acc_trade_volume"] = bad
    assert not detect(b, 1, NOW)
