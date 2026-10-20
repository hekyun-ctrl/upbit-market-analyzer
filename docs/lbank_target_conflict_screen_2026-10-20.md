# Frozen target-conflict veto paired screen — 2026-10-20 23:29 KST

This is a descriptive subset screen on22 already examined development entries, not a fresh strategy backtest, native fill evidence or qualified candidate.

Rule target_conflict_no_direction_override_v1 was fixed in docs/lbank_fixed22_entry_attribution_2026-10-20.md at1508c71afb24f8417df8da0b2da65ddab549a261. Exclude only existing target_room=conflict; retain unknown. No new thresholds, directions, entries, reentries, stop/TP or capital changes. Retains9, removes13. Use actual source5c977a7 and corrected source-ready path outputs only. Earlier unready-input intrabar result remains INVALID.

## Reproduction and integrity
Run python conflict_screen.py using research/continuation/conflict_screen_20261020_2331.py (frozen before successful outcome calculation atbe125f1496287b64371ff8a87ab8746ea362ce75).
Inputs:
- attribution_result.json SHA256057735b5c5a83fac3dfc2edea1b855103131a16e39e643630fcf111db9172802
- intrabar_result.json SHA256de55cbb223ede9c032aafb2668212004a5c5569cd1ffb2411895e2c8703776a5
- prior_report.json SHA256c15624c575ff1a08b2fe74761dc9877c5c7bbb104e193667e7845946d35491e7.
Original sources/data archive URLs and SHA in preceding attribution document. All three whole hashes asserted before calculation. First attempt rejected surplus local terminal newline (removed only after exact archived SHA verified); second rejected timestamp:number policy row ID assumption (fixed to original number suffix). Neither failed attempt calculated market comparisons; original scripts retained as execution audit.
Six baseline=retained+removed cash partitions reconcile; independent Decimal summation matches float totals. Bootstrap circular2-calendar-day blocks5000replicates seed2329 on paired day sums; only7development days, so intervals are fragile descriptive sensitivity, not production inference.

## Actual results
Both paths retain9/22 original selected entries, covering5/7days;1.286/day is selected-entry density, not opportunity recall or expected native frequency.
| Exit model | Retained net R H-L / L-H | PF H-L / L-H | Doublecost R H-L / L-H | Removed profitable exits H-L / L-H |
| --- | --- | --- | --- | --- |
| levels | +1.128132 / +1.128132 |1.231731 /1.231731|-1.698265 /-1.698265|4 /4|
| warning_exit | +1.128132 / +1.128132 |1.231731 /1.231731|-1.698265 /-1.698265|4 /4|
| trend_runner | -3.673576 / -1.787494 |.037774 /.541142|-6.501465 /-4.617238|7 /5|

Keep positive levels result; do not generalize failure to all techniques. It fails doublecost robustness. Levels TP1states4/9,Wilson lower18.88%;runner0/9 or1/9,Wilson lower0/1.99%. These assumed path states are not actual TP1-first native fills.
Paired improvement in totalR:
- levels+6.258069, dayblock95%[-1.485013,14.942281]
- warning+5.032808, [-3.634681,14.042585]
- runnerHL+3.363691,[-.615527,7.806338];LH+5.646448,[1.111246,11.611770].
A positive lower bound for one assumed-path difference is not a positive strategy-expectancy lower bound, and the two paths are not independent samples.
Original minute-open runner observations remain7unknown retained plus6unknown removed. Original true all-entry return stays unknown; filtered unknowns cannot be counted as avoided losses. Report contains all22 retained/removed rows perpath/policy and emergency-within5min source-event proxy; proxy is not actual warning delivery or native emergency rate.

## Decision
Do not deploy this veto as a validated profitability fix. No risk changes, loss/margin increases, forward candidate or replacement. Current runner remains negative; removing conflicts alone also discards profitable exits and does not fix entry direction/timing. No threshold tuning, selective path choice or unknown-room reinterpretation.

Next finite numerical step: reuse existing public-techniques4 TechniqueStudy and its original confirmation/context/RSI/plan gates, source5c977a7 unchanged, for a bounded Jan8–15 chronological offline episode replay plus Jan16followup. Freeze execution cadence/state schema and actual function-order protocol first, assert source readiness and match original study fixtures before market calculation. Record unique raw setup IDs, watches, opens, completed and censored separately, perstrategy with whole costs/doublecost; use default gates, no conflict-veto integration or new technique. Do not call this reused development evidence OOS; no actual-native/realmanual fill claim. This tests already running alternative episodes rather than optimizing current22 entries or adding unverified formulas.

## Targeted external source check
Primary official explanation https://bollingerbands.us/help-methods.php and author manual https://www.bollingerbands.com/_files/ugd/58be43_b120ddf0184540608baf19e2c0ae2019.pdf read this run. They support requiring price and independent volume-indicator confirmation for MethodII, not blanket reversal at band edges. The manual lacks a publicly complete numeric entry/exit/cost/closed-BTC-ledger contract; no thresholds imported from third-party book mirrors, no candidate or backtest claimed. This explanatory lead does not displace existing4technique episode priority.

## Bounded operations
Latest productionHEAD5c977a7, deployment23799b11-6f14-4e93-aacf-78ce691f9393 SUCCESS unchanged. Paper notification filter14:15–14:35UTC returned progress APIacks14:15:01/14:30:02, nottrade signals/device delivery. Full study ledger was not read this run; do not claim0newstudytrades from this filter. No restart/deployment.
Result SHA2565cae2cac959b561afe8fd79953c96a605fbe316b80a41d39726f88bba40a43a8; Git blob05bbc2a8f2af787309605fabaa755c44a957585e.
