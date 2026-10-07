"""Time-aware ceilings for capped Telegram discovery notifications."""

from __future__ import annotations


def early_watch_daily_limits(base_limit: int, priority_reserve: int, local_hour: int) -> tuple[int, int]:
    """Return (total ceiling, available reserve) without spending morning slots overnight.

    From 00:00 through 07:59 KST, only the standard daily allocation is usable.
    At 08:00 KST the reserve becomes available to qualifying priority signals.
    """
    base = max(0, int(base_limit))
    reserve = max(0, int(priority_reserve))
    hour = int(local_hour)
    if not 0 <= hour <= 23:
        raise ValueError("local_hour must be between 0 and 23")
    if hour < 8:
        return base, 0
    return base + reserve, reserve
