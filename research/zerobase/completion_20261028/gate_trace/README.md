# Actual gate execution and independent continuous comparison — inactive research checkpoint

Original acceptance: [trade-evidence design](../../../../docs/zerobase/TRADE_EVIDENCE_ALERT_DESIGN_20261025.md), sections4–10, especially P3. This implements the next finite mechanics unit. **The whole feature, qualified unseen P3 comparison, P4 promotion and P5 application remain incomplete. No production merge/deployment, runtime activation, Telegram/ZB sends or orders.**

## What changed

`lbank_gate_trace.py` observes the **original directly called** classify, confirmation and alert-gate functions. It records actual Python helper call/return occurrences, reason fields, source hashes, source lines reached, exact return-site/containing conditions, selected economic values, original argument hashes and the actual allowlisted risk/cost configuration. It does not run a check again to explain it. Six common gate groups retain individual actual calls, including multiple candidate/override paths. Uncalled groups are `not_evaluated`; a capped/failed trace says `trace_incomplete_not_observed`. An earlier return and its actual reason/site explain where control stopped.

A reached source line is not proof that every operand of a short-circuit expression ran. Individual operand booleans are deliberately unavailable; no unexecuted predicate is called passed or failed. Indicator/cost utility calls and returns are retained, while their internal calculation loops/comprehensions and the evidence infrastructure are excluded from line coverage. Limits are2,048 calls/40,000 line events. Existing thread tracers are preserved, failures never retry a policy, and the optional sidecar remains disabled by default. Trace payloads go only to the sidecar; the existing policy diagnostics/performance log payloads stay unchanged. Every one of the12 retained live-envelope fixtures fits the existing512KiB producer limit.

Public quotes can now retain the actual request/CCXT response boundary and a sanitized normalized public response. Its hash is bound to the exact snapshot quote and evaluation, and a missing/future/changed reference rolls back. This is **not raw exchange wire, fill evidence or historical quote receipts**. Only synthetic unit fixtures exercise the new boundary; no public request was made. The original candidate→confirmation→alert-eligibility record chain can be linked immutably to a frozen plan; economic field/chain revisions reject atomically. Legacy absent links remain unavailable.

`research/continuous_comparison.py` incrementally runs the **existing frozen retained-replay schedule**: classify, confirm an existing watch, then arm a candidate; E3 discovery confirmation is selected from the original mixin. This is not a claim that the complete deployed polling schedule has been replayed. Each policy has its own durable watch, daily/global/reversal cooldown, original event IDs, sticky E3/E5 invalidation and frozen plans. Source observation, all policy steps, decisions/plans and cursor commit together. Repeated identical observations are idempotent; sequence/time/revision gaps reject. Existing research adapters are serialized and restored in a separate offline process, never installed in the live process.

Manifest identity includes source/code/cost/policy definitions. In-run mutation rejects; a separately declared cost scenario has a distinct identity. The source observation is shared, not cloned into independent market samples. The runner defaults to100,000 observations and reuses the existing64MiB main/WAL nondeleting guard; exhaustion or a pinned reader pauses analysis, and an atomic oversized step rolls back. Main+WAL can temporarily approach twice the main bound plus SQLite overhead during one transaction, as in the existing sidecar. No automatic deletion/rotation was introduced.

All no-signal and unavailable observations remain in the step table. Original fixed six-hour half/half followups and first-contact evaluation are reused. Frozen plans never change; missing followups are unresolved/null. A valid observed policy path with no entries has model PnL0 and an undefined unresolved ratio; a wholly unqualified/unavailable comparison has PnL null, not a false zero. Emergency and full intraperiod MTM drawdown remain null without their required observations. The report includes every exact-anchor matched/excluded/added/unmatched plan, policy reasons and comparable timing/entry differences; it never matches by a convenient time/price window.

## Evidence and results

The [predeclaration](experiment_manifest.json) was persisted at `2a3bf170487a572b0a0779c77cc9d711ff63c1bb` before the finite comparison. This was a changed-path mechanics regression on reused development material, **not another96-arm experiment, holdout or improvement test**.

- 12 retained native-state observations (the same first6 ready plus6 original candidate observations from the prior sidecar test): all original policy outputs and input states are exactly equal with tracing, all12 traces complete,6 prior qualified candidates. Existing archives/quotes are referenced by exact hashes; their historical receive times stay null.
- 20 consecutive retained observations from `signal15`, 2026-10-20T03:25:00Z inclusive to03:46:00Z exclusive: capture/P1 states and every step/report equal after reopening SQLite following observation10. Their economic signals, watch/expiry counts and observation counts equal the existing unchanged replay on **only this finite interval**. SQLite integrity and foreign keys pass.

| Reused finite interval | capture | P1 |
|---|---:|---:|
| Observations |20|20|
| Frozen entries |1|0|
| First observed contact SL |1|0|
| Model net USDT |−9.35927984656063|0|
| Closed-trade model drawdown USDT |9.35927984656063|0|
| Unresolved ratio |0|null (no entries)|
| Emergency/full MTM drawdown |null|null|

The single original capture plan is exactly excluded by P1 on the common anchor. No common final entry exists here, so final lead/entry improvement is null. The one known stop is already part of prior development results; P1's zero is no-entry accounting and **does not prove improvement**. Original E1/E3/E4/E5 full development comparisons, manual30/60-second assumptions and nominal0/15-second variants remain preserved at their prior immutable commits and are not rerun or combined as independent trades. Fees6bp/side, stop slippage2bp and assumed funding1bp remain the original base-model assumptions; adverse prior scenarios are8bp/5bp/3bp. Actual funding history0 days and fill/spread/slippage validation remain absent. No new manual-delay outcome or receipt delay was invented.

The same20 observations presented to `purpose=unseen` are all blocked: valid observations0, all policy comparison economics null, qualified unseen observations0. The current source qualification function has no flag that clears `RAW5M_INDEPENDENCE_UNVERIFIED` or `CANDLE_VOLUME_UNIT_UNVERIFIED`. Synthetic final/snapshot/receipt assertions cannot qualify it.

[Complete trace cases](retained_trace_cases.json.gz), [all40 policy-observation steps](continuous_steps.json.gz), [source index](continuous_input_index.json), [comparison/trade cards](continuous_report.json), [original finite controls](continuous_original_controls.json), [unseen rejection report](unseen_qualification_report.json), [checks](retained_checks.json), [manifest/commands/hashes](manifest.json), [validation](validation.json).

117 related tests pass, including28 new tests. New checks cover exact executed/unrun/return-site reporting, tracer/callback/cap failure isolation and single-call behavior, quote/decision-chain identity and atomic rollback, independent policy state, restart/idempotency/revision/cursor rollback, frozen manifests/plans, unresolved versus no-entry and nondeleting capacity failure. The prior related receipt/native/sidecar/default30s regressions are included because their paths changed. Unchanged P0 tests15, legacy467, full96-arm replay and identical HTTP/WS probes were not repeated.

Initial failed attempts are retained: an overly broad imported fixture duplicated12 discovered tests and one test originally confused a separately identified cost scenario with a mutation of a running manifest; production AST checking initially could not see an evaluation wrapper, fixed by retaining direct original calls; the first qualified real trace reached the bounded line cap, fixed by preserving utility call/returns while excluding calculation loops and using the stated bounded caps. Final source guards exclude evidence-infrastructure tracing. None changed strategies or tuned outcomes. [Frozen verification](frozen_strategy_verification.json) confirms all23 unchanged production strategy/helper function ASTs and24 frozen P1/E1/E3/E4/E5/separate-ZB/config/result blobs. The four actual immutable PR22 design/P0 files match.

## Original-design acceptance and finite remaining work

| Original acceptance | This checkpoint |
|---|---|
| Record actually run gates and leave unrun checks unavailable | Implemented/tested for original policy calls with exact return/source/call evidence. Individual short-circuit operand values are not inferred. No deployed-runtime trace claimed. |
| Reproducible receive/final/source/quote provenance | New normalized quote receipt/decision-chain mechanics implemented; retained historical quote receipts0, verified official final bars0, real raw-to-snapshot bindings0. Full derived-input receipt coverage stays false. |
| Independent policy state on the same continuous data, including no signals | Incremental retained-replay orchestration/restart/followup/report mechanics implemented; finite20-observation regression only. Actual qualified unseen continuous comparison remains blocked. |
| Source interpretation and costs | Both original source blockers remain; chart1m/5m same feed is not independent. REST403/forming frames are not re-probed or promoted to official final/volume/funding evidence. |
| Bounded failure-isolated storage/retention | Reuses nondeleting sidecar bounds; capacity/rollback tests pass. Operator-reviewed archive rotation/prune/runtime operation still unperformed. |
| P4 qualified promotion / P5 authorized apply | Not qualified/not performed. Original criteria unchanged;1h/4h remain reference only. |

Remaining implementation is finite and tied to P3: reviewed official source decoding/derived-input receipt linkage and feeding the original live schedule into the comparison path once exact qualified data exists; then the predeclared unused continuous comparison and original P4 review. Actual data waits on documented final-bar/volume semantics, independently published native5m alongside1m with closed-rollover/reconnect receipt coverage, and actual funding/fill/spread evidence. Do not manufacture timestamps or call the six retained forming frames qualified. Reviewed retention operation and P5's explicitly authorized application remain distinct. No profit threshold was relaxed, and no100-paper/30-day prerequisite was added.
