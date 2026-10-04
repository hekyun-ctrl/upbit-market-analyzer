# v3.11 — independent completed-structure entry

## Observed problem

On 2026-10-04, AXS and BLAST were discovered by the early-watch path but did
not become delivered candidates. AXS later reaccelerated; BLAST first fell
below the raw-signal stop benchmark and then formed a new breakout. A later
high is not proof that buying the initial alert would have succeeded.

AXS at 13:10 KST had a completed 5m volume ratio of 5.16786, aligned 15m
volume of 0.63101, and a three-completed-5m rolling volume ratio of 2.30603.
Its completed breakout candle closed at 1842 above the preceding 1807 high.
The close position was between 0.65 and 0.75. The v3.10 rolling alternative
was restricted to executed-buying confirmation and required a 0.75 close
position. The ordinary path could also reject a completed 5m breakout on
the separate 1m candle's shape and total-book imbalance.

## Change

`CANDIDATE_COMPLETED_STRUCTURE_ENABLED=true` enables an independent route:

- A fresh, completed 5m WB dual-band/prior-high breakout; volume >=1.5x its
  own previous 20 completed bars; close position >=0.65 and upper wick <=0.35.
- Aligned completed 15m volume >=1x, or the last three completed 5m volumes
  >=1x the preceding 20 **non-overlapping** three-bar blocks. The alternative
  needs 63 consecutive completed 5m bars; no forming-bar projection.
- Ten consecutive completed 1m candles and a 1m close/live price above the
  breakout. The 1m candle's shape is not treated as the 5m breakout's shape.
- Fresh relative strength in the top 2%, positive 5m/15m/60m momentum,
  intact 15m and 60m support and prices above their completed MA20s, and
  breadth >=20%. RSI >=95 and BTC weakness/crash still block entry.
- Three nearby book snapshots: meaningful KRW depth on both sides, normal
  spread, bid/ask notional >=1 in a majority and >=0.5 in every sample.
  Total-book imbalance alone cannot reject this separately proven route.
  Known fresh heavy selling still prevents admission; missing trade-side
  data is never described as actual executed-buying confirmation.
- A **new** plan anchored to the newly completed breakout, not an hours-old
  discovery price. The new stop uses its structural low, ATR and tick size.
  No existing candidate stop is widened; configured stop-width, real
  resistance, first-target R:R and modeled net-cost floors remain intact.
- At least 60 seconds of survival, with refreshed structure, volume, higher
  trend, nearby depth, relative strength and BTC checks. A final live quote,
  fresh survival proof and cost/R:R recheck are mandatory before dispatch.

The initial-discovery watch already lasts 12 hours. A deeply invalidated
first dip stays invalid, but a fresh high may now enter a separate completed
breakout recheck when enabled. It is not labeled a first retest. Duplicate
screens remain limited to one per symbol/5m bucket and share the inflight
guard. Existing contracting-first-retest and executed-buying routes remain
separate; their guards are unchanged.

## Validation

- `python -m pytest -q`: **564 passed**, including all previous 519 tests.
- Actual public-candle replays are cut at each evaluation time. They cover
  BLAST's 09:05 breakout, 09:10 failure and 09:25 renewed breakout, plus
  AXS's 12:20 insufficient combined volume and 13:10 rolling-volume proof.
- Missing bars, 62-bar history, weak volume, lost support, wick, thin/distant
  books, extreme RSI, BTC decline, breadth, liquidity, chase and structural
  stop failures stay rejected. Stale survival/quote evidence and cost failure
  cannot pass Telegram delivery. A mocked Telegram send verifies the new
  message without sending a manual production message.
- `import server` confirms MCP startup-module compatibility.

Historical candles verify structure only. They do not reconstruct historical
fills or orderbooks, and passing a replayed structure does not guarantee that
the complete historical entry plan would have passed risk and execution.
Evaluate delivered notifications by target/stop first-touch, drawdown, costs
and lead time; do not substitute raw-signal hypothetical results for actual
candidate success. Runtime counters reset on deployment and identify their
collection start.

## Rollout

Pre-deployment baseline (2026-10-04 14:10 KST, v3.10 collection started
2026-10-03 08:12:53 UTC): 0 delivered candidates; 29 early-watch starts,
26 completed watches, 20 decided +5%/-3% benchmark outcomes (7 target-first,
13 stop-first), and 7 target-first watches without candidate approval.
These are hypothetical early-watch benchmarks, not realized trading results.

The new route defaults off in code. Enable the non-secret service setting
above for the tested production release. Disabling that setting removes the
new route and invalidated-watch recheck; previous paths remain available.
Rollback base: `3e0846e` (v3.10). Verify Railway's deployed commit, public
`get_monitor_status` version, WebSocket connection and Telegram configuration.
