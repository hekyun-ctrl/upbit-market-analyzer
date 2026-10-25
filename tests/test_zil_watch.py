import asyncio
from datetime import datetime, timezone
from unittest.mock import AsyncMock

import pytest
from audit_store import AuditStore
from zil_watch import ZilWatcher, detect, end_at, message

NOW = datetime(2026, 10, 26, tzinfo=timezone.utc).timestamp() + 3


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
    assert len(hits) == 1 and hits[0]["ratio"] == 10


def test_incomplete_future_old_and_low_value_excluded():
    b = bars()
    assert not detect(b, 1, NOW - 5)
    assert not detect(b, 1, NOW + 180)
    b[-1]["candle_acc_trade_price"] = 9999999
    assert not detect(b, 1, NOW)


def test_sparse_baseline_disclosed_and_zero_baseline_excluded():
    b = bars()
    b[0] = row(NOW - 7200)
    assert detect(b, 1, NOW)[0]["sparse"]
    for r in b[:-1]:
        r["candle_acc_trade_volume"] = 0
    assert not detect(b, 1, NOW)


def test_prior_only_and_duplicate_rows():
    b = bars()
    assert detect(b + [b[-1], row(NOW + 60, 1e10, 1e10)], 1, NOW)[0]["ratio"] == 10


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
    text = message(detect(bars(), 1, NOW), {"trade_price": 3.3}, bars(5), [], {}, NOW)
    assert "매수 신호가 아닙니다" in text
    assert "저항 확인 불가" in text
    assert "1시간 흐름 자료 부족" in text
