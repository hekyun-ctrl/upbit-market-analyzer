"""User availability schedule for *new* LBank trading-opportunity alerts.

All checks are Korean Standard Time (UTC+09:00), independent of Railway UTC.
Wednesday, Thursday, Friday and Saturday 23:00 through following 06:00 are
quiet hours when explicitly enabled. Quiet-hour blocking is disabled by
default at the user's request for 24-hour performance observation.

Never apply to protective position warnings, targets, manual-position bot
commands, risk follow-ups or 24-hour market-data research.
"""
import os
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
QUIET_START_HOUR = 23
QUIET_END_HOUR = 6
QUIET_START_WEEKDAYS = frozenset((2, 3, 4, 5))  # Wed Thu Fri Sat


def quiet_hours_enabled():
    """Opt-in only; default allows opportunity reviews at every hour."""
    return os.getenv("LBANK_QUIET_HOURS_ENABLED", "false").lower() == "true"


def is_quiet_new_entry_time(epoch_seconds):
    """Whether fresh discretionary entry notifications should be muted.

    Uses the START weekday for 00:00-05:59, not the present day's weekday.
    Returns False at exactly 06:00; True at exactly 23:00 on quiet nights.
    """
    if not quiet_hours_enabled():
        return False
    local = datetime.fromtimestamp(float(epoch_seconds), tz=KST)
    day, hour = local.weekday(), local.hour
    if hour >= QUIET_START_HOUR:
        return day in QUIET_START_WEEKDAYS
    if hour < QUIET_END_HOUR:
        return ((day - 1) % 7) in QUIET_START_WEEKDAYS
    return False
