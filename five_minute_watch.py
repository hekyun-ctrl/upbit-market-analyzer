"""Observation-only completed 5-minute turnover acceleration.

Never places an order or bypasses the existing candidate safety checks.
"""
from collections import defaultdict, deque
from datetime import datetime, timezone
from math import isfinite
import os
import time

from minute_surge import normalized

VERSION = "five-minute-watch-v1"
PERIOD = 300


def enabled():
    return os.getenv("UPBIT_FIVE_MINUTE_WATCH_ENABLED", "false").lower() in ("true", "1", "yes")


def proof(bars, market):
    """Use only a completed 5m bar and 20 preceding complete 5m buckets."""
    if len(bars) < 21:
        return None
    past, current = bars[-21:-1], bars[-1]
    if current["start"] - past[0]["start"] != 20 * PERIOD:
        return None
    if any(past[i]["start"] - past[i - 1]["start"] != PERIOD for i in range(1, 20)):
        return None
    if current["start"] - past[-1]["start"] != PERIOD:
        return None
    if not all(isfinite(current[k]) for k in ("open", "high", "low", "close", "value")):
        return None
    previous = past[-1]
    if previous["close"] <= 0 or current["open"] <= 0:
        return None
    change = (current["close"] / previous["close"] - 1) * 100
    baseline = sum(row["value"] for row in past) / 20
    if baseline <= 0:
        return None
    ratio = current["value"] / baseline
    level = max(row["high"] for row in past[-12:])
    span = current["high"] - current["low"]
    close_position = (current["close"] - current["low"]) / span if span > 0 else 0.0
    if not (current["value"] >= 50_000_000 and ratio >= 3.0
            and change >= 2.0 and current["close"] > level
            and current["close"] > current["open"] and close_position >= 0.65):
        return None
    return dict(market=market, start=current["start"], close=current["close"],
                breakout_level=level, change_5m_pct=round(change, 3),
                trade_value_5m_krw=round(current["value"]),
                five_minute_turnover_ratio=round(ratio, 3),
                close_position=round(close_position, 3))


class FiveMinuteWatch:
    def __init__(self):
        self.bars = defaultdict(lambda: deque(maxlen=30))
        self.live = {}
        self.last_seq = {}

    def warm(self, market, candles, now):
        """Seed prior completed candles only; never send historical alerts."""
        groups = {}
        for raw in candles:
            try:
                b = normalized(raw)
            except (TypeError, KeyError, ValueError):
                continue
            k = b["start"] - b["start"] % PERIOD
            if k + PERIOD > now or k < now - 3 * 3600:
                continue
            x = groups.get(k)
            if x is None:
                groups[k] = dict(start=k, open=b["open"], high=b["high"],
                                 low=b["low"], close=b["close"], value=b["value"],
                                 last=b["start"])
            else:
                if b["start"] < x["first"]:
                    x["open"] = b["open"]
                if b["start"] > x["last"]:
                    x["close"], x["last"] = b["close"], b["start"]
                x["high"] = max(x["high"], b["high"])
                x["low"] = min(x["low"], b["low"])
                x["value"] += b["value"]
            groups[k]["first"] = min(groups[k].get("first", b["start"]), b["start"])
        result = []
        if groups:
            keys = sorted(groups)
            previous = None
            for k in range(keys[0], keys[-1] + PERIOD, PERIOD):
                x = groups.get(k)
                if x is None and previous is not None:
                    price = previous["close"]
                    x = dict(start=k, open=price, high=price, low=price, close=price, value=0.0)
                if x is not None:
                    result.append({key: x[key] for key in ("start", "open", "high", "low", "close", "value")})
                    previous = x
        self.bars[market] = deque(result[-30:], maxlen=30)

    def reset_live(self):
        self.live.clear()
        self.last_seq.clear()

    def update(self, market, price, volume, timestamp_ms, sequential_id=None, *, now=None):
        now = time.time() if now is None else now
        second = timestamp_ms // 1000
        if (not all(isfinite(v) for v in (price, volume, now))
                or price <= 0 or volume < 0 or not -1 <= now - second <= 10):
            return None
        if sequential_id is not None:
            if sequential_id <= self.last_seq.get(market, -1):
                return None
            self.last_seq[market] = sequential_id
        start = second - second % PERIOD
        current = self.live.get(market)
        if current and start < current["start"]:
            return None
        result = None
        if current and start > current["start"]:
            if start - current["start"] == PERIOD:
                # An initial partial bucket after restart is never certified.
                if current["complete"]:
                    result = self.completed(market, current, now)
            current = None
        if current is None:
            current = dict(start=start, open=price, high=price, low=price,
                           close=price, value=0.0,
                           complete=(second - start <= 10))
            self.live[market] = current
        current["high"] = max(current["high"], price)
        current["low"] = min(current["low"], price)
        current["close"] = price
        current["value"] += price * volume
        return result

    def completed(self, market, bar, now):
        """Accept one bar at a time, with no replay after reconnect."""
        rows = self.bars[market]
        if rows and bar["start"] <= rows[-1]["start"]:
            return None
        if rows:
            last = rows[-1]
            for k in range(last["start"] + PERIOD, bar["start"], PERIOD):
                price = rows[-1]["close"]
                rows.append(dict(start=k, open=price, high=price,
                                 low=price, close=price, value=0.0))
        rows.append({k: bar[k] for k in ("start", "open", "high", "low", "close", "value")})
        p = proof(list(rows), market)
        if p is None:
            return None
        return dict(signal="leader_volume_acceleration", label="완료 5분봉 급등 초기 관찰",
                    market=market, price=p["close"],
                    time_utc=datetime.fromtimestamp(now, timezone.utc).isoformat(),
                    internal_only=True, notify_early_watch=True,
                    completed_5m_watch=True, source_signal="five_minute_watch",
                    discovery_window="intraday", breakout_level=p["breakout_level"],
                    source_bar_closed_at=p["start"] + PERIOD,
                    trade_value_5m_krw=p["trade_value_5m_krw"],
                    five_minute_turnover_ratio=p["five_minute_turnover_ratio"],
                    observed_5m_change_pct=p["change_5m_pct"],
                    close_position=p["close_position"])
