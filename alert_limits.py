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


def restored_early_watch_usage(
    overnight_count: int,
    morning_count: int,
    morning_priority_count: int,
    base_limit: int,
) -> tuple[int, int]:
    """Normalize persisted usage after introducing the morning reserve.

    Legacy deployments could spend priority slots before 08:00 KST. Count at
    most the base allocation from that period so those historical overflows do
    not keep the protected morning reserve locked for the rest of the day.
    """
    base = max(0, int(base_limit))
    overnight = max(0, int(overnight_count))
    morning = max(0, int(morning_count))
    morning_priority = max(0, int(morning_priority_count))
    return min(overnight, base) + morning, morning_priority
