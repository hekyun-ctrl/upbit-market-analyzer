"""Reuse the original breakout predicate at discovery and confirmation.

No fitted thresholds, new indicators or extra waiting. A lost trigger can only
be supported by the already-existing larger-direction priority, not time alone.
This consistency guard is not a profitability-certified replacement strategy.
"""
POLICY = "native_breakout_trigger_or_higher_priority_v1"
REJECT_REASON = "breakout_trigger_lost_without_higher_priority"


def evidence(prices, native_state, ema):
    curr = prices[-1]
    m5, m15 = (curr/prices[-6]-1)*100, (curr/prices[-16]-1)*100
    macd_arr = [a-b for a,b in zip(ema(prices,12),ema(prices,26))]
    macd, sig = macd_arr[-1], ema(macd_arr,9)[-1]
    bars = native_state['minute_bars'] if native_state is not None else None
    prior_high = max(b['high'] for b in bars[-16:-1]) if bars else max(prices[-16:-1])
    prior_low = min(b['low'] for b in bars[-16:-1]) if bars else min(prices[-16:-1])
    return dict(momentum5_pct=m5,momentum15_pct=m15,
        breakout_long=curr>prior_high and macd>sig and m5>=.14 and m15>=.20,
        breakout_short=curr<prior_low and macd<sig and m5<=-.14 and m15<=-.20,
        prior_high=prior_high,prior_low=prior_low)


def confirmation_allowed(plan, fresh):
    quality=plan['entry_quality'];ranked=quality.get('ranked_entry') or {}
    trigger=fresh['breakout_long' if plan['direction']==1 else 'breakout_short']
    higher=bool((quality.get('priority') or {}).get('dominant_intact')
                or ranked.get('may_override_minor_reaction'))
    quality['breakout_confirmation']=dict(policy=POLICY,original_trigger_current=trigger,
        existing_higher_priority_support=higher,priority_override_used=not trigger and higher,
        original_predicate=fresh,extra_wait_seconds=0)
    return trigger or higher
