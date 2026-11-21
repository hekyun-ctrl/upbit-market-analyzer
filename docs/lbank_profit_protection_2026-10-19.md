# LBank pre-TP1 protection and profitable reversal review — 2026-10-19

## Confirmed code paths

Before this change, native frozen-thesis signals only started their ATR runner after TP1 delivery. A favorable movement falling short of TP1 therefore had no profit protection path. Confirmed opposite 15m direction, 5m EMA direction and opposite swings also required price to be adverse to the original signal entry before invalidation. A profitable confirmed reversal could consequently be ignored. Manual registrations previously suppressed hypothetical followups without a corresponding higher-direction reversal check; new manual registrations now receive their own check anchored at report time, with no invented structural anchor. The monitor observes signal quotes, not authenticated exchange positions; manually reported fills are a separate ledger.

Earlier entry work on 2dd51bd already ranks aligned completed 15m/5m evidence above isolated minor obstacles, retains repeated support/resistance barriers, and rejects entry extension beyond 2.5 native 5m ATR from EMA9 (also from the retest anchor for retests). This release retains those rules and the existing confirmation timing, RSI gate, sizing, hard stop and monetary risk caps. It adds an exact KST expiry to new-policy entry messages; expiry is the existing five-minute validity, not a new wait. This does not solve every possible late entry.

## New frozen policy: native_pre_tp_profit_v1

Only newly delivered signals opt in. Previously sent signals retain their recorded management policy. Newly registered manual positions opt in using the user's reported entry and stop; old manual records are not retroactively given an invented favorable excursion.

- Track the best observed quote since registration, never pre-entry candle extrema.
- Before TP1, arm after favorable movement reaches the larger of original entry-to-stop price distance and twice the modeled break-even distance. This is price-risk distance, not net 1R. The frozen cost model is taker 0.06% per side, exit slippage 0.02%, funding reserve 0.01%; actual execution costs remain unknown.
- Put the advisory guard one quarter of the movement from modeled break-even toward the best observed quote. Require fresh completed native 5m data and at least half an ATR between the current quote and proposed guard. Never propose a guard already crossed.
- Raise protection only in the favorable direction, on a new completed 5m bar, by at least max(0.25 ATR, 0.01% entry). Manual proposals also respect the user's already reported tighter stop.
- Preserve the guard across the TP1 transition; do not loosen it to price break-even.
- Check original stop and confirmed structure failure first. A guard touch is a profit-management notice, not a reversal or a new entry. End hypothetical reference monitoring after delivered guard-touch notice; keep a reported actual position until the user reports closure.
- Persist pending notices and guard touches. Retry delivery after failure/restart; a rebound cannot erase a known touch. Only wholly post-activation completed native candles can establish an intrabar touch.
- Allow a fully confirmed higher-direction reversal while price remains profitable for the new opt-in cohort. Mixed evidence still does not constitute a reversal. This removes the loss prerequisite; it does not claim to predict reversals before they are confirmed.

These are advisory alerts, not exchange stop orders. A fast reversal can cross the guard before observation or execution, and even the modeled guard cannot guarantee positive realized PnL. Protection is conditional, not active on every tiny favorable movement.

## Historical evidence and limits

`research/fixtures/lbank_profit_protection_20261019.json` preserves public reference #12 and historical contract quote/native 5m bar publications. `research/replay_profit_protection.py` combines them with the existing original-bar fixture, using each bar only after its logged publication. No one-minute candles are fabricated.

The coarse quote replay arms at **19:32 KST** with a reference guard near **82,299.7**, before TP1 **81,823**. It observes a crossing at **19:39 KST** at **82,369.3**. The observed crossing price is worse than the guard for this short. It must not be treated as a fill at the guard or as proof of a net winning trade. This replay is coarser than the live 15-second risk loop, does not reproduce all entry decisions, and does not use a user's different actual fill as the reference entry. The change demonstrates that a missing management path now exists; it does not establish future profitability or frequency.

Hypothetical accounting applies the policy only to the opt-in management cohort, retains baseline levels/warning cohorts, marks all outcomes `actual_pnl=False`, uses the observed adverse quote on crossings, and excludes unresolved intrabar touches from return claims. New policy/version and protection evidence are retained separately from historical signals.

## Actual technical analysis used by live alerts

| Role | Live calculation |
| --- | --- |
| Data | Native LBank futures 1m OHLCV; five consecutive native minutes aggregate to a completed 5m bar. Closure is stream inferred, not REST final-flag or chart parity certification. Aligned 15m closes require three complete 5m bars. |
| Breakout | Prior 15-minute native high/low breakout, 1m-close momentum over 5/15 minutes, MACD(12,26,9). Initial candidate thresholds 0.14%/0.20%, with separate existing confirmation checks. |
| Trend retest | 1m EMA20/50 direction and slope, native local high/low pullback and recovery, MACD histogram reaction and momentum checks. |
| Range rebound | Prior 60-minute native box, balance, boundary location and rebound/rejection; cost/risk-dependent minimum width and room to the opposing boundary. |
| Larger-direction priority | Completed 15m changes and completed 5m EMA9/21 direction/slope; confirmed native 5m pivots and repeated obstacles through the target path. |
| RSI gate | Wilder RSI14 on completed native 5m closes, rising for longs and falling for shorts. No universal RSI30/70 gate or absolute extreme veto. |
| Volatility/location | Native 5m Wilder ATR14, structural stop geometry, no-chase/extension limits and target room. |
| Followup | Frozen structural reference, completed 15m direction/5m EMA/swings, hard-stop observations, new pre-TP1 guard; post-TP1 ATR runner may trail/extend targets using trend and relative volume. |

RSI divergence context, BTC/USDT dominance and USDT supply/peg, public-trader technique studies, and order-book shadow research are annotations or research cohorts, not the primary live entry gates. EMA, MACD and RSI are related transforms of the same price series, not independent votes or a calibrated win probability. This is a rule-based 1m/5m/15m system, not a proven high-win-rate model or a complete 1h/4h trend system.

## Validation

321 LBank tests pass. Run `PYTHONPATH=<dependency directory> python -m unittest discover -p 'test_lbank*.py'`.

Coverage includes both directions, model break-even arithmetic, small moves, unknown/stale native evidence, pre-entry extrema rejection, causal publication times, monotonic protection, TP1 handoff, reported tighter stops, partial/closed manual positions, send failures/restarts, original-stop precedence, profitable reversal opt-in, baseline isolation, adverse observed crossing prices and exclusion of ambiguous intrabar outcomes. Passing these checks verifies behavior, not investment performance.
