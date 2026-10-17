"""Structural entry patterns from closes, optionally using native candle wicks.

No private API, orders or trade execution. Each proposal still needs completed
5-minute sampled RSI *direction* alignment, fresh second-stage confirmation,
fee-aware R:R and risk cap.
"""


def detect_structure(prices, ema, rsi):
    candidates = detect_structures(prices, ema, rsi)
    return candidates[0] if candidates else None


def detect_structures(prices, ema, rsi, bars=None, min_range_fraction=.006, diagnostics=None):
    """Return all range-bounce and trend-pullback candidates independently."""
    if len(prices) < 65:
        return []
    candidates = []
    curr = prices[-1]
    tail = prices[-6:]
    swing_low, swing_high = min(tail[:-1]), max(tail[:-1])
    if bars is not None:
        if len(bars) != len(prices):
            raise ValueError("native candles and closes must align")
        swing_low = min(b["low"] for b in bars[-6:-1])
        swing_high = max(b["high"] for b in bars[-6:-1])
    macd = [a - b for a, b in zip(ema(prices, 12), ema(prices, 26))]
    signal = ema(macd, 9)
    histogram = [a - b for a, b in zip(macd, signal)]
    channel = prices[-65:-5]
    low, high = min(channel), max(channel)
    if bars is not None:
        low = min(b["low"] for b in bars[-65:-5])
        high = max(b["high"] for b in bars[-65:-5])
    width = high - low
    balanced = (min_range_fraction <= width / curr <= .025 and
                abs(sum(channel[:30]) / 30 - sum(channel[30:]) / 30) <= .42 * width)
    if diagnostics is not None:
        diagnostics.update(range_width_pct=width/curr*100,
                           range_required_pct=min_range_fraction*100,
                           range_width_ok=min_range_fraction <= width/curr <= .025,
                           range_balanced=balanced)
    if balanced:
        if (low - curr * .00065 <= swing_low <= low + .18 * width and
                low < curr <= low + .35 * width and
                curr - swing_low >= curr * .00055 and curr > prices[-2] and
                histogram[-1] > histogram[-3]):
            candidates.append({"direction": 1, "strategy": "range_rebound",
                    "level": low, "structure_anchor": swing_low, "barrier": high})
        if (high - .18 * width <= swing_high <= high + curr * .00065 and
                high - .35 * width <= curr < high and
                swing_high - curr >= curr * .00055 and curr < prices[-2] and
                histogram[-1] < histogram[-3]):
            candidates.append({"direction": -1, "strategy": "range_rebound",
                    "level": high, "structure_anchor": swing_high, "barrier": low})

    # Continuation on a rising/falling 20-period EMA after local swing retest.
    # Do not fabricate resistance for an open-trend target.
    fast, slow = ema(prices, 20), ema(prices, 50)
    peak, trough = max(prices[-30:-6]), min(prices[-30:-6])
    if bars is not None:
        peak = max(b["high"] for b in bars[-30:-6])
        trough = min(b["low"] for b in bars[-30:-6])
    if diagnostics is not None:
        diagnostics.update(long_pullback_pct=(peak-swing_low)/curr*100,
                           short_pullback_pct=(swing_high-trough)/curr*100,
                           pullback_min_pct=.15,
                           ema_direction=1 if fast[-1]>slow[-1] else -1 if fast[-1]<slow[-1] else 0)
    if (fast[-1] > slow[-1] and fast[-1] > fast[-9] + curr * .00025 and
            slow[-1] > slow[-12] and
            fast[-1] - curr * .0015 <= swing_low <= fast[-1] + curr * .0006 and
            curr > fast[-1] + curr * .0001 and curr > prices[-2] and
            curr - swing_low >= curr * .00055 and
            .0015 <= (peak - swing_low) / curr <= .006 and
            histogram[-1] > histogram[-3]):
        candidates.append({"direction": 1, "strategy": "trend_retest",
                "level": fast[-1], "structure_anchor": swing_low})
    if (fast[-1] < slow[-1] and fast[-1] < fast[-9] - curr * .00025 and
            slow[-1] < slow[-12] and
            fast[-1] - curr * .0006 <= swing_high <= fast[-1] + curr * .0015 and
            curr < fast[-1] - curr * .0001 and curr < prices[-2] and
            swing_high - curr >= curr * .00055 and
            .0015 <= (swing_high - trough) / curr <= .006 and
            histogram[-1] < histogram[-3]):
        candidates.append({"direction": -1, "strategy": "trend_retest",
                "level": fast[-1], "structure_anchor": swing_high})
    return candidates


def confirm_structure(prices, watch, ema, rsi, current_price=None):
    """Fresh quote confirms reaction is intact rather than chasing the first bounce."""
    if not watch or watch.get("strategy", "breakout") == "breakout" or len(prices) < 65:
        return False
    curr = prices[-1] if current_price is None else current_price
    direction, level, anchor = watch["direction"], watch["level"], watch["structure_anchor"]
    macd = [a - b for a, b in zip(ema(prices, 12), ema(prices, 26))]
    sig = ema(macd, 9)
    hist = [a - b for a, b in zip(macd, sig)]
    m5 = (curr / prices[-6] - 1) * 100
    if direction == 1:
        valid = (curr > anchor and
                 curr >= level - curr * .0002 and
                 curr >= prices[-2] - curr * .0001 and
                 hist[-1] >= hist[-3] and m5 >= -.07)
    else:
        valid = (curr < anchor and
                 curr <= level + curr * .0002 and
                 curr <= prices[-2] + curr * .0001 and
                 hist[-1] <= hist[-3] and m5 <= .07)
    if watch["strategy"] == "range_rebound":
        valid = valid and direction * (watch["barrier"] - curr) > 0
    return valid
