# Completed-minute breakout context and bounded post-stop retry

Base: `1bf35cd426699ef8a61c7d92f2c1ec9827d597a3`.
Service: `lbank-btc-futures-monitor`, existing `capture` mode.

The 2026-11-24 01:37–01:39 KST incident logs contain short breakout
candidates rejected as `flat_sampled_market`: the last completed 5m closes
had a range of 0.0859675%, below 0.12%, despite the current 1m breakdown.
Reference 75 had previously been sent at 62,580.1 and its 62,748.6 stop
was observed breached. That reference is not a winning trade or actual fill.

## Fixed changes

In capture mode only, a fresh closed native 1m breakout can remove the
flat-5m-close veto for the breakout strategy. It must clear both the frozen
setup level and the preceding 15 actual minute wicks, have a directional
body of at least max(0.025% of price, 0.65 minute ATR), directional 5/15-minute
close progress, and volume at least 1.1 times the prior 15-minute median.
The minimum level clearance is max(0.002% of price, 0.05 minute ATR).
All candles must be contiguous, valid, closed and current at observation.
Forming candles, current quotes alone, weak bodies, invalid volume and stale
or future data cannot grant the exception. The other context vetoes are
re-evaluated; this evidence alone cannot approve an entry.

The latest successfully sent reference may arm one same-direction retry
after its stop-breach notice is successfully sent. A new fully screened
breakout must use a completed minute starting after that stop notice and
advance beyond the earlier entry. Only then may the 45-minute notification
cooldown allow one retry. The daily cap still applies; a second retry inside
the original 45-minute episode is blocked. Armed/used state survives restart.
Existing risk, fees, stop distance, target-room checks, minimum net RR,
position sizing, reverse-direction limits and stage-two confirmation remain.
Stops are not widened to turn an earlier stop-out into a win.

## Stop-noise correction after reference 76

Reference 76 was sent at 61,406.3 with stop 61,487.0744, a distance of
80.7744 (0.13154%). Recorded completed 5m ATR was 184.1704. The old
capture policy could use only 1.5 times the shorter 1m ATR as its noise floor.
New capture-mode breakout/trend-retest stops use a floor of
max(0.05% of entry, 2 times completed 1m ATR, 1 times completed 5m ATR),
and remain beyond the original observed structural anchor and buffer.
This does not depend on the later 61,505.4 rebound. On the recorded 5m ATR
alone, the hypothetical minimum short stop would be 61,590.4704; the full
planner may place it farther or reject it for risk/target constraints.
No previously sent stop is changed. Position size is recomputed from the
same fee-inclusive loss budget; target room and net RR are recalculated.
The 0.60% stop cap, daily cap, margin cap and monetary loss budget remain.
The experimental structure_cost mode and the legacy/range policy remain.

## Verification and limits

Nine focused regressions cover both directions, false intrabar breaks,
invalid/future/stale bars, adverse higher context, unchanged risk rejection,
one-retry limits, daily caps and restart persistence. The entire LBank unit
suite is also required before deployment. A bounded read-only startup archive
captures public native candles, first-arrival times and recorded quotes for
paired incident review; it excludes credentials and account/position data.

Synthetic mechanism regressions are not a replay of live fills. Retained
quote observations cannot prove the first possible alert time, user receipt,
execution price, profitability or future accuracy. The independent RSI video
strategy and unrelated services are not part of this release.
