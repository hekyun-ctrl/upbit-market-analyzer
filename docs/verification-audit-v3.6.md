# v3.6: executed-buy persistence with bounded book/breadth substitution

The SAND audit found an early raw alert at 64.1 KRW (2026-10-02 15:29:56 KST),
then a 15:30:31 screen at 64.2 with completed 5m/15m volume 3.74x/4.18x.
Breadth 30.8%, queued bid/ask size 0.29x and hot-RSI/weak-book combination
blocked it. The raw signal reached +5% before -3%; this is missed raw follow-through,
not executed trading profit. A later raw alert at 72.4 hit -3% before +5%.
Avoiding all filters would restore that losing chase too.

Historical execution sides were not retained. We cannot truthfully claim that
this version would have approved the early SAND entry, or estimate its hit rate.
The new route is prospective; compare actual delivered alerts and rejected
observations by strategy version, target-before-stop, excursion and costs.

## Changes

- Collect `ask_bid` and `sequential_id` from the existing public trade WebSocket.
  BID means aggressive buy execution; queued orderbook sizes remain distinct.
  Deduplicate per-second IDs, ignore older events, bound each second to 512 IDs
  and 120 seconds per market. Truncation/unknown sides cannot certify evidence.
  Reconnect clears execution history. OHLC warm-up does not create buying flow.
- Use the last 60 **completed seconds**, divided into three 20s windows. Require
  >=20 total trades, >=5/window, >=10m KRW total and >=1m/window, >=60% BID notional
  overall and in at least two windows including the newest. Require non-falling
  lows (0.15% tolerance), >=0.1% closing rise, a recent tick within five seconds
  and evidence age <=10s. An unfinished candle never becomes a completed bar.
- The additional route requires top 2% relative strength, 5m/15m/60m momentum
  >=0.2/0.8/1.5%, established completed hourly trend and intact completed 15m
  trend above MA20, completed 5m volume >=1.5x its preceding 20 bars and 15m >=1x,
  ten completed minute bars without gaps, verified WB/5m breakout or first
  retest, and live price <=0.5% above its completed 5m close.
- Replace book imbalance and moderate breadth rejection **only** on that route.
  Breadth <20%, BTC crash/weakness, RSI hard caps, failed WB/structure, chase,
  liquidity, resistance, stop-width and R:R remain gates. At least three book
  snapshots are required; a majority must have bid/ask ratio >=0.2, normal spread,
  and >=5m KRW on each side within 0.5% of the quote. Five-minute initial structural
  stop width remains <=3%; the first target requires >=2R before costs.
- Keep explicit risk deductions, without score saturation hiding them. Only this
  fully qualified route uses a separate floor of 86 (configurable 86..100), matching
  final Telegram policy. It cannot borrow the fast/explosive/RSI-only exceptions.
- Full 60s survival on all signal paths, with newly sampled executions, new books,
  completed bars, refreshed hourly/15m context, BTC and relative leadership.
  Dispatch rechecks original entry range/stop/chase/R:R and execution freshness.
  If the setup has passed its entry range it is not sent as currently executable.
- Telegram explicitly labels the route and reports actual executed buy-notional
  percentage and trade count, alongside completed volume and risk deductions.
  Coverage logs distinguish markets with valid live execution history.

Disable only the added route with `CANDIDATE_TRADE_FLOW_LEADER_ENABLED=false`.
Existing routes stay unchanged. No automatic orders or manually sent alerts.
No historical success probability or future return is asserted by a score.

## Validation

`python -m pytest -q`: 321 passed, including the unchanged prior 270 tests.
Added 51 checks cover completed-second accounting, duplicate/unknown/older ticks,
bounded buffers and reset, non-buying/stale/thin flow, genuine weak-book admission
with deductions, late chase, incomplete candles, missing volume/minutes, extreme
breadth, thin/wide books, RSI hard caps, BTC crash/daily weakness, broad stop,
hourly trend loss, fresh survival evidence, Telegram risk/score/freshness checks,
and full survival timing on fast, completed and pullback source paths.
