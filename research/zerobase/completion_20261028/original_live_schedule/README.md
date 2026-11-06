# Original entry scheduling and every-poll input port — finite checkpoint

Implemented and locally verified; disabled by default and not deployed. This
closes the exact original **entry scheduling/input-port mechanics** unit. It
does not qualify the actual feed, run an unused continuous comparison, prove
profitability, or complete the whole feature.

## What executes

`research/original_live_schedule.py` selects the unchanged original monitor
AST for completed-key/first-ready tracking, sample/confirmation/new-bar due
expressions, sample cursor/cache updates and the **entire original entry
branch**. It executes those statements without rewriting any entry branch or
copying a strategy calculation. The selected AST digest is
`4c1c8b8cbec3892e884e87cad28c41c3c7415612d50dde520b8ce238d2ddabf2`.
The original confirms an existing watch before classifying; periodic ticks
may call confirmation under 30s; expiry/cancellation and subsequent setup-due
classification follow the original branch. This differs from the previous
retained runner's classify→confirm→arm schedule.

Each policy keeps its own sample cursor, ready/setup keys, watch, cached
observations, alert/cooldown state, decisions, plans and input cursor in the
existing capped atomic comparison store. A failed second policy rolls back
both policies and the observation. Restart/idempotency and nondeleting caps
remain in force. This adapter declares capture mode, 60s periodic sampling
and a modeled delivery result; these are explicit comparison inputs, not
newly verified deployed configuration. E3's intentional scheduling experiment
and E5's additional scheduling require their explicit experimental adapters;
they cannot silently be presented as the original scheduler.

All external journal/cache/tracker/delivery calls in this entry slice use
in-memory model ports. The default comparison models successful delivery to
apply the original alert cooldown. A modeled failed delivery preserves the
qualified frozen plan but creates no modeled position or cooldown; valid
no-entry economics remain 0. Neither model sends, records a provider ACK or
claims actual fills. The network/startup/manual-risk/followup loop is outside
this entry-slice adapter. Original six-hour fixed followups are reused for
the diagnostic report, not the complete production management loop.

The original monitor now emits an optional `entry_poll` envelope immediately
before every due check, including quote polls with no entry evaluation due.
It records the snapshot, existing normalized quote receipt identity, journal
availability and original schedule state. It uses the existing disabled-by-
default failure-isolated bounded queue; recording failure cannot escape the
monitor. `stored_poll_points` exports bounded exact envelopes in sidecar row
order, links the original normalized response/request/receipt text and prior
recorded gaps, and preserves absent receipts as null. Sidecar insertion order
is not exchange receive order. Unclean-restart inventory stays in the source
store and continuity remains unverified. No historical poll is interpolated.

## Finite validation and all results

- **86 related tests pass, including 26 new.** They cover exact source AST,
  timer boundaries, new-bar priority, confirm-before-classify, expiry/rearm,
  journal/data failure, atomic rollback/restart/idempotency, modeled delivery
  success/failure, exact poll/quote/gap export, and disabled failure isolation.
- The predeclared same 20 retained development observations in signal15
  (03:25≤UTC<03:46 on 2026-10-20), capture and P1, reproduce exactly across an
  uninterrupted run and DB reopen after observation10. All original input
  hashes remain unchanged. SQLite integrity/FK pass.
- Capture: 1 modeled entry, 1 SL-first, net −9.35927984656063 USDT and closed
  drawdown 9.35927984656063 USDT. P1: no entry, net/drawdown 0 and unresolved
  ratio null. Common final entries0, so confirmation lead/entry improvement
  are null. Full MTM drawdown and emergency counts remain null. This finite
  interval happens to retain the previous scheduler's economic result; it
  does not prove schedules are generally equivalent.
- Presenting the same20 observations as unseen blocks all20 per policy:
  valid/qualified0 and comparison net null. Actual final/native snapshot and
  historical quote receipt qualification0; new independent samples0.
- `comparison_report.json` contains every modeled plan, exact-anchor pairing,
  exclusion/addition reason, timing and observed fixed followup. `steps.json.gz`
  contains every no-signal poll/actual call order/due gate. `input_index.json`
  links every original source text and state hash. `schedule_comparison.json`
  keeps the previous finite result alongside the changed schedule result.
  They are development mechanics, not holdout performance.
- 23 original strategy/helper ASTs, 24 frozen P1/E1/E3/E4/E5/separate-ZB/result
  blobs and the actual four required PR22 design/P0 files are unchanged.

The first functional import encountered a truncated unchanged CCXT cache;
a fresh task-specific Python cache loaded its installed source without an
install or stub. Two initial adapter setup failures (ambiguous original
sample-due selector, missing original local counter template) and one
field-incomplete synthetic delivery fixture failure are preserved. They
were fixed before the final suite; no strategy parameters were tuned.
The final finite run was repeated only because this adapter/input-port code
changed. No completed96-arm replay, legacy467 suite or identical public
HTTP/WS probe was repeated.

## Original finite acceptance matrix

| Original requirement | Technical result | Actual evidence result |
|---|---|---|
| Original entry schedule and poll inputs | Exact selected AST + every-poll capture/export implemented, local tests pass | Not deployed; retained historical original poll stream is incomplete and is never reconstructed |
| Independent durable policy state, all intervals, frozen plans | Atomic restartable bounded implementation verified | Finite20 development observations only |
| Raw receipt→final bar→all derived inputs | Existing sealed/quote integrity mechanics preserved | Official futures final/volume definition and independent native5m absent; full derived-input receipt qualification remains blocked |
| Unused continuous same-input comparison | Existing orchestration and new original-schedule port ready for actual inputs | Not executed; retained data is reused development, not unseen |
| P4 original promotion criteria | Current results and each limitation disclosed | Economic/utility improvement unproven; formal qualified promotion verdict is data insufficient |
| P5 application | Separate review/approval boundary preserved | Not authorized or performed; not a new prerequisite for finishing this research implementation unit |

`RAW5M_INDEPENDENCE_UNVERIFIED` and `CANDLE_VOLUME_UNIT_UNVERIFIED` remain.
The six actual retained chart frames are forming; 1m/5m from that same feed
are not independent5m. No official source decoder is invented without a
documented final/volume schema. No receipt/hash assertion can clear the
source blockers. Funding/fill/spread/slippage evidence is missing; actual
funding history0 days. Original base/adverse fees6/8bp, stop slip2/5bp and
assumed funding1/3bp remain assumptions; manual30/60s and nominal0/15s variants
are correlated scenarios, not independent samples. Prior E5 all48 controls/
contrasts unchanged with E5 entries0; E3/E4 entered-unresolved results stay
null, and P1 no-entry stays0. 1h/4h remain reference only. No profit threshold
relaxation or new100-paper/30-day prerequisite was introduced.

The finite remaining work is actual official source/derived-receipt
qualification when documented sources exist, then one predeclared unused
continuous comparison and the original P4 evidence verdict. Actual rollout
and retention operation remain separately authorized operation. Whole feature
completion cannot be announced while the required actual comparison is
unavailable. No sends/orders/deployment/production merge, Railway changes,
Upbit/CAP/ZIL changes, additional workers/models or new automations occurred.
