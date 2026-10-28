# P2 E4 nearby-barrier exception / chase ablation

**Research checkpoint complete; whole feature and profitability remain unproven.** These are reused signal-adjacent development observations, not unseen or continuous evaluation. No sends, orders, production merge or deployment.

Predeclaration/sole lease: `116f43b7a89a5c6a6080b23062f703a3f656010e`. Initial code: `9b7aa8a441455d07cf60c3477dc6ed223745e706`. Source checkpoint `7c8fb9b2f1c6a0b74f7b2de41a0f0141c73844c3`; exact UTC start/end and verified heads are in the single completion state/handoff. No extra worker, replacement model or automation was created.

## Isolated change

E4 calls PR21 `location_check` unchanged inside `calculate_plan` at candidate and confirmation. Breakout distance from original level must be (0,1.5] completed native 1m ATR; trend-retest distance from structural anchor must be (0,2]. Range-rebound location handling is unchanged. E1 frozen first-retest proof is retained for its original trigger/pattern/expiry checks at confirmation. This uses the existing current-ATR check; paired-price attribution separately uses one discovery ATR for both entries.

Only inside plan calculation, ranked `may_override_single_pivots` is set false. A known unbreached nearby barrier cannot be bypassed by aligned 15m/5m direction. Other priority fields and exceptions, target algorithms, RR1.5, quantity/risk/margin/costs, capture momentum/context/RSI, +/-0.1% watch quote rule, TTL, cooldown and max alerts are unchanged. Unknown barriers keep each original target panel behavior: legacy required-price targets can remain; P1 already rejects `room_unknown`. E4 does not extend targets or tighten structural stops to force entries.

No location mode, momentum bypass, extra confirmation progress, detector or scheduler is enabled by E4. E3 wait removal/sticky original-stop invalidation and E1 detection occur only in their declared arms. Production monitor, PR21 location implementation, P1, E1/E3 modules and entry-priority/quality code remain byte-identical. 1h/4h remain reference only.

## Same-data results

16 arms = two frozen target panels × E1 on/off × E3 on/off × E4 on/off; 3 cost/availability scenarios and 8 paired E4 contrasts. All 24 original full control arm-scenario window outputs exactly reproduce stored E1/E3 results. Repeated observations and delay/cost variants are not independent trades.

|Scenario|Control → E4|Raw candidate families|Final alerts|Matched / excluded / added|E4 stop-first / unknown|E4 total model net / closed DD USDT|
|---|---|---:|---:|---:|---:|---|
|base_delay0|capture → capture_e4|67 → 67|3 → 1|0 / 3 / 1|0 / 1|null / null|
|base_delay0|capture_e3 → capture_e3_e4|67 → 67|5 → 2|1 / 4 / 1|1 / 1|null / null|
|base_delay0|structural_p1 → structural_p1_e4|67 → 67|0 → 0|0 / 0 / 0|0 / 0|0.0000000 / 0.0000000|
|base_delay0|structural_p1_e3 → structural_p1_e3_e4|67 → 67|0 → 0|0 / 0 / 0|0 / 0|0.0000000 / 0.0000000|
|base_delay0|capture_e1 → capture_e1_e4|77 → 77|3 → 1|0 / 3 / 1|0 / 1|null / null|
|base_delay0|capture_e1_e3 → capture_e1_e3_e4|77 → 77|5 → 2|1 / 4 / 1|1 / 1|null / null|
|base_delay0|structural_p1_e1 → structural_p1_e1_e4|77 → 77|0 → 0|0 / 0 / 0|0 / 0|0.0000000 / 0.0000000|
|base_delay0|structural_p1_e1_e3 → structural_p1_e1_e3_e4|77 → 77|0 → 0|0 / 0 / 0|0 / 0|0.0000000 / 0.0000000|
|base_delay15|capture → capture_e4|53 → 53|2 → 1|0 / 2 / 1|0 / 1|null / null|
|base_delay15|capture_e3 → capture_e3_e4|53 → 53|4 → 2|2 / 2 / 0|1 / 1|null / null|
|base_delay15|structural_p1 → structural_p1_e4|53 → 53|0 → 0|0 / 0 / 0|0 / 0|0.0000000 / 0.0000000|
|base_delay15|structural_p1_e3 → structural_p1_e3_e4|53 → 53|0 → 0|0 / 0 / 0|0 / 0|0.0000000 / 0.0000000|
|base_delay15|capture_e1 → capture_e1_e4|59 → 59|2 → 1|0 / 2 / 1|0 / 1|null / null|
|base_delay15|capture_e1_e3 → capture_e1_e3_e4|59 → 59|4 → 2|2 / 2 / 0|1 / 1|null / null|
|base_delay15|structural_p1_e1 → structural_p1_e1_e4|59 → 59|0 → 0|0 / 0 / 0|0 / 0|0.0000000 / 0.0000000|
|base_delay15|structural_p1_e1_e3 → structural_p1_e1_e3_e4|59 → 59|0 → 0|0 / 0 / 0|0 / 0|0.0000000 / 0.0000000|
|adverse_delay0|capture → capture_e4|65 → 65|1 → 0|0 / 1 / 0|0 / 0|0.0000000 / 0.0000000|
|adverse_delay0|capture_e3 → capture_e3_e4|65 → 65|0 → 0|0 / 0 / 0|0 / 0|0.0000000 / 0.0000000|
|adverse_delay0|structural_p1 → structural_p1_e4|65 → 65|0 → 0|0 / 0 / 0|0 / 0|0.0000000 / 0.0000000|
|adverse_delay0|structural_p1_e3 → structural_p1_e3_e4|65 → 65|0 → 0|0 / 0 / 0|0 / 0|0.0000000 / 0.0000000|
|adverse_delay0|capture_e1 → capture_e1_e4|75 → 75|1 → 0|0 / 1 / 0|0 / 0|0.0000000 / 0.0000000|
|adverse_delay0|capture_e1_e3 → capture_e1_e3_e4|75 → 75|0 → 0|0 / 0 / 0|0 / 0|0.0000000 / 0.0000000|
|adverse_delay0|structural_p1_e1 → structural_p1_e1_e4|75 → 75|0 → 0|0 / 0 / 0|0 / 0|0.0000000 / 0.0000000|
|adverse_delay0|structural_p1_e1_e3 → structural_p1_e1_e3_e4|75 → 75|0 → 0|0 / 0 / 0|0 / 0|0.0000000 / 0.0000000|

Base0 E4 excludes all3 original stop-first events at the chase/location check and adds1 unresolved event. Base15 excludes2 stop-first events and adds that reused unresolved event. E3+E4 base0 excludes3 stop-first +1 unresolved, adds1 stop-first and retains1 unresolved; base15 retains1 stop-first +1 unresolved and excludes1 stop-first +1 unresolved. These are development-path changes, not independent profitable outcomes.

A zero-entry arm has model PnL0 and closed-trade DD0; its unresolved ratio is null because the denominator is0. An entered unresolved trade and policy total/closed DD remain null. Emergency exits and full mark-to-market DD are unmodeled/null. Reduced alarms and omitted losing examples alone do not establish utility, precision or profitable economics. Resolved-only subtotal must not replace a null policy total.

### Recorded E4 checks

These are executed evaluations and exact-event counts; other gates can preempt E4. Unexecuted checks remain null. Candidate/confirmation/alert-limit/watch-scheduling reasons and unique-event flow for every arm are in `e4_gate_accounting.json` and the full decision CSV.

|Scenario|E4 arm|Executed / not evaluated|Location failures / unique events|Native-barrier rejections / unique events|
|---|---|---:|---:|---:|
|base_delay0|capture_e4|49 / 26|30 / 24|5 / 5|
|base_delay0|capture_e3_e4|47 / 23|28 / 23|5 / 5|
|base_delay0|structural_p1_e4|45 / 23|28 / 23|0 / 0|
|base_delay0|structural_p1_e3_e4|45 / 23|28 / 23|0 / 0|
|base_delay0|capture_e1_e4|56 / 29|29 / 25|9 / 9|
|base_delay0|capture_e1_e3_e4|54 / 26|27 / 24|9 / 9|
|base_delay0|structural_p1_e1_e4|52 / 26|27 / 24|0 / 0|
|base_delay0|structural_p1_e1_e3_e4|52 / 26|27 / 24|0 / 0|
|base_delay15|capture_e4|38 / 21|24 / 19|3 / 3|
|base_delay15|capture_e3_e4|37 / 18|22 / 18|3 / 3|
|base_delay15|structural_p1_e4|35 / 18|22 / 18|0 / 0|
|base_delay15|structural_p1_e3_e4|35 / 18|22 / 18|0 / 0|
|base_delay15|capture_e1_e4|44 / 21|23 / 20|7 / 7|
|base_delay15|capture_e1_e3_e4|43 / 18|21 / 19|7 / 7|
|base_delay15|structural_p1_e1_e4|41 / 18|21 / 19|0 / 0|
|base_delay15|structural_p1_e1_e3_e4|41 / 18|21 / 19|0 / 0|
|adverse_delay0|capture_e4|44 / 22|28 / 23|1 / 1|
|adverse_delay0|capture_e3_e4|44 / 22|28 / 23|1 / 1|
|adverse_delay0|structural_p1_e4|44 / 22|28 / 23|0 / 0|
|adverse_delay0|structural_p1_e3_e4|44 / 22|28 / 23|0 / 0|
|adverse_delay0|capture_e1_e4|51 / 25|27 / 24|1 / 1|
|adverse_delay0|capture_e1_e3_e4|51 / 25|27 / 24|1 / 1|
|adverse_delay0|structural_p1_e1_e4|51 / 25|27 / 24|0 / 0|
|adverse_delay0|structural_p1_e1_e3_e4|51 / 25|27 / 24|0 / 0|

The new base0 E3+E4 stop-first event was already a qualified baseline breakout candidate, but the baseline selected an earlier same-direction trend-retest candidate. E4 rejects that trend-retest on location, so the existing strategy-priority path selects the breakout instead. No failed comparator confirmation is invented. The E4-only added unresolved event also reflects gate-order/state interaction: at its first baseline confirmation the final breakout-freshness guard cancels the watch; E4 first fails the location check, retains the existing watch under unchanged scheduling and later confirms when the location bound passes. This is not a new detector or proof of better timing.

In this sample zero plan evaluations surviving the E4 location check had an originally true single-pivot override. The logged native-barrier rejection counts therefore cannot be presented as new removals caused by disabling that exception; chase preemption masks that part of E4. Symmetric synthetic tests separately prove the exception is disabled when reachable.

### Timing and follow-up

Exact source anchors identify common events; unmatched anchors do not receive invented counterpart times. `trades.csv`, `event_accounting.json` and `trade_cards.json` retain every common/excluded/added trade, observed gate failures or explicit absence of a comparator confirmation, TP/SL first contact, follow-up gaps and5/15/30/60-minute marks. These marks assume unmanaged holding and are not realized profits after the benchmark exit.

Confirmation old-minus-new seconds by paired contrast (positive = new earlier):

- base_delay0, capture_e3_vs_capture_e3_e4: `[0.0]`
- base_delay0, capture_e1_e3_vs_capture_e1_e3_e4: `[0.0]`
- base_delay15, capture_e3_vs_capture_e3_e4: `[0.0, 0.0]`
- base_delay15, capture_e1_e3_vs_capture_e1_e3_e4: `[0.0, 0.0]`

Candidate lead and known-missing availability are in `opportunities.csv`/`candidate_accounting.json`. `fixed_plan_attribution.json` freezes the earliest actually observed confirmed S/T for both actual entry quotes; this is retrospective timing attribution, separate from actual policy paths. `e4_gate_accounting.json` also gives directional price worsening with one common discovery ATR; no later shrinking ATR is used to flatter distance.

Each card preserves30/60-second manual-delay scenarios using the next retained quote within90seconds, fixed original S/T and risk/margin caps. No ticks are interpolated. These test fixed-plan RR permissibility and are not an independent trade count or proof of future E4 gate execution/manual fills. Missing subsequent quote/outcome remains null.

## Fixed costs and data limits

Base costs: taker6bp each side, stop-slip2bp, entry funding reserve1bp. Adverse costs:8bp each side, stop-slip5bp, reserve3bp. Reference balance1871.9017USDT, risk0.5%, margin15%, leverage30 are retained model assumptions. Actual entry/TP slippage, spread, personal fee tier and funding settlements are unverified. P1 quantity/tick/minimum metadata remains the previous un-reverified public snapshot assumption.

Nominal bar availability0/15seconds is an explicit offline scenario; actual historical receive timestamps/known_at remain null. Retained ticker quote times are observation proxies, not fills or Telegram/device delivery. No production latency claim is made. Same-feed 1m/5m transformations are not independent. `RAW5M_INDEPENDENCE_UNVERIFIED` and `CANDLE_VOLUME_UNIT_UNVERIFIED` remain; verified closed/native-independent bars, funding-history days and unseen evaluation remain absent. No old HTTP/WS probes were repeated or verification flags relaxed.

PR21 bundled location remains a distinct preserved row, not an E4 arm: stored P0 nominal0s capture3 / location1 (new location outcome unresolved),15s capture2 / location1 unresolved. That bundle also changes detection, momentum/confirmation behavior and must not be attributed to E4.

## Validation and reproduction

58 related tests passed including14 new E4 synthetic tests; one separately added exact inclusive-boundary test passed (15 new total,59 distinct related tests). Synthetic prices establish behavior only. Core changed-path tests and all eight factor names cover symmetric chase, barriers, unknown-room separation, E1 proof expiry, scope restoration, original age/default wait, preempted gates and no dispatch intent. P0 15/legacy467 were not repeated.

```bash
PYTHONPATH=/workspace/scratch/44f97b78c955/zb-test-deps python -m unittest test_lbank_barrier_chase_experiment test_lbank_first_retest_experiment test_lbank_confirmation_experiment test_lbank_entry_location test_lbank_structural_comparison -q
PYTHONPATH=/workspace/scratch/44f97b78c955/zb-test-deps python -m unittest test_lbank_barrier_chase_experiment.E4Tests.test_exact_location_limit_is_inclusive -q
PYTHONPATH=/workspace/scratch/44f97b78c955/zb-test-deps python research/compare_barrier_chase_experiment.py
PYTHONPATH=/workspace/scratch/44f97b78c955/zb-test-deps python research/render_barrier_chase_report.py
python research/validate_barrier_chase_experiment.py
git -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol diff --cached --check
```

`experiment_manifest.json` is the committed predeclaration; `manifest.json` covers code/inputs/full outputs and deterministic gzip plus uncompressed decision SHA256. `report_manifest.json` covers derived accounting/attribution. `validation.json` covers the complete artifact inventory, original-code equality,24 controls and zero/null semantics. Project requirements provide dependencies; the recorded PYTHONPATH reuses the already available ccxt4.5.90 test environment, with no live exchange calls.

## Remaining work

E5 early-reversal route and its necessary factor combinations; failure-isolated live EvidenceLedger integration/migration/restart/outage/bounded retention; P3 truly received continuous/unseen same-data comparison and source qualification; P4 evidence review and P5 separately authorized application. No parameter winner was selected from reused outcomes, no RR/cost/target tuning, no new100paper/30day prerequisite. This checkpoint is not whole completion or production qualification.

The default staged whitespace check flags standard CSV CRLF line endings (exit2). The explicit CRLF-aware staged check passes without changing any strategy or artifact bytes. This diagnostic is recorded in validation commands.
