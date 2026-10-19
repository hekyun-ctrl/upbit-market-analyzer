"""Pre-send native follow-through, an explicit operational hypothesis.

Only bars which START after candidate discovery may confirm it. This does
not predict a warning-free future, change stops, or suppress risk notices.
"""
import math

from lbank_native_entry import SOURCE

POLICY = "native_two_closed_minute_followthrough_v1"


def confirmation_quality(state, watch):
    """Return (allowed, reason, frozen evidence); no ticker/native fallback."""
    try:
        d, price, quote = watch["direction"], watch["price"], state["quote"]
        start = watch["native_watch_1m_start"]
        bars = state["minute_bars"]
        if (state["source"] != SOURCE or d not in (-1, 1)
                or not all(math.isfinite(v) and v > 0 for v in (price, quote))
                or not isinstance(start, int) or start % 60 or len(bars) < 2):
            return False, "invalid_native_confirmation_evidence", None
        # The bar at start+60 is already forming at discovery. Exclude it.
        after = [b for b in bars if b["start"] >= start + 120]
        if len(after) < 2:
            return False, "await_two_post_watch_closed_minutes", None
        last = after[-2:]
        if (last[-1] is not bars[-1] or last[1]["start"]-last[0]["start"] != 60
                or any(b["quality"] != "stream_closed" or b["start"] % 60 for b in last)):
            return False, "invalid_native_confirmation_evidence", None
        for b in last:
            o, h, l, c = [float(b[k]) for k in ("open", "high", "low", "close")]
            if (not all(math.isfinite(v) and v > 0 for v in (o,h,l,c))
                    or l > min(o,c) or h < max(o,c) or l > h):
                return False, "invalid_native_confirmation_evidence", None
        stop = float(watch["stop"])
        if not math.isfinite(stop) or stop <= 0 or d*(price-stop) <= 0:
            return False, "invalid_native_confirmation_evidence", None
        key = "low" if d == 1 else "high"
        if any(d*(b[key]-stop) <= 0 for b in after):
            return False, "watch_structure_stop_crossed", None
        if d * (quote-price) <= 0:
            return False, "quote_not_progressing_from_watch", None
        if d * (quote-last[-1]["close"]) < 0:
            return False, "quote_lost_closed_minute_reaction", None
        if (any(d*(b["close"]-price) <= 0 for b in last)
                or d*(last[1]["close"]-last[0]["close"]) <= 0
                or any(d*(b["close"]-b["open"]) <= 0 for b in last)):
            return False, "closed_minute_followthrough_missing", None
        # A breakout must stay beyond its ORIGINAL, frozen breakout line.
        level = watch.get("level")
        if watch.get("strategy") == "breakout":
            if level is None or not math.isfinite(level) or level <= 0:
                return False, "breakout_level_unavailable", None
            if any(d*(b["close"]-level) <= 0 for b in last) or d*(quote-level) <= 0:
                return False, "breakout_level_not_held", None
        return True, "native_followthrough_confirmed", {
            "policy": POLICY, "watch_last_closed_1m": start,
            "confirmed_1m_starts": [b["start"] for b in last],
            "confirmed_1m_closes": [b["close"] for b in last],
            "watch_price": price, "confirmation_quote": quote,
            "signed_progress_pct": d*(quote/price-1)*100,
            "frozen_breakout_level": level if watch.get("strategy") == "breakout" else None}
    except (KeyError, TypeError, ValueError, IndexError, OverflowError):
        return False, "invalid_native_confirmation_evidence", None
