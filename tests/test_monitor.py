from monitor import MonitorConfig, SignalEngine


def _config(**overrides):
    values = {
        "markets": ("KRW-IQ",),
        "all_krw_markets": False,
        "price_surge_1m_pct": 1.5,
        "breakout_pct": 0.4,
        "volume_ratio": 2.0,
        "min_trade_value_krw": 1_000.0,
        "alert_cooldown_seconds": 600,
        "max_alerts_per_minute": 5,
        "rebreakout_enabled": True,
        "rebreakout_consolidation_seconds": 1800,
        "rebreakout_max_range_pct": 3.0,
        "rebreakout_price_buffer_pct": 0.3,
        "rebreakout_min_volume_ratio": 3.0,
    }
    values.update(overrides)
    return MonitorConfig(**values)


def test_price_and_volume_surge_is_detected_after_warmup():
    engine = SignalEngine(_config())
    alerts = []
    base_ms = 1_800_000_000_000

    for second in range(121):
        price = 100.0 if second < 61 else 100.0 + (second - 60) * 0.03
        volume = 0.1 if second < 61 else 1.0
        alerts.extend(engine.update("KRW-IQ", price, volume, base_ms + second * 1000))

    assert any(alert["signal"] == "price_volume_surge" for alert in alerts)


def test_alert_cooldown_blocks_duplicate_signal():
    engine = SignalEngine(_config(alert_cooldown_seconds=600))
    alerts = []
    base_ms = 1_800_000_000_000

    for second in range(150):
        price = 100.0 if second < 61 else 103.0
        volume = 0.1 if second < 61 else 1.0
        alerts.extend(engine.update("KRW-IQ", price, volume, base_ms + second * 1000))

    surge_alerts = [a for a in alerts if a["signal"] == "price_volume_surge"]
    assert len(surge_alerts) == 1


def test_invalid_non_positive_trade_is_ignored():
    engine = SignalEngine(_config())
    assert engine.update("KRW-IQ", 0, 10, 1_800_000_000_000) == []


def test_long_consolidation_volume_rebreakout_is_detected():
    engine = SignalEngine(_config(min_trade_value_krw=1_000.0))
    alerts = []
    base_ms = 1_800_000_000_000

    for second in range(1921):
        if second < 1861:
            price = 100.0 + (0.2 if second % 20 < 10 else -0.2)
            volume = 0.2
        else:
            price = 100.2 + (second - 1860) * 0.02
            volume = 2.0
        alerts.extend(engine.update("KRW-IQ", price, volume, base_ms + second * 1000))

    rebreakouts = [
        alert for alert in alerts if alert["signal"] == "consolidation_rebreakout"
    ]
    assert len(rebreakouts) == 1
    assert rebreakouts[0]["consolidation_minutes"] == 30
    assert rebreakouts[0]["consolidation_range_pct"] <= 3.0
    assert rebreakouts[0]["volume_ratio_vs_consolidation"] >= 3.0


def test_wide_range_is_not_classified_as_consolidation_rebreakout():
    engine = SignalEngine(_config(min_trade_value_krw=1_000.0))
    alerts = []
    base_ms = 1_800_000_000_000

    for second in range(1921):
        if second < 1861:
            price = 100.0 + (4.0 if second % 20 < 10 else -4.0)
            volume = 0.2
        else:
            price = 105.0 + (second - 1860) * 0.02
            volume = 2.0
        alerts.extend(engine.update("KRW-IQ", price, volume, base_ms + second * 1000))

    assert not any(
        alert["signal"] == "consolidation_rebreakout" for alert in alerts
    )


def test_rebreakout_requires_volume_expansion():
    engine = SignalEngine(
        _config(min_trade_value_krw=100.0, price_surge_1m_pct=10.0)
    )
    alerts = []
    base_ms = 1_800_000_000_000

    for second in range(1921):
        if second < 1861:
            price = 100.0 + (0.2 if second % 20 < 10 else -0.2)
        else:
            price = 100.2 + (second - 1860) * 0.02
        alerts.extend(
            engine.update("KRW-IQ", price, 0.2, base_ms + second * 1000)
        )

    assert not any(
        alert["signal"] == "consolidation_rebreakout" for alert in alerts
    )
