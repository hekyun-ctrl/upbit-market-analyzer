"""Five-timeframe *observed* structure diagnostics, not an entry signal.

Use only closed, contiguous native 1m and 5m candles. 15m/1h/4h
closes are derived from complete 5m sequences; these are NOT separately
exchange-verified timeframe feeds. Never append the live quote as a candle.
"""
import math

POLICY = "native_1m_5m_15m_1h_4h_shadow_v2"


def valid_observed_bar(b, period, evaluated_at):
    try:
        values = [float(b[k]) for k in ("open", "high", "low", "close")]
        start, receipt = b["start"], b["received_at"]
        updated = b.get("updated", start)
        return (type(start) is int and start % period == 0
                and b.get("period", period) == period
                and b.get("quality") == "stream_closed"
                and type(updated) in (int, float) and math.isfinite(updated)
                and type(receipt) in (int, float) and math.isfinite(receipt)
                and all(math.isfinite(v) and v > 0 for v in values)
                and values[2] <= min(values[0], values[3])
                <= max(values[0], values[3]) <= values[1]
                and start + period <= receipt <= evaluated_at
                and start <= updated <= start + period + 15)
    except (ValueError, KeyError, TypeError, OverflowError):
        return False


def _closes_for_period(bars, base, aggregate, now, required=3):
    if aggregate % base:
        return None
    count = aggregate // base
    latest_complete = int(now // aggregate) * aggregate - aggregate
    first = latest_complete-(required-1)*aggregate
    relevant = [b for b in bars if first <= b["start"] < latest_complete+aggregate]
    index = {b["start"]: b for b in relevant}
    if len(index) != len(relevant):
        return None
    closes = []
    for g in range(latest_complete-(required-1)*aggregate,
                   latest_complete+1, aggregate):
        window=[index.get(g + j*base) for j in range(count)]
        if (any(b is None for b in window)
                or not all(valid_observed_bar(b,base,now) for b in window)):
            return None
        closes.append(window[-1]["close"])
    return closes


def _label(closes, neutral_fraction):
    if closes is None or len(closes)<2:
        return dict(direction="unverified", return_pct=None)
    change=float(closes[-1])/float(closes[-2])-1
    direction=("up" if change > neutral_fraction else
               "down" if change < -neutral_fraction else "flat")
    return dict(direction=direction, return_pct=round(change*100,5))


def five_timeframe_shadow(state, now):
    """Deterministic 1m/5m/15m/1h/4h snapshots, no future/quote leakage."""
    result=dict(policy=POLICY, shadow_only=True, entry_approved=False,
                sources={"1m":"closed_native_1m",
                         "5m":"closed_5m_derived_from_native_1m",
                         "15m":"complete_5m_aggregates",
                         "1h":"complete_5m_aggregates",
                         "4h":"complete_5m_aggregates"})
    try:
        minutes=state["minute_bars"]
        fives=state["five_bars"]
        settings=(("1m",minutes,60,60,.0003),
                  ("5m",fives,300,300,.0005),
                  ("15m",fives,300,900,.001),
                  ("1h",fives,300,3600,.002),
                  ("4h",fives,300,14400,.004))
        for name,bars,base,period,neutral in settings:
            closes=_closes_for_period(bars,base,period,now)
            result[name]=_label(closes,neutral)
        d15=result["15m"]["direction"]
        d60=result["1h"]["direction"]
        result["higher_conflict"] = _conflict(d15, d60)
        result["four_hour_conflict"] = _conflict(d60, result["4h"]["direction"])
        result["main_bias"] = {"up": "long", "down": "short", "flat": "neutral"}.get(
            d60, "unverified")
    except (ValueError,KeyError,TypeError,IndexError,OverflowError):
        for name in ("1m","5m","15m","1h","4h"):
            result[name]=dict(direction="unverified",return_pct=None)
        result["higher_conflict"]="unverified"
        result["four_hour_conflict"]="unverified"
        result["main_bias"]="unverified"
    result["interpretation"]="not_trade_approval"
    return result


def _conflict(first, second):
    if "unverified" in (first, second):
        return "unverified"
    return "mixed" if {first, second} == {"up", "down"} else "not_opposed"
