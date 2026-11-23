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
