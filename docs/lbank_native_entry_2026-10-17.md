# Native candle integration into LBank entry reviews

The live `run()` pipeline now reads `/data/lbank_native_candles.sqlite3` using a
separate read-only SQLite connection and one consistent transaction. Only the
collector opens a writable connection. Existing ticker caches are retained for
fast price risk warnings, observation accounting and cooldown persistence; they
are never an entry fallback.

- Short-horizon strategies: consecutive closed native **1m** closes for
  EMA/MACD/momentum, native highs/lows for structural swings and breakouts.
  5m closes are not passed into minute-indexed strategies.
- Entry reference and second-stage price confirmation: current perpetual quote,
  kept separate from the closed-bar indicator array. The existing 0.10% chase
  bound also rejects an initial quote too far from the closed-bar trigger.
- RSI: Wilder(14), completed native **5m** bars only. Only direction alignment
  is checked; there are no new absolute 30/70 or extreme-history entry filters.
- Context: five complete aligned **15m** closes from native 5m groups. Existing
  flat-market, chase, countertrend and verified-reversal thresholds are retained.
- Stops and plausible target range: native candle wicks; the existing fee,
  RR, risk budget, leverage and margin cap calculations remain in force.
- Short Telegram format and monetary fields remain; native plans identify
  themselves as `원본 5분봉 기반/호가 미검증`. Actual account/order verification
  and automatic trading are not added.

## Readiness and gaps

Require at least 65 consecutive recent closed 1m bars, 20 consecutive recent
closed 5m bars and five aligned complete 15m groups. The 20-bar minimum preserves
the earlier live sampled-RSI minimum. Up to 250 bars seed native Wilder RSI;
250 remains a diagnostic maturity target, not a new absolute RSI entry rule.
Before 250, initialization can differ from a chart with a much longer history.
This release does not claim chart parity or improved trading returns.

The latest bar must match the most recently completed wall-clock interval.
Missing, stale, corrupt, forming or discontinuous data pause new entry reviews
and clear any watch. No bars are interpolated. After a gap, enough consecutive
fresh history must rebuild (20 full 5m bars can take about 100 minutes). Risk
notices for already tracked signals/positions continue using fresh quote polls.
The collector's on-volume database survives redeploy; forming bars remain
uncertified until the stream advances. 5m closure is inferred from continuous
native 1m streaming, not independently REST-verified.

`NATIVE_ENTRY_STATUS` is authoritative for live entry readiness, including bar
counts, `entry_source_switched`, latest 5m start, RSI and maturity status.
`NATIVE_CANDLES_STATUS` describes collector history only. Delivered plans persist
`data_source`, `native_5m_start`, `native_5m_count` and `rsi5`; older schema keys
remain aliases for existing consumers. Native neutral-exit shadow observations
use their own state file to avoid mixing earlier sampled research results.

## Verification

`python -m unittest discover -p 'test_lbank*.py'`

Includes native DB continuity and restart/read-only behavior, stale/current bars,
known Wilder RSI, independent native stages with sampled fallback forbidden,
wick-based breakout/stop behavior, direction-only extreme RSI, cost/risk caps,
chase rejection, Telegram format and existing TP1/TP2 risk followups.
