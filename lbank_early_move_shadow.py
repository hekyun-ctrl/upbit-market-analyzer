"""Research-only early-move and rejected-entry diagnostic lanes for LBank.

Never emits Telegram, grants an entry, adjusts a risk gate, or reads future bars.
Use only already CLOSED native 1m bars. A 15m/1h conflict is reported rather
than silently suppressing a price-risk observation. NOT a validated predictor.
"""
import math
from lbank_five_timeframe_shadow import valid_observed_bar

POLICY = "zb_early_move_shadow_v2"


def _positive(x):
    return isinstance(x, (int, float)) and not isinstance(x, bool) and math.isfinite(x) and x > 0


def early_move_shadow(closed_minutes, evaluated_at, *, direction=-1,
                      trend15=None, trend60=None, prior_notice_at=None,
                      prior_notice_direction=None, cooldown_seconds=900,
                      min_break_fraction=.0004, max_chase_fraction=.007):
    """Separate a potential *information-only* early break from entry approval.

    The prior support/resistance is determined exclusively by bars BEFORE the
    latest two closed minutes. The move against that level is measured using
    latest CLOSED price. No tick volume, liquidity or higher timeframe is
    fabricated. Alerts are never sent by this function.
    """
    result=dict(policy=POLICY,shadow_only=True,entry_approved=False,
                telegram_send=False,status="insufficient_data",
                context="unverified",risk_note="not_an_entry_signal")
    if (direction not in (-1,1) or type(evaluated_at) not in (int,float)
            or not math.isfinite(evaluated_at)
            or not 0 < min_break_fraction < max_chase_fraction < 1
            or len(closed_minutes)<9):
        return result
    bars=list(closed_minutes[-20:])
    if (not all(valid_observed_bar(b,60,evaluated_at) for b in bars)
            or any(b["start"]-a["start"] != 60 for a,b in zip(bars,bars[1:]))):
        result["status"]="invalid_or_gapped_native_minutes"
        return result
    past=bars[:-2]
    level=(min(b["low"] for b in past) if direction == -1
           else max(b["high"] for b in past))
    latest=bars[-1]
    if any(b["received_at"] > latest["start"] for b in past):
        result["status"] = "prior_level_not_known_before_break"
        return result
    displacement=direction*(latest["close"]/level-1)
    result.update(level=level,closed_price=latest["close"],
                  break_pct=round(displacement*100,5),
                  evaluated_closed_start=latest["start"])
    if displacement <= min_break_fraction:
        result["status"]="no_confirmed_early_break"
        return result
    # Catch early movement; do not re-label already extended entries as early.
    if displacement >= max_chase_fraction:
        result["status"]="late_move_chase_prohibited"
        return result
    a,b=bars[-2:]
    if direction * (a["close"]/level-1) > min_break_fraction:
        result["status"] = "level_already_broken"
        return result
    # The first closed minute beyond the pre-existing level can be enough to
    # observe a setup. A second directional candle is not required.
    if (direction*(b["close"]-b["open"]) <= 0
            or direction*(b["close"]-a["close"]) <= 0):
        result["status"]="minute_momentum_unconfirmed"
        return result
    if trend15 in (-1,1) and trend60 in (-1,1):
        context=("aligned" if trend15 == direction and trend60 == direction
                 else "mixed" if trend15 != trend60
                 else "countertrend")
    else:
        context="higher_timeframe_unverified"
    result["context"]=context
    if (prior_notice_at is not None and prior_notice_direction == direction
            and evaluated_at-prior_notice_at < cooldown_seconds):
        result["status"]="duplicate_suppressed"
        return result
    result["status"]="early_break_shadow"
    return result


def rejected_entry_review(reason, baseline_entry, current_quote, *,
                          observed_at, latest_closed_bar,
                          denied_minute_start=None,
                          max_deviation_fraction=.001):
    """Identify fresh-price reassessment without authorizing stale entry.

    A materially changed price NEVER inherits the prior trade's stop/target.
    A new complete 1m candle is needed to run the independent strategy
    evaluator again on new market structure.
    """
    review=dict(policy=POLICY,shadow_only=True,entry_approved=False,
                telegram_send=False,status="invalid_snapshot")
    if (not _positive(baseline_entry) or not _positive(current_quote)
            or not isinstance(observed_at,(int,float))
            or not math.isfinite(observed_at)
            or not math.isfinite(max_deviation_fraction)
            or max_deviation_fraction<=0):
        return review
    gap=abs(current_quote/baseline_entry-1)
    review["deviation_pct"]=round(100*gap,5)
    if reason == "avoid_late_5m_impulse_chase":
        review["status"]="research_earlier_signal_only_no_chase_override"
    elif reason == "price_deviation":
        fresh = (type(denied_minute_start) is int and latest_closed_bar is not None
                 and valid_observed_bar(latest_closed_bar,60,observed_at)
                 and denied_minute_start < latest_closed_bar["start"]
                 == int(observed_at//60)*60-60)
        review["status"] = ("fresh_structure_reassessment_needed" if fresh
                            else "await_fresh_closed_minute")
        review["stale_entry_disallowed"]=True
        review["old_stop_target_reuse_disallowed"]=True
        review["observed_at"] = observed_at
        review["fresh_closed_start"] = latest_closed_bar["start"] if fresh else None
    else:
        review["status"]="keep_existing_gate"
    return review
