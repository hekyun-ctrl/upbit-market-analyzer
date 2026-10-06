# Structure and audit v3.19

## Fresh rebreakout admission

The v3.18 path correctly separated a renewed breakout from its original watch,
but still required top-rank leadership, positive 5m/15m/60m momentum, and
aligned completed 15m/1h trends at the same time. The same correlated trend
checks were then repeated during the 60-second survival stage.

For a fresh, completed 5m structural breakout only, trend context is now
qualified by either:

- fresh relative leadership: rank in the top 2%, 5m momentum at least +0.2%,
  and 15m momentum at least +0.8%; or
- established completed 15m/1h trend support.

The selected basis is recorded on the candidate. Survival repeats the chosen
proof using refreshed relative-strength data or refreshed higher-timeframe
candles. A fresh leader does not need an unrelated 60m momentum threshold or
an hourly moving-average turn. It is not labeled as an established hourly
trend and receives no hourly extension target unless the hourly structure is
actually aligned.

The watchlist's completed-breakout scanner uses the same top-2%, +0.2% 5m,
+0.8% 15m admission for this separate completed-structure review; the generic
+1.0% 5m `relative_strength_eligible` threshold no longer blocks the review
before the full candle, execution and risk analysis runs.

The following remain independent hard checks: completed breakout structure
and volume, market breadth floor, breakout level hold, no more than 0.5%
execution gap from the confirmed close, BTC crash protection, extreme RSI and
known-selling rejection, repeated nearby order-book depth, spread/cost checks,
structural 5m stop-width limit, minimum risk/reward, and the existing +5%
anti-chase limit from the original watch. Ordinary non-fresh structure checks
are unchanged.

## Validation

Regression coverage checks that fresh breakouts can qualify through either
trend proof, that the old route remains conjunctive, that the selected
leadership proof survives without a 60m gate, and that ordinary risk and
execution floors remain enforced. No profitability or hit-rate improvement
is claimed before new live observations.
