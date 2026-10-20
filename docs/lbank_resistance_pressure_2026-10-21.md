# Resistance and pressure observation deployment — 2026-10-21
User requested immediate check, correction and deployment. **Only descriptive observation was implemented/deployed; requested live entry decision correction remains incomplete.**

Current source25388b431c9c9d38df6a434670586c65f382d604, Railway deployment4bbda6c7-29e8-4811-8322-5f8d0574c1be terminalSUCCESS. Runtime at2026-10-20T15:23:35Z identifies exact commit and monitorSHA256 c8c7afd9cdcf45c3a90af5fa68ec94aa399b587c4c94a86f079c6c718b5c1b53. Native360one-minute/163five-minute ready. Observer emitted both directions15:23:36Z,readytrue/live_gatefalse/entry_overridefalse/strength_comparisonnull. Exact evidence in research/continuation/results/resistance_pressure_20261021_0015/runtime.json.
Existing source24non-run definitions match previous5c977a7 byte-for-byte; run only adds exception-isolated once-per-completed5m observer. No notification/order/risk/margin/entry-predicate changes. Previous deployment23799b11/source5c977a7 is rollback. Intermediate dependency commits ad349084/6541037 builds areREMOVED; correct final25388b4 verified live.

## Actual implementation
lbank_resistance_pressure.py:
- Retain nearest confirmed pivot levels even after quote crossing; record quote-crossed and completed5m-close-crossed separately.
- Existing .25ATR pivot-cluster radius; report clustered pivot count and median subsequent completed2bar rejection/ATR.
- Report signed3completed-bar progress/ATR and close-path efficiency, plus3bar mean totalvolume divided by preceding20bar median.
- All are unvalidated descriptive features. No fitted score, strength comparison, threshold permission or claimed absorption/aggressor flow. Total traded volume does not identify buy-vs-sell aggression.
- Fresh/quality-valid completed native states required, invalid/stale inputs produce no permission. No source state mutation. Observer emits once per5m, two directional rows; these are not paper trades or entry alerts.

## Tests and limits
355tests passed locally and server predeploy;5new tests cover invalid/stale/quality inputs, no permission, state nonmutation, directional sign symmetry and closed-bucket dedup.24existing non-run function definitions exactly unchanged. Missing local dependencies/fixtures restored from pinned5c977a7 rather than removed tests. Fixed22 descriptive vectors also generated with readyassertions; no weights fitted/outcome comparison or alpha test. These checks establish implementation scope, not financial accuracy.
Earlier single conflict-veto failed robustcost/runner checks and remains unpromoted. No validated formula now establishes current pressure exceeds resistance. User's requested completed live correction has NOT been delivered. Existing promotion requirements remain.
The actual missed-rally case remains priority: initial22:40KST81708 target-range rejection, then22:50/23:00barriers, then23:02extension rejection. Original stop/target-first replay and original full minute snapshots still missing. Do not infer profitable entry just from eventual83050 quote.

## Continuation
Prioritize actual earliest missed-rally input recovery and compare these exact descriptive features with failed breakthroughs using existing completed-prefix archives. Freeze complete candidate contract before performance comparisons; no ad-hoc weighted sum after seeing outcomes. Preserve the previously queued public4 episode task but do not let it displace this named user problem. Collect actual new native observation coverage/gaps; runtime between assistant runs is the deployed process, not continuous AI. Do not call this observer a completed trading alarm or a performance improvement.
