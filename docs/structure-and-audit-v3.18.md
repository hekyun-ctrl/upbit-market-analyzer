# Structure and audit v3.18

## Fresh breakout rechecks

A scanner-detected consolidation rebreakout inside an active watch now receives
its own setup identity. A completed-bar breakout recheck is also kept distinct
from a pullback/re-entry callback. This prevents an older signal's day-high
drawdown rejection from being inherited by a new structure.

The new setup still has to pass the existing completed-candle, relative
strength, market breadth, order-book, BTC, liquidity, anti-chase, stop-width,
minimum risk/reward, and dispatch-survival checks. A fresh setup is blocked once
price is more than 5% above its original watch price. Genuine pullback rechecks
continue to use the prior day-high drawdown rule.

Fresh setups receive a separate screening identifier and retain a parent signal
identifier for audit. The original raw-signal tracker remains active, while the
early-watch tracker records the original watch's later outcome.

## Regression checks

- Fresh structural rechecks do not inherit the old day-high drawdown gate.
- Ordinary watchlist rechecks retain that gate.
- Fresh breakouts more than 5% above the original watch price are rejected.
- Broken first pullbacks can be re-screened only at a new high, without
  fabricating a retest.

These changes alter signal classification and audit identity. They do not
guarantee a profitable entry or change automatic-order behavior.
