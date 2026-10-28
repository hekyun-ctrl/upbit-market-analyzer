# P2 E1 detection-only × E3 comparison

Research checkpoint on reused signal-adjacent development observations, **not unseen/continuous validation, profitability proof or production activation**. Source checkpoint `f2599841c0d400bd10e1527508f9dc03ad207ce4`; sole lease and predeclaration were published before execution in `be8191e9f9280acd207ce9c95ae966c754b6b2d8`. Initial adapter/test checkpoint: `546bd05a573761fd9adedd76f2bd45da81370663`.

## Fixed experiment

`experiment_manifest.json` declares eight separate arms: capture/P1 target panels × original/E1 detection × original/E3 confirmation. Each arm has independent watch/cooldown/daily state. Six comparisons isolate detection at each confirmation setting, and confirmation at E1. All four original capture/P1/E3 arms, in all three scenarios, exactly reproduce saved E3 economic window outputs. No winner is selected from these reused outcomes.

E1 reuses `lbank_entry_location.first_retests` byte-for-byte from PR21. Its completed structure or actual quote proposal precedes same `(strategy,direction)` capture fallback, using the existing classification deduplication priority. The detector's native closed minute validation, 10-bar breakout, 0.5 ATR impulse/retest bound, 0.25 ATR retest upper bound, three following bars and 1.5 ATR resumption bound are unchanged. Real current quote is separate from completed bars. Source-anchored event IDs are reused; approximate price/time matching is forbidden.

**This arm changes detection only.** It keeps capture's accumulated retest-momentum check (including its existing priority exceptions), context, RSI direction, stop, geometry, targets, costs, RR1.5, chase limits, TTL and original 30s confirmation. PR21's bundled `location` mode, momentum bypass, extra location checks, target-barrier exception removal and confirmation progress are not enabled. E3 combinations separately reuse the already frozen readiness/actual-age/sticky-stop implementation. P1 algorithms and tick/quantity assumptions are unchanged. E1 is not a completed implementation of every PR21 or early-reversal behavior.

Target algorithms, costs and risk are frozen per panel; actual discovery-time plans remain those observed by each arm. `fixed_plan_attribution.json` separately freezes the earliest observed confirmed S/T and shared capped quantity to both observed entry quotes, avoiding a later plan at an earlier time. No independent next barrier or extended target is invented. 1h/4h stay reference-only.

## Results

| Scenario | capture → E1 raw bar families | capture / E1 alerts | E3 / E1+E3 alerts | E1+E3 SL / unresolved | All P1 combinations |
|---|---:|---:|---:|---:|---:|
| Existing cost, nominal delay0 | 67 → 77 | 3 / 3 | 5 / 5 | 3 / 2 | 0 entries |
| Existing cost, nominal delay15s | 53 → 59 | 2 / 2 | 4 / 4 | 2 / 2 | 0 entries |
| Adverse cost, nominal delay0 | 65 → 75 | 1 / 1 | 0 / 0 | 0 / 0 | 0 entries |

The E1 final alert plans, prices, timestamps and outcomes equal their corresponding original detector arm in all 12 arm-scenarios; raw candidate and rejection outputs differ. Detection changes are real; they do not pass existing qualification in this sample. E1 has **zero first-retest qualified evaluations and zero first-retest final alerts**. Base0 first-retest evaluations:14 over12 exact anchors, including2 live-quote and12 closed-resumption evaluations. Rejections:10 risk/target,3 retest momentum,1 RSI. The10 risk/target details are5 native barrier conflicts,2 native EMA extensions and3 targets outside observed range. With P1, the corresponding details are8 room-insufficient and2 EMA extension, plus the same momentum/RSI failures. Base15 has10 evaluations over8 anchors:9 risk/target,1 RSI; these are repeated scenarios of the same sources.

Base0 E1 has61 unique raw candidate events versus52 capture:52 common,9 E1-only,0 capture-only. Base15 has46 versus40:40 common,6 E1-only,0 capture-only. Adverse has59 versus50:50 common,9 E1-only. For common candidates, all signed old-minus-new leads are0 except **one exact anchor at −130.565779s**, meaning E1's first evaluation of that anchor is later, not earlier. The same event repeats across delay/cost panels and is not three independent failures. New-only anchors have no fabricated comparator lead. Counts of raw bar families, exact anchors and repeated evaluations measure different things.

All existing final alerts match exactly under E1: base0 common3, base15 common2, adverse common1; no added/excluded final alerts and confirmation lead0. E1+E3 similarly matches E3 base0 common5/base15 common4; adverse both no entries. The E3 effect within E1 repeats the previous common/added/excluded pattern:base0 2/3/1, base15 1/3/1, adverse0/0/1. Common E3 confirmation leads0 and62.180124s; common resolved entries areSL. These are the same retained market events, not new samples or a fresh timing result.

Base0 E1 total model net/closed-trade drawdown:−28.0779196/28.0779196 USDT; base15 −18.7186398/18.7186398; adverse −9.3586427/9.3586427. E1+E3 base0/base15 total net and closed-trade drawdown remain **null** because2 outcomes are unresolved. Adverse E1+E3 and all P1 arms have no-entry net0/closed-trade drawdown0; unresolved ratio is null when denominator0. This is not improvement. Emergency count and full mark-to-market drawdown remain null for every arm.

`candidate_accounting.json` records all scenario/policy detection sources/rejections, exact unique anchors and per-comparison signed candidate leads. `opportunities.csv` preserves each anchor's first evaluations. `gate_decisions.csv.gz` retains every observed stage/input hash and E1 proof; unexecuted gates remain not_evaluated. `trades.csv`, `trade_cards.json`, `event_accounting.json` and `fixed_plan_attribution.json` retain all common/excluded/added final events, subsequent fixed-model outcomes,5/15/30/60min horizons and same-trade30/60s manual-delay assumptions. A comparator with no confirmation evaluation is explicitly absent, not assigned an invented failure. Comparison rows and nominal delay variants are never summed as independent trades.

## Costs, data and limits

Same preserved model: existing taker6bp per side, stop slip2bp, per-entry funding reserve1bp; adverse8/5/3bp. Risk0.5%, margin15%, leverage30 and reference balance1871.9017 are unchanged model settings, not account changes. P1 uses the previously declared old public instrument metadata assumption. Spread, entry/TP execution, actual account fees, fills and settlement funding remain unverified; funding history0 days. Partial benchmark assumptions and earliest observed fixed-plan timing attribution are retained, not live fills. Manual delay uses an actual next retained quote within90s and otherwise remains unresolved.

Actual received/known/Telegram timestamps stay null. Nominal0/15s availability is an assumption. The preserved source observations are not independently published closed5m evidence. **RAW5M_INDEPENDENCE_UNVERIFIED** and **CANDLE_VOLUME_UNIT_UNVERIFIED** remain, along with missing final-bar schema, closed rollover/reconnect continuity and unseen continuous observations. No identical failed HTTP/WS source probes were repeated.

44 targeted tests passed, including10 new synthetic E1 isolation tests. Synthetic fixtures test symmetry, quote/bar separation, invalid source/gap/forming data, scoped adapter restoration, retained momentum/context/RSI/cost/risk checks, arm naming and actual default confirmation age. They are not performance samples. The existing PR21 detector file, production monitor, E3 code and P1 planner are unchanged. P0's15 and legacy467 tests were not rerun.

```bash
PYTHONPATH=/workspace/scratch/44f97b78c955/zb-test-deps python -m unittest test_lbank_first_retest_experiment test_lbank_confirmation_experiment test_lbank_entry_location test_lbank_structural_comparison -q
PYTHONPATH=/workspace/scratch/44f97b78c955/zb-test-deps python research/compare_first_retest_experiment.py
PYTHONPATH=/workspace/scratch/44f97b78c955/zb-test-deps python research/render_first_retest_report.py
python research/validate_first_retest_experiment.py
git diff --check
```

Hashes, commands, baseline checks and UTC start/end are in `manifest.json`, `report_manifest.json` and `validation.json`. Decision CSV is preserved compressed with mtime0 and its decompressed SHA256; SQLite and raw CSV are reconstructable intermediates. No sends, dispatch intents, orders, live activation, production merge/deploy, Railway changes or Upbit/CAP/ZIL edits occurred.

Next: predeclare/implement standalone E4 and E5 and their necessary combinations; separately implement the failure-isolated live EvidenceLedger adapter with migration/restart/outage/retention checks. P3 continuous true-receipt comparison, P4 qualified evidence review and P5 authorized production application remain incomplete. No promotion thresholds were loosened and no100-paper/30-day prerequisite was added.
