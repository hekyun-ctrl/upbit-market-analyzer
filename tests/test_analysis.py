from analysis import analyze_candles


def _candles(count: int = 80):
    chronological = [
        {
            "trade_price": 100.0 + index,
            "candle_acc_trade_volume": 1000.0 + index,
        }
        for index in range(count)
    ]
    return list(reversed(chronological))


def test_analysis_uses_newest_first_upbit_order():
    result = analyze_candles(_candles())
    assert result["latest_price"] == 179.0
    assert result["ma20"] == 169.5
    assert result["rsi14"] == 100.0
    assert "권유" in result["disclaimer"]
