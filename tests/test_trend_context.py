import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from trend_context import higher_timeframe_context

NOW = datetime(2026, 10, 1, 6, 3, tzinfo=timezone.utc)


def dated_candles(minutes, falling=False):
    end = NOW - timedelta(minutes=minutes)
    rows = []
    for i in range(32):
        close = 100 + i * 0.2 + (0, 0.5, -0.5, 0.6, 0.2)[i % 5]
        if falling:
            close = 200 - close
        rows.append(
            {
                "candle_date_time_utc": (
                    end - timedelta(minutes=minutes * (31 - i))
                ).isoformat(),
                "opening_price": close - 0.1,
                "high_price": close + 0.3,
                "low_price": close - 0.3,
                "trade_price": close,
                "candle_acc_trade_volume": 100,
            }
        )
    return list(reversed(rows))


def test_falling_four_hour_background_does_not_veto_hourly_reversal():
    context = higher_timeframe_context(
        dated_candles(15), dated_candles(60), dated_candles(240, falling=True), now=NOW
    )
    assert context["ready"]
    assert context["hourly_established"]
    assert context["four_hour"]["status"] == "하락 우위"


def test_unfinished_and_future_bars_cannot_change_holding_context():
    fifteen, hourly = dated_candles(15), dated_candles(60)
    expected = higher_timeframe_context(fifteen, hourly, [], now=NOW)
    for rows in (fifteen, hourly):
        rows.insert(
            0,
            {
                **rows[0],
                "candle_date_time_utc": NOW.isoformat(),
                "trade_price": 1,
                "low_price": 1,
            },
        )
        rows.insert(
            0,
            {**rows[0], "candle_date_time_utc": (NOW + timedelta(days=1)).isoformat()},
        )
    assert higher_timeframe_context(fifteen, hourly, [], now=NOW) == expected


def test_stale_missing_and_gapped_higher_data_cannot_enable_exception():
    fifteen, hourly = dated_candles(15), dated_candles(60)
    assert not higher_timeframe_context(fifteen, [], [], now=NOW)["ready"]
    assert not higher_timeframe_context(
        fifteen, hourly, [], now=NOW + timedelta(hours=2)
    )["ready"]
    assert not higher_timeframe_context(fifteen, hourly[:3] + hourly[4:], [], now=NOW)[
        "ready"
    ]
    for c in hourly:
        c.pop("candle_date_time_utc")
    assert not higher_timeframe_context(fifteen, hourly, [], now=NOW)["ready"]


def test_recent_unconfirmed_low_is_not_a_structural_protection_level():
    fifteen, hourly = dated_candles(15), dated_candles(60)
    before = higher_timeframe_context(fifteen, hourly, [], now=NOW)
    fifteen[0]["low_price"] = 1
    after = higher_timeframe_context(fifteen, hourly, [], now=NOW)
    assert after["fifteen"]["support"] == before["fifteen"]["support"]


def test_wld_regression_does_not_use_later_rally_to_confirm_morning_trend():
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/wld_20260930.json").read_text()
    )
    morning, evening = [datetime.fromisoformat(t) for t in fixture["as_of_kst"]]
    early = higher_timeframe_context(
        fixture["fifteen"], fixture["hourly"], fixture["four_hour"], now=morning
    )
    later = higher_timeframe_context(
        fixture["fifteen"], fixture["hourly"], fixture["four_hour"], now=evening
    )
    assert early["ready"] and not early["hourly_established"]
    assert early["hourly"]["close"] == 665
    assert early["hourly"]["ma20"] == 671.25
    assert later["hourly_established"]
    assert later["hourly"]["close"] == 698
    assert later["hourly"]["last_volume_ratio"] > 1.5
    assert not later["four_hour"]["established"]
