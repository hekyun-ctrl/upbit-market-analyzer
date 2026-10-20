> **INVALIDATED for active native management inference (2026-10-20 22:32 KST):** input omitted quality=stream_closed, so native valid_state rejected bars. Preserved below for audit only. Use [corrected replay and attribution](lbank_fixed22_entry_attribution_2026-10-20.md); do not use these runner results for strategy decisions.

# Fixed22 intraminute path sensitivity — 2026-10-20
Research only; no operating change or promotion.

## Frozen protocol and provenance
Code frozen before outcomes at cf417f8cf8c0d3beffa72ad93e0b6b45ef5694f0:
research/continuation/intrabar_paths_20261020_2211.py.
Production HEAD freshly checked: 5c977a7f51cea67048f95791d1dcc4230634939a, unchanged.
Actual source restored from that exact commit; all32 source Git blob hashes checked against recursive tree. Scratch had been cleared; no old local cache was assumed. Python3.12.14 / ccxt4.5.89. No live monitor main, messaging, account or order calls.

Original22 plans taken directly from prior report (SHA256 c15624c575ff1a08b2fe74761dc9877c5c7bbb104e193667e7845946d35491e7), with identical reference IDs, times, direction, stops and targets. Entry Jan8–15 UTC; followup ends Jan16 exclusive. Same development history, not independent OOS; two scenarios are22 paired entries, not44 independent observations.

Official Binance original inputs:
- https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1m/BTCUSDT-1m-2025-01.zip
  SHA256 b69ca5b2c2d1ae00d68d2790d09138cb2a44085ff19e0da5dc98b569ea7e489e;44640 contiguous60s rows.
- https://data.binance.vision/data/futures/um/monthly/fundingRate/BTCUSDT/BTCUSDT-fundingRate-2025-01.zip
  SHA256 df725010d55458866eb5a47e9ee708c6796c9d7e77b14c38342cea11f381815e;93 rows.
Inputs downloaded again and whole archive hashes verified.

## Execution
Place pinned32 source files (restore_sources manifest), prior replay as prior.py, prior report as prior_report.json, above archives as bars.zip/funding.zip, and new script as intrabar.py in same directory. Run python intrabar.py.
prior.py is research/continuation/replay_full_management_20261020_2028.py, blob81a6ede9d0f36abaefa6e8eec6ad21f661ff60a1. Manifest saved alongside this report.
Paths fixed O-H-L-C or O-L-H-C at0/15/30/45s each minute. Accounting then risk then acknowledgment; original minute-sample history updated once per minute. Completed1m/5m bars only;15m features use completed constituent bars. No forming-bar H/L added to historical features. 13 prior deterministic cases pass;92160 poll-prefix assertions;264 Decimal comparisons against independently expressed leg cash. No temporal mutation regression was added in this run: prefix assertions are narrower evidence, not a proof of every source function's causality.

Fee6bp/side and slip2bp/side; archived funding at minute settlement quote. Doublecost doubles fee/slip and funding charges, not receipts. R denominator is original source stop cash loss. Source censor handling remains unchanged; original13 unknown observed outcomes retained separately. Assumed path completions do not resolve true native outcomes.

## Actual calculation
Both assumed paths produced identical aggregate cash results (exit timestamps differ). All22 scenario rows completed for each policy.
| Policy | Net total R | PF | Doublecost R | Closed DD R | TP1 states |
| --- | ---: | ---: | ---: | ---: | ---: |
| levels | -5.129937 |0.706752|-12.031229|8.666839|8/22|
| warning_exit | -5.129937 |0.706752|-12.031229|8.666839|8/22|
| trend_runner | -6.062594 |0.678123|-12.962485|9.492440|8/22|

These are hypothetical trajectory scenario results, not observed LBank ticks, fills, manual latency, mathematical upper/lower bounds or an improvement claim. Original minute-open runner all22 expectancy remains NULL (9complete/13censored); its conditional9 sum -8.565962R must not be compared as a like-for-like causal management improvement.
Full results preserve132 policy rows, all legs/events and original unknown summary. SHA256 a97d7c39a576c181994fe4a005cc100a46cf4bcc9d81ba497ac555a846a76788; remote Git blob e6053e27e1d60ae985666b686a49e76deb58135c readback verified.

## Decision and limits
No robust positive edge demonstrated: both fixed synthetic scenarios lose before promotion inference. No strategy replacement, risk change or deployment. Correlated confidence interval, actual five-minute emergency rate, native fills/manual execution and new forward evidence remain unestablished; this finite sensitivity exercise cannot certify those gates. Source HEAD checked; runtime telemetry was not freshly inspected in this calculation and previous readiness is not claimed current.
Next: stop exit-path variants. Recover original direction/location attribution and use these same frozen22 entries plus completed known-at-entry bars to inventory actual target-room/conflict/unknown and directional evidence paths, particularly the existing crossed-pivot gap. Report paired accepted-context coverage and uncertainty, then freeze only a source-grounded complete entry correction before any new comparative test. Do not simply ban band-edge entries or tune cutoffs against these22 outcomes.
