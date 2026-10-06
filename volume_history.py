"""Pure helpers for a shadow 72-hour KRW turnover leaderboard."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


def _parse_utc(value: Any) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return parsed.replace(tzinfo=timezone.utc) if parsed.tzinfo is None else parsed.astimezone(timezone.utc)


def build_72h_turnover_snapshot(
    candles_by_market: dict[str, list[dict[str, Any]]],
    as_of: datetime,
) -> dict[str, Any]:
    """Rank markets by completed hourly KRW traded value in the last 72 hours.

    The in-progress hour is excluded. Markets with less than 72 hours of
    available candle history receive no rank, rather than a misleading low
    turnover rank. Upbit omits candles for hours with no trades; those hours
    contribute zero to the rolling sum.
    """
    now = as_of.replace(tzinfo=timezone.utc) if as_of.tzinfo is None else as_of.astimezone(timezone.utc)
    boundary = now.replace(minute=0, second=0, microsecond=0)
    start = boundary - timedelta(hours=72)
    rows: list[dict[str, Any]] = []

    for market, candles in candles_by_market.items():
        parsed = []
        for candle in candles:
            opened = _parse_utc(candle.get("candle_date_time_utc") or candle.get("time_utc"))
            if opened is None or opened >= boundary:
                continue
            try:
                value = max(0.0, float(candle.get("candle_acc_trade_price") or candle.get("trade_value") or 0))
            except (TypeError, ValueError):
                continue
            parsed.append((opened, value))

        oldest = min((opened for opened, _ in parsed), default=None)
        recent = [(opened, value) for opened, value in parsed if start <= opened < boundary]
        complete = oldest is not None and oldest <= start
        rows.append({
            "market": market,
            "turnover_72h_krw": round(sum(value for _, value in recent), 2) if complete else None,
            "history_complete": complete,
            "traded_hours_72h": len(recent) if complete else None,
            "latest_completed_hour_utc": max((opened for opened, _ in parsed), default=None).isoformat() if parsed else None,
        })

    ranked = sorted(
        (row for row in rows if row["history_complete"]),
        key=lambda row: (-float(row["turnover_72h_krw"] or 0), row["market"]),
    )
    universe = len(ranked)
    for rank, row in enumerate(ranked, start=1):
        row["rank_72h"] = rank
        row["rank_percentile_72h"] = round(rank / universe * 100, 2) if universe else None
    for row in rows:
        row.setdefault("rank_72h", None)
        row.setdefault("rank_percentile_72h", None)

    return {
        "as_of_utc": boundary.isoformat(),
        "window_hours": 72,
        "requested_market_count": len(candles_by_market),
        "ranked_market_count": universe,
        "items": rows,
    }


def summarize_72h_ranked_outcomes(outcomes: list[dict[str, Any]]) -> dict[str, Any]:
    """Compare completed candidate outcomes by the recorded 72h rank band."""
    definitions = (("top_50", 1, 50), ("rank_51_100", 51, 100), ("rank_101_plus", 101, None))
    buckets = {
        name: {"sample_count": 0, "target_1_first": 0, "stop_first": 0, "other": 0}
        for name, _, _ in definitions
    }
    unrated = 0
    for outcome in outcomes:
        rank = outcome.get("volume_72h_rank")
        if not isinstance(rank, int) or rank < 1:
            unrated += 1
            continue
        bucket = next(
            (
                buckets[name]
                for name, low, high in definitions
                if rank >= low and (high is None or rank <= high)
            ),
            None,
        )
        if bucket is None:
            unrated += 1
            continue
        bucket["sample_count"] += 1
        result = outcome.get("result")
        key = result if result in {"target_1_first", "stop_first"} else "other"
        bucket[key] += 1
    for bucket in buckets.values():
        decided = bucket["target_1_first"] + bucket["stop_first"]
        bucket["decided_count"] = decided
        bucket["target_1_first_rate_pct"] = (
            round(bucket["target_1_first"] / decided * 100, 2) if decided else None
        )
    return {
        "by_rank_band": buckets,
        "unrated_outcome_count": unrated,
        "definition": "실제 전송 후보의 목표1·손절 선도달을 발송 시 저장한 72h 순위 구간별 집계",
        "automatic_threshold_tuning": False,
    }
