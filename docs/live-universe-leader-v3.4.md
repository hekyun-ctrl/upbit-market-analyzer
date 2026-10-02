# live-universe-leader-v3.4

October 2 operational evidence identified two distinct problems: the healthy
WebSocket still subscribed to the previous day's 290 markets while the public
KRW list contained 291, and a top-2-percent PUMP screen with completed breakout
evidence was rejected solely for 32.5% market breadth. Other SAND rejection
tracks reached their raw stop before later rallies; these must not be counted
as risk-free missed winners. Raw +5%/-3% tracking is not candidate execution P&L.

Changes:

- Refresh ALL_KRW membership every 60 seconds (MONITOR_MARKET_REFRESH_SECONDS,
  minimum 30). Resubscribe with the complete set only on change, preserving
  engine and watch state. Warm additions without blocking live reception;
  remove inactive markets from relative rankings. Empty/failed REST fetches
  retain the working subscription. Subscription count, refresh time, received
  market count, and markets without received trades are separately inspectable.
- Add a breadth-only exception for a top-2-percent eligible leader in 20% to
  the ordinary breadth floor: fresh completed 5m breakout volume >=3x (>=1.5x
  on the first two-bar retest), completed 15m volume >=1x, recent ten completed
  minutes present, WB confirmed, positive 5/15m momentum, positive hourly
  momentum when available, persistent hard book floor, normal spread, and
  live price no more than 0.5% above the completed close. This does not waive
  ordinary entry, RSI, stop, chase, volume, risk/reward or score rules.
  Retain breadth score deductions. Require the full survival interval and
  renewed completed evidence, eligibility, top-2-percent rank, breadth >=20%,
  and the existing independent BTC survival checks.
- Admit young top-2-percent eligible leaders to the next completed 5m REST
  recheck without requiring an hour of price history. Recheck admission alone
  is not an entry approval; no fake pullback or retest flag is assigned. Keep
  at most one screen per market per completed bar, rapid-drop invalidation,
  inflight guard and repeat-delivery limits.

Tests cover dynamic subscription changes, REST/send failures, warm additions,
cancellation, coverage, rank exclusions, fresh and retry breadth exceptions,
survival loss, and blocked unsafe cases. They establish software behavior,
not a measured improvement in win rate. Subsequent forward outcomes are needed
to judge missed rises, avoided declines and candidate returns after costs.
