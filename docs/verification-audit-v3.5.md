# verification-audit-v3.5

## October 2 verification review

The 17:42:34 KST ranking snapshot comprised all 291 KRW tickers. Completed
1m/5m/15m/60m candle data was retrieved for the union of the value top 50 and
gain top 10: 55 unique markets, no request failures. Ranking and candles used
the same cutoff, not later prices. Operational screening records were compared
with these data; historical orderbook persistence cannot be reconstructed from
a current snapshot, so this is not an execution backtest.

Examples:

- SAND 17:30 screening quote 83.3 had completed 5m volume 1.175x, a 0.388 book
  ratio and only 0.24% resistance room. Its earlier raw 81.2 detection later
  reached +5%, but that is not a +5% return from the screening quote.
- APT 13:50 quote 1,114 was rejected only for the missing first retest despite
  WB confirmation. Completed 5m volume was 5.455x but 15m volume was 0.636x;
  the new completed-breakout alternative still would not qualify this example.
- At the ranking cutoff POD had only 19 completed 5m and 6 completed 15m bars.
  No previous-20 volume baseline was available. Missing history is separate
  from a failed market condition; the strategy does not fabricate that baseline.
- ZK and PUMP initially alerted at 18.5 and 8.22, respectively, then declined.
  Merely appearing on the daily gain leaderboard is not evidence that the
  alert-time entry would have been profitable.

## Changes

1. Add an ordinary completed-breakout alternative to the required first retest.
   Require eligible top-5-percent leadership, early acceleration, positive
   5m/15m momentum, a strong completed 5m close above prior structure, 5m
   volume >=1.5x the preceding 20 bars, 15m volume >=1x, the last 10 completed
   minutes present, confirmed WB on 1m or a true completed 5m WB breakout,
   persistent normal book support, normal spread, non-crashing BTC, and live
   price no more than 0.5% above the completed close. No fake retest flag.
   Retain ordinary 1m hold/quality, RSI, breadth, score, stop and R:R gates.
   Eligible raw surges now reach REST validation instead of being forced into
   a pullback watch before their completed structure can be checked.
2. Always use the full normal survival interval for this alternative, including
   on fast or pullback trigger paths. Recheck completed structure, volumes,
   liquidity, entry range, relative eligibility/rank and BTC before dispatch.
   Telegram labels the no-pullback route and actual WB confirmation timeframe.
3. Keep 60 completed entry-timeframe bars, but require only the mathematically
   necessary 21 completed 15m/BTC bars. A missing optional MA60 cannot impose
   a 15-hour listing embargo. Insufficient history reports counts/requirements.
4. ALL_KRW refresh now bypasses the default 300-second market-list cache.
   Previously a 60-second loop could report a refresh while using a stale list.
5. Infer observed quote increments with Decimal subtraction. Preserve raw and
   rounded stop diagnostics even when stop-width rejection happens before the
   target plan. Add completed-history counts, structural proof, 10m continuity,
   hourly context, queue wait and fetch duration to screening evidence.

Validation: 270 tests passed, including actual WB calculations for a breakout
without a pullback; insufficient volume, missing minute, unfinished candle,
weak book and chase rejection; full survival, changed rank, history boundaries,
listing-cache bypass, Decimal tick grid and rejected-stop diagnostics.

These tests establish software behavior, not improved win rate or profitability.
Raw-signal +5%/-3% outcomes remain distinct from candidate returns after costs.
