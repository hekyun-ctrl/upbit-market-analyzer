# v3.15: completed-breakout execution audit (2026-10-04 KST)

Base: `a0f5088ccc05eb497d964f9d905b233ce2a6826e` (v3.14).
Scope: independently qualified completed 5m WB structure entries. Public
data only; no orders and no manual production Telegram test message.

## What the observed records establish

At 21:41 KST, the v3.14 process started at 20:38 KST had 75 screening
records, 31 rejected evaluations and zero sent candidates. These are
restart-scoped counts, including repeated checks, not 75 different assets.
Overlapping reasons included resistance room (22) and WB/first support (21).
Later log retrieval contained 77 records and 32 rejected evaluations.

Two logged completed-breakout cases were reconstructed from official public
candles and logged near-quote notional depth. Full historical distant books
and actual user fills are unavailable. Source:
`https://api.upbit.com/v1/candles/minutes/{unit}` with explicit historical `to`.
Fixture: `tests/fixtures/structure-execution-20261004.json`. It contains only
bars completed at each decision cutoff. Later outcome labels are separate;
they are never input to signal evaluation.

| Case / decision KST | Completed 5m volume | Completed 15m / rolling alternative | Observed book | Interpretation |
|---|---:|---:|---|---|
| SAND 20:45 | 5.14x | 1.43x aligned | legal 104/105 one-tick spread 0.957%; nearby bid depth 157–172m KRW, ask 346–415m | Resting bid/ask ratios 0.38–0.50 do not establish absence of executed buys. Logged executed buy share 97.45%. |
| BAT 21:10 | 9.72x | 0.12x aligned / 1.18x rolling | legal 147/148 one-tick spread 0.678%; nearby bid 5.90m, ask 8.42–8.71m | Local ratio 0.68–0.70 has real depth despite the old >=1 majority threshold. Cost and structural risk must still pass. |

SAND's initial 103 KRW capture subsequently recorded MFE +3.88% and
15m return +2.91%. This is not the return available from the later 105 KRW
completed-breakout decision, a realized trade or evidence that all its
admission filters were wrong. Its completed hourly close was below MA20;
v3.15 records that specific remaining trend blocker. The hourly trend guard
has not been relaxed using the subsequent rise.

BAT's initial 147 KRW capture recorded 30m return -2.04%. Replay now passes
the local book and independent completed-breakout admission checks, but
still rejects at cost planning: net first-target R about 0.13 (<1.00).
The stop remains structural; it is not tightened artificially to force an
alert. This separates an obsolete book rejection from a valid risk rejection.

## Changes

1. **Clock correction:** Candle completion still uses the original snapshot
   cutoff. Executed-flow freshness uses a separate execution timestamp after
   data collection. Previously, live flow refreshed during the ~5s snapshot
   fetch could appear to come from the future relative to its start timestamp
   and silently fail fresh-flow checks. Future/stale data remain rejected.
2. **Local execution depth:** Each of three snapshots is centered on its own
   quotes. Both nearby sides require >=5m KRW. All near ratios must be >=0.2;
   the majority must be >=0.5. The old majority >=1 threshold is removed only
   in the independently proven 5m structure lane.
3. **Observed buying alternative:** Weak local imbalance may be supported by
   fresh `upbit_websocket_trade` data: exactly three windows, >=2 trades each,
   >=10m total KRW, executed buy share >=60%, last close holds current and
   is not below the first close. It is labeled buy-share evidence, never
   persistent buying. Missing source, stale/future data and heavy selling
   cannot qualify this alternative.
4. **Legal one tick:** A verified official one-tick spread may exceed the
   normal 0.5% spread up to the existing 1.2% hard bound. Depth, repeated
   snapshots, 60s survival, refreshed dispatch quote and net R>=1 are required.
   Nonlegal, multi-tick, thin and excessively wide books remain blocked.
5. **Resistance evidence:** A continuous equal-high plateau is one touch;
   a fully flat tape is not a swing peak. Separated peak episodes remain
   separate tests. Genuine 15m peaks and significant daily highs remain.
6. **Distance vs executable reward:** For an independently admitted 5m
   structure entry only, a close real resistance is sent to actual tick-rounded
   first-target/structural-stop and cost validation instead of a fixed 2.5%
   distance veto. Its price is not raised or erased. Gross first R>=the
   configured normal floor (2.00) and net R>=1.00 remain. Other routes retain
   their distance rules. Rounding a target to/below entry fails closed.
7. **Residual 1m gates:** An independently confirmed 5m breakout does not
   need a later 20s exposure candle, a second 1m WB event or a 1m MA20 check.
   The completed latest 1m and live price must still hold the breakout.
   Volume/shape are evaluated on the completed 5m event; gaps, chase, BTC,
   liquidity, higher trends, score penalties, structural stops and risk remain.
8. **Survival and delivery:** Recompute local book, flow and costs; refresh
   the quote at dispatch. Generic fast-lane R and breadth exceptions cannot
   leak into the structure lane. The final Telegram policy uses the same
   local-book eligibility. Messages distinguish observed buy share from
   persistent buying or missing actual-buy evidence.
9. **Diagnostics:** Record the independent structure gates and exact
   failed ones, as well as actual resistance levels/admission basis. A
   proven breakout blocked by higher trend is reported as such rather than
   solely as a missing first pullback.

## Validation

- Full suite: **590 passed**. Server import and `git diff --check` pass.
- 23 added tests cover two public replays, legal/local depth, actual flow
  freshness/source, thin/far/multitick/wide books, equal-high plateaus,
  preserved historical resistance, obsolete 1m gates, net-cost failure,
  independent risk-floor consistency and refreshed dispatch policy.
- A positive controlled case with resistance room <2.5% preserves its real
  target 103.3, first R 2.09 and net R 1.43. A negative close-resistance case
  still fails reward; the positive result is not a market backtest win.
- A controlled legal-tick case passes admission, survival and Telegram
  policy while preserving costs. Existing mock Telegram send tests pass.
- Baseline v3.14 had five failing tests. Two surfaced the established hourly
  retest path's missing unit-volume guards; those guards are restored alongside
  turnover checks. Three stale tests are updated to the deployed config defaults
  and properly dated completed 5m/15m fast-path data. Assertions are not removed.

## Operational evaluation and rollback

No increase in live accuracy, profit or alert frequency is claimed yet.
Evaluate later sent candidates and rejected opportunities at executable
prices, recording costs and first-touch ordering. MFE from the initial capture
must not be presented as the return obtainable at a later entry alert.
Counters are in-memory and restart on deployment; compare collection timestamps.

No production variable changes are needed; the already enabled
`CANDIDATE_COMPLETED_STRUCTURE_ENABLED=true` flag controls this lane.
Rollback to the preceding v3.14 deployment or disable this flag to remove
the route. Retain the causal clock correction and diagnostic explanation
when diagnosing future regressions.
