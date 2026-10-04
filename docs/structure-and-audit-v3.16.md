# v3.16: independent price structure and durable audit

Base: production v3.15, `984c86de7c80421c8404b0eb184cee64fdda3482`.
Decision date: 2026-10-05 KST. This is a functional repair, not a demonstrated
increase in profitability. Public quotes and simulated outcomes only.

## Observed bottleneck and causal checks

The initial-capture and entry pipelines must be evaluated separately. A
reported zero candidates is not successful filtering. A zero missed-winner
count with zero completed observations establishes no recall result.
Reboots previously discarded events and reset the cohort.

The new fixture `tests/fixtures/structure-route-20261005.json` keeps original
screening timestamps and official historical candles separate from later
outcomes. Source: https://api.upbit.com/v1/candles/minutes/{unit}. Forming bars
are removed at each original cutoff. No later return enters admission.
Historical full orderbooks and actual user fills are unavailable; these
replays establish structure evidence, not executable counterfactual profits.

| Historical decision (KST) | Completed structure result | Interpretation |
| --- | --- | --- |
| SUI 10/05 00:45 | Price close 1,702 > prior 20-bar high 1,693; 5m volume 2.154x, aligned 15m 3.777x | Old fast WB upper still above close. Independently verify price breakout; do not label WB confirmed. |
| AKT 10/05 00:40 | Historical impulse followed by first support episode; contraction verified | Structure qualifies; breadth, local depth, trend and costs remain separate possible blockers. |
| BEAM 10/05 00:57 | Structure/volume fail | Remains excluded. Subsequent loss is not needed to make the decision. |
| POD 10/05 01:27 | Latest close below prior high; insufficient 5m/15m activity | Remains excluded. |
| XLM 10/05 00:35, W 10/04 23:14 | Missing minute in required completed ten-minute sequence | Real public-bar gap remains blocked; no synthetic candles. |

SUI's initial 1,629 capture later observed MFE +5.16%, but this is not the
return obtainable at the later 1,702 structure decision. SAND, W, BEAM and
IOTA initial observations also subsequently lost value. Neither rising
screenshots nor initial-capture acceleration alone establish an entry edge.

## Implementation

- A completed green 5m close above the preceding 20 highs can confirm a
  price breakout without clearing a rapidly moving Bollinger upper. WB
  confirmation is separately truthful. Latest completed 5m volume >=1.5x,
  aligned 15m >=1x or the existing causal 63-bar alternative, candle shape
  and ten consecutive completed one-minute bars are still required.
- A first-support route searches the preceding three completed impulses.
  Each impulse must pass its own past-volume baseline and 15m confirmation.
  The first contact episode must hold the actual old high; a broken,
  repeated or remote episode is ineligible. All following volume must be
  positive and below the impulse, latest volume >=0.5x its previous 20 bars,
  and latest 15m activity remains verified. The general impulse-volume gate
  no longer contradicts this independently qualified contraction.
- The price proof replaces WB evidence points and its absence penalty only
  after every independent structure/execution gate passes. It never adds
  both bonuses or covers missing requirements. Existing score floor and
  explicit market, spread, resistance and heat deductions remain.
- Admission, survival and final Telegram policy all accept the same proof
  mode. Recompute completed structure, close-to-price depths, costs and
  current quote. No single quote proves 60s survival or actual buying.
- Preserve rank/momentum, intact 15m/hourly trend, BTC crash rejection,
  liquidity, 60s confirmation, real resistance prices, structural stops,
  gross first-target R >= configured 2.0 and net R >=1.0. No artificial stop
  tightening or fabricated resistance target to manufacture candidates.
- Failed structure logs name missing history, continuity, volume and shape;
  absent close data is unknown, not a fabricated chase failure.

## Durable and interpretable performance

SQLite WAL on the persistent `/data` mount stores screening records and
candidate/raw/initial/trend outcomes. Retain 30 days; do not infer an account
position. Daily/API audits read retained records rather than a deque tail.
Daily delivery count, candidate cooldown and last daily-report date restore.

Initial-capture outcomes belong to the initial detection day, even when the
two-hour observation finishes after midnight. Pending and restart-interrupted
cohorts are explicit. Unfinished tracks across a restart are not assigned a
target/stop first-touch result: public prices during the gap cannot establish
ordering. Missing completed samples display "판정 가능한 완료 표본 없음".
Collection start and storage failures are visible. Previously erased records
cannot be fully recovered; durable history begins with this deployment.

## Verification and rollback

Full suite: **610 passed**, including 20 added public-bar, independent-path,
dispatch and durable-audit regressions. Existing assertions retained. The
controlled non-WB case passes admission, fresh survival and final dispatch
policy without fabricated WB/buy-flow evidence. A contracted first dip below
the 1.5x impulse threshold passes its dedicated path; corrupted volume/anchor
evidence fails. Durability tests reopen the database, test duplicate concurrent
writes, cohort-day attribution, interrupted observations and storage failure.

Evaluate forward sent candidates with timestamped executable prices, costs,
first-touch ordering and unresolved counts. More alerts or higher test counts
are not evidence of a profitable strategy. Roll back code to v3.15 if needed;
keep the persistent volume and never delete its audit records. Disabling
`CANDIDATE_COMPLETED_STRUCTURE_ENABLED` removes both new entry modes.
