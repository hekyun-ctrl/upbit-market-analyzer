"""Bounded live WebSocket execution evidence; OHLC warm-up is never buy flow."""
from collections import defaultdict, deque
from dataclasses import dataclass, field
from math import isfinite


@dataclass
class _Bucket:
    second: int
    buy: float = 0.0
    sell: float = 0.0
    count: int = 0
    low: float = float("inf")
    close: float = 0.0
    last_ms: int = 0
    ids: set = field(default_factory=set)
    incomplete: bool = False


class TradeFlow:
    def __init__(self):
        self._buckets = defaultdict(deque)
        self._started = {}

    def clear(self):
        self._buckets.clear()
        self._started.clear()

    def observe(self, market, price, volume, timestamp_ms, side, sequential_id):
        if not all(isfinite(x) and x > 0 for x in (price, volume)):
            return
        second = timestamp_ms // 1000
        buckets = self._buckets[market]
        if buckets and second < buckets[-1].second:
            return  # Older/out-of-order events must not rewrite a completed window.
        self._started.setdefault(market, second)
        if not buckets or second != buckets[-1].second:
            buckets.append(_Bucket(second))
        while buckets and buckets[0].second < second - 120:
            buckets.popleft()
        bucket = buckets[-1]
        if sequential_id is None or side not in ("BID", "ASK"):
            bucket.incomplete = True
            return
        if sequential_id in bucket.ids:
            return
        if len(bucket.ids) >= 512:
            bucket.incomplete = True  # Bounded memory; never certify a truncated sample.
            return
        bucket.ids.add(sequential_id)
        bucket.count += 1
        if side == "BID":
            bucket.buy += price * volume
        else:
            bucket.sell += price * volume
        bucket.low = min(bucket.low, price)
        if timestamp_ms >= bucket.last_ms:
            bucket.close, bucket.last_ms = price, timestamp_ms

    def snapshot(self, market, now):
        buckets = [b for b in self._buckets.get(market, ()) if now - 60 <= b.second < now]
        windows = []
        for index in range(3):
            selected = [b for b in buckets if now - 60 + index * 20 <= b.second < now - 40 + index * 20]
            buy, sell = sum(b.buy for b in selected), sum(b.sell for b in selected)
            valid = [b for b in selected if b.count]
            windows.append({"buy_krw": buy, "sell_krw": sell,
                "count": sum(b.count for b in selected),
                "low": min((b.low for b in valid), default=0),
                "close": valid[-1].close if valid else 0})
        context = {"source": "upbit_websocket_trade", "as_of": now,
            "window_seconds": 60, "windows": windows,
            "ready": bool(buckets and self._started.get(market, now) <= now - 60
                          and not any(b.incomplete for b in buckets)
                          and 0 < now - buckets[-1].second <= 5)}
        buy = sum(w["buy_krw"] for w in windows)
        total = buy + sum(w["sell_krw"] for w in windows)
        context.update(buy_share_pct=round(buy / total * 100, 2) if total else 0,
                       total_krw=total, trade_count=sum(w["count"] for w in windows))
        return context


def buying_persistent(context, current, now):
    """Check 60 completed seconds, including the most recent 20s window."""
    if (not context or not context.get("ready")
            or context.get("source") != "upbit_websocket_trade"
            or context.get("window_seconds") != 60
            or abs(now - float(context.get("as_of", 0))) > 10):
        return False
    windows = context.get("windows", [])
    if len(windows) != 3 or any(w.get("count", 0) < 5 for w in windows):
        return False
    totals = [w["buy_krw"] + w["sell_krw"] for w in windows]
    if sum(totals) < 10_000_000 or any(t < 1_000_000 for t in totals):
        return False
    shares = [w["buy_krw"] / total for w, total in zip(windows, totals)]
    buy_share = sum(w["buy_krw"] for w in windows) / sum(totals)
    lows = [w["low"] for w in windows]
    closes = [w["close"] for w in windows]
    return bool(buy_share >= .60 and shares[-1] >= .60
                and sum(s >= .60 for s in shares) >= 2
                and sum(w["count"] for w in windows) >= 20
                and min(lows + closes) > 0
                and all(b >= a * .9985 for a, b in zip(lows, lows[1:]))
                and closes[-1] >= closes[0] * 1.001
                and lows[-1] * .998 <= current <= closes[-1] * 1.005)
