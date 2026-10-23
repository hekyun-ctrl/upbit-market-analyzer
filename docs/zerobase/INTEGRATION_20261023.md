# ZB integration release — 2026-10-23 KST

## Scope and current conclusion

This release connects a persistent ZB computation worker to new native candle
arrivals, public instrument/market observations, quote-sized paper execution,
funding input events and an inert delivery queue. **It does not qualify ZB for
entry publication or prove improvement over the existing strategy.**

The user's paper and connection-status notification stop remains in force.
The new worker receives no Telegram transport. Existing reference and risk
paths remain separate. No account reads, orders, positions or transfers exist
in the new integration. The reference equity is a model's 1,000 USDT, never
the user's account balance.

## Changes

- `lbank_native_candles.py`: persist the first wall-clock arrival of each
  stream-closed minute and derived five-minute candle, in the same SQLite
  transaction. Existing old rows without original receipt evidence are not
  relabelled with today's receipt time. The 5m source explicitly says derived
  from 1m. Keep 32 days for optional 4h context; retry recorder startup after
  storage failures instead of leaving its thread permanently stopped.
- `public.py`: only the documented public instrument, marketData and marketOrder
  GETs. Select exactly BTCUSDT SwapU; verify BTC/USDT/USDT, priceTick,
  volumeTick, multiplier, minimum amount, status and time. Forecast funding
  uses the API's nextFeeTime/positionFeeTime, never a fixed 8h schedule.
- Fees use the official public base taker 0.06% policy, not an authenticated
  personal tier. Forecast funding and 2bp exit slippage remain ASSUMED. MARK
  price is required for confirmed funding. Displayed rates are not settlements.
- `LiveEngine`: same candidates and accounting as offline replay; a live
  candidate waits for the first fresh, quantity-aware eligible quote. Depth
  VWAP is recalculated until risk-sized quantity stabilizes. No entry
  slippage is added again to observed entry depth. No late revival after the
  first eligible quote fails. Closed-bar invalidation is durable and waits
  for actual depth before assigning a paper exit fill, even after a rebound.
- Funding events and complete coverage records have explicit timestamps and
  sources. Late funding after a quantity mutation requires replay. Missing
  funding coverage is not zero. Conflicting settlement revisions are rejected.
- `EventStore`: input, event sequence, archived position, state and outbox are
  one transaction. Snapshot memory is compact; SQL retains the full history.
  Restart restores LiveEngine, active plan, contexts and native input cursor.
- `Dispatcher`: disabled by default. TTL and quote freshness rechecked before
  READY delivery; old disabled backlogs cannot burst after activation. Reserve
  UNKNOWN before network. Unknown entry responses are not blindly duplicated.
  Emergency retries preserve the original event and explicitly disclose a
  possible duplicate. ACK means provider response, not device receipt or fill.
- `service.py`: independent worker, opt-in via `LBANK_ZB_ENGINE_ENABLED=true`.
  Other switches remain `LBANK_PAPER_NOTIFICATIONS_ENABLED=false` and
  `LBANK_ZB_RUNTIME_ENABLED=false`. Disk guard and bounded retries protect
  the worker; failures cannot suspend the existing risk loop.

## Evidence and unresolved prerequisites

`research/zerobase/public_evidence_20261023.json` contains the observed public
BTCUSDT product and market records (no credentials). The response confirmed
priceTick 0.1, volumeTick 0.0001, multiplier 1, minimum volume 0.0001 and
minimum order cost 1 USDT. Candle volume semantics are still UNVERIFIED;
matching depth units does not automatically certify candle volume units.

`qualification_probe_20261023.json` records another bounded call to the
previously observed public historical KLine path. It returned HTTP 200 with
error 10006, `not open path`. No alternate exchange or spot data was inserted.
The documented contract API exposes current market funding, not complete
confirmed funding settlement history or a documented candle history endpoint.

`forward_acceptance_20261023.json` records a real public WebSocket capture:
two completed native 1m arrivals were saved. End-to-end acceptance did **not**
pass because a subsequent public API request timed out. This failed result
is retained. Production runtime acceptance must be checked separately.

Current mandatory entry blockers are explicit, independent of elapsed time:

1. Independent raw native 5m and 1m cross-check coverage unavailable.
2. Candle volume provenance/unit not independently established.
3. Required continuous histories (60 raw1m, 450 raw5m / 150 derived15m)
   must mature. Optional 1h/4h absence or disagreement adds no entry gate.
4. Confirmed funding coverage is required before verified net PnL, and actual
   source observations must remain fresh for paper entry permission.

Remaining evaluation work: matched baseline execution/risk normalization,
favorable/unfavorable intrabar outcome bounds, complete daily observation
calendar and a sealed historical evaluation. The existing proxy comparison
remains diagnostic, not native LBank performance. `ENGINEERING_READY` and
strategy adoption are not granted by test count or deployment success.

## Validation and reproduction

```sh
python -m unittest discover -p 'test_lbank*.py'
python -m unittest discover -s tests/zerobase -v
python research/zerobase/forward_acceptance_20261023.py
python -m lbank_zb.tape --input events.jsonl --output report.json
```

At the implementation checkpoint, the full suite passed 425 tests (70 ZB,
including 15 integration cases). The deployment pre-deploy command remains
the full `test_lbank*.py` suite and must pass on the actual pinned image.

The integration tests cover quantity/risk/fee arithmetic, unknown evidence,
stale/crossed/insufficient depth, expiry, no OHLC fill in live mode, independent
paper stop handling under entry halt, partial lots and remaining funding,
late funding, funding coverage, transaction rollback, exact restart state,
duplicate replay, disabled delivery, stale quote cancellation, ambiguous
emergency retry and no backlog revival. Neither unit tests nor the public
probe constitute a profitability backtest.

## Operational verification

After deployment verify exact runtime commit, terminal SUCCESS, native
arrival growth, ZB journal cursor/count growth, saved public evidence and
explicit RAW5M/CANDLE_VOLUME blockers. Keep both outbound switches false.
Rollback is the previously successful `12b39da` image; the added arrival
table is additive and old code does not depend on it. Do not shrink/delete
the existing 500 MB persistent volume.
