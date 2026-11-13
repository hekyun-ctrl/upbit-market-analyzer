# ZB B-short three-cause repair / 2026-11-13 (KST)

## Scope and production safeguard

This change is a **test-only candidate and observation repair**, not a profitability
certification or broker order system. No account API, automated order or broker
position modification is added. The existing formal qualified-trade gate is
unchanged. Newly projected targets are explicitly **NOT confirmed support**.
The existing one-minute experimental delivery flag controls BOTH independent
B lanes; all delivered messages are marked "실험 진입 후보 / 실거래 검증 전".

## Incident evidence (Railway production, 2026-11-13 09:00-12:35 KST)

- 44 formal five-minute evaluations: 8 warmup + 36 no qualified entry.
- All 36 evaluated declines recorded `rsi_short_waiting_extreme`.
- Six formal decisions also recorded `no_known_target`.
- 121 one-minute B trial decisions all blocked on
  `insufficient_or_invalid_five_history`; 74 authentic recent continuous
  five-minute candles were present, versus an old 96-bar experimental gate.
- B scenario event messages were accepted separately but are **not** formal
  actionable entry approvals. Native candle recorder was receiving OHLCV.

## Fixes

1. B higher-timeframe consensus requires 72 strictly contiguous completed
   native LBank five-minute candles, at least 12 *fully aligned* 15-minute
   groups and 5 *fully aligned* one-hour groups. The regime indicator uses
   EMA(3) vs EMA(5) hourly, explicitly rather than pretending there are
   six full one-hour groups. All existing provenance and receipt checks,
   source-gap handling, and no-future-5m exclusion remain.
2. Separate 5-minute first support-break B trial independent of legacy
   RSI extreme and delayed failed-reclaim checks, plus existing earlier
   1-minute microbreak trial now able to run after sufficient genuine
   five-minute history. A 5-minute trial closes on exactly its five-minute
   clock, checks native live bid/ask, caps spread and repricing, calculates
   structural stop and costs, and retains durable 30-minute dedupe.
3. Price discovery is not treated as a missing confirmed support: a
   bounded volatility **projected** target is calculated and rejected if
   beyond 8x observed ATR or crossing nearer known support; stop, quote,
   and cost-adjusted 2R must also pass. The existing B retest research
   journal records `projected_target_shadow` or rejection when no known
   support; it cannot falsely mark an unverified projected-target trade
   `qualified_shadow`.

## Replay results and limitations

Source `research/zerobase/audits/2026-11-12/lbank_native_5m_177bars.json`
contains 177 consecutive five-minute native derived bars. Offline historical
evaluation after the 72-bar warmup yielded 106 independent first-break
checks: 86 no break, 6 inadequate volume, 6 projected exit beyond ATR
cap, 4 extended move, 2 known closer support, and 2 excessive stop risk.
**There were zero economically eligible five-minute candidate trades**.
This is not proof of profitability or that a past alert should have fired.
In addition, 1-minute live signals must be assessed on genuine native
one-minute closes; five-minute history alone cannot substitute.

New concentrated and regression test suites pass locally: 77 focused
tests and 604 total `test_lbank*.py` tests as of branch revision.
Railway predeploy additionally gates on `test_lbank_b_three_fixes`.
These are functional tests, **NOT** held-out trade profitability verification.

## Operational acceptance

- Verify successful Railway deployment on
  `feature/lbank-video-alert-20261104`, not merely PR merge.
- `VIDEO_B_FIVE_TEST_DECISION` displays candidate/delivery/trend/available
  on each new completed five-minute bar.
- `VIDEO_B_MOMENTUM_TEST_DECISION` should stop returning
  `insufficient_or_invalid_five_history` once 72 verified five-minute bars
  and 61 verified one-minute bars are present.
- Check actual Telegram acceptance only if a correctly qualified test
  candidate arises. **Do not claim a formal alert was emitted** from
  these changes; formal profitability gate was intentionally not weakened.

## Future research before formal promotion

Maintain immutable decision timestamps and reconstruct every candidate
at then-available LBank quote/fee/slippage inputs. On an independent
chronologically held-out sample: report count, win/loss/unresolved,
max adverse excursion, fee-adjusted P&L, drawdown, and missed breakouts.
Do not promote the projected-target trial into a "formal qualified"
position recommendation until objective held-out evidence supports it.
