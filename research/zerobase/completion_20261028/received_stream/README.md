# Sealed received-stream importer — provenance mechanics, unqualified actual feed

This implements the next P3 technical unit from the original PR22 design. It does not complete P3, qualify a strategy, or establish profitability. No deployment, live enablement, sends, orders, private account access, or new market requests occurred.

## Changes

`lbank_received_stream.py` imports an integrity-sealed, append-only segment chain into additive tables in the existing bounded live evidence store. Each wire occurrence retains exact original bytes/SHA256, original UTC receipt text, archive ordinal and collector session. Identical bytes received twice are separate occurrences. Unchanged bar content retains its revision; changed content/final evidence receives the next revision. Earlier occurrences, snapshots, candidate anchors and frozen plans remain immutable.

Segments require consecutive archive ordinals and the exact previous segment seal. A changed/reordered/overlapping segment, unknown record/wire schema, missing/non-UTC receipt, changed hash, backwards receipt clock or undeclared session restart is rejected atomically. Restart and finite-probe-end gaps retain the original attempt times and `lost_records:null`. These archive ordinals are assigned retrospectively from the retained order, not claimed exchange sequences or original collector ingest sequences. Seal verification proves integrity, not authenticity, independence or continuous receipt coverage.

A snapshot references an exact stream occurrence and bar index for every native 1m/5m/research-5m row it contains. Final status and the corresponding receipt must be explicit; nominal close is never substituted for receipt. Future, stale, missing, duplicated, wrong-period or changed bar references fail closed. A later revision cannot rewrite an already sealed snapshot. Five-minute bars must come from their referenced 5m receipt; this importer never aggregates/relabels 1m as independent 5m.

The only built-in wire decoder is the already observed public chart schema. It records `final:null` and no final definition. `tradeTime`, elapsed time and rollover are not official final flags. Unknown final fields/schema fail closed. The code extension interface permits a future reviewed decoder and test-only fixtures; there is no JSON/CLI/config verification bypass, and all importer results retain both source blockers and `qualified:false`. There is currently no implemented official final decoder. Synthetic final adapters exist only in the test file and prove mechanics, not real schema qualification.

Optional `received_snapshot_hash` in a live evidence envelope binds the exact native state, quote and evaluation time to a sealed snapshot. It supplies the original candidate anchor's exact occurrence/revision/known-at through confirmation, frozen plan and process restart. It does not invent a quote receipt, full input availability, indicator provenance or an actual gate vector. Existing live monitor/collector producers remain unchanged and disabled-by-default live evidence remains disabled. Unbound legacy snapshots retain unknown receipt/known-at values.

Atomic import shares the existing evidence transaction, SQLite main/WAL cap and nondeleting record limits. An import failure rolls back seal, raw sources, bars and snapshots together. Idempotent restart does not duplicate them. The whole-store archive now inventories sealed segment/snapshot hashes and received record/bar counts in addition to the existing comparison reference and verified backup hash. This is an offline implementation/test; actual operator-reviewed retention/rotation or deletion has not been performed.

## Real retained material

`retained_sealed.json` contains the original six raw wire frames from `source_probe/audit.json`, with the original receipt strings unchanged. The two sampled chart connections are separated by exact attempt-start/end records, not joined into invented continuous history. The two empty official WS attempts are not converted into chart receipts. The six frames remain four 1m and two 5m forming updates of two bar keys, with revisions `[1,1,2,2,3,4]` across their occurrences.

The CLI imported ten records (six wires, two restart declarations and two end gaps), retained six unique hashes, passed SQLite integrity/foreign-key checks, and produced **zero verified final bars and zero snapshot bindings**. Reimporting the sealed file changed nothing. Six explicit current-time offline binding attempts all failed with `FINAL_RECEIPT_UNVERIFIED`, and all six failures rolled back without changing the ten retained records. Their current evaluation timestamps are not historical transport receive times.

This is the same development material already retained, not new continuous/unseen data, independent 5m evidence, trades, fills, performance observations or a policy comparison. No new count/net/lead/DD claims are drawn from it. `RAW5M_INDEPENDENCE_UNVERIFIED` and `CANDLE_VOLUME_UNIT_UNVERIFIED` remain. Actual official final/volume definitions, independent native 5m receipts, closed-rollover/reconnect coverage, funding settlements and fill/spread/slippage evidence remain unavailable.

## Validation and reproducibility

The focused suite passes **76 tests, including 26 new importer tests**. Cases cover exact receipts, duplicate occurrences versus revisions, stale/future finals, unknown schema/final fields, source hash tampering, sequence/chain conflicts, restart/gap loss extent, native 5m direct binding, bounded storage, atomic disk failure, archive preservation, immutable candidate/plan linkage across restart, exact live snapshot identity, and all six retained frames. The related suite includes the existing retained-real-policy integration; input/output hashes remain unchanged. Synthetic tests establish technical behavior, not profitability.

The first related run lacked `ccxt` on its default import path (36 tests, two import errors). `environment_resolution.json` preserves the failure and the use of the unchanged dependency directory from the previous checkpoint; no dependency stubs, model substitution or new install were used. The final exact command/log, code/input/output SHA256 values, UTC start/end, source heads and limits are in `manifest.json` and `targeted_tests.txt`. `research/validate_received_stream_artifacts.py` verifies all listed bytes and compares both raw frames and receipt strings with the original audit.

Offline CLI examples:

```sh
python research/import_received_stream.py --sealed-input research/zerobase/completion_20261028/received_stream/retained_sealed.json --database /tmp/received.sqlite3 --report /tmp/import-report.json
python research/validate_received_stream_artifacts.py
```

The SQLite file is a reproducible intermediate, not a committed evidence substitute. Full raw bytes, seals, receipts, import report, duplicate report and rejection report are retained here. No P0/P1/E1–E5 full replay, unchanged HTTP/WS probe, legacy 467-test suite or 96-arm comparison was repeated.

## Original-design acceptance and concrete remaining work

| Original requirement | Status after this implementation |
|---|---|
| P0 inert evidence, exact event IDs, no-entry versus unresolved and full development cards | Existing research evidence retained |
| P1 and P2 E1/E3/E4/E5 isolated experiments and necessary development comparisons | Complete at prior checkpoint; no new improvement claim |
| Exact wire occurrence/revision, final-bar-to-snapshot binding, immutable candidate/plan anchor, restart/gap provenance | Importer mechanics implemented/tested; actual retained feed cannot supply verified final bars; live raw-to-snapshot qualification remains unavailable |
| Full actually executed live gate trace and all input receipt coverage | Still required; the optional binding covers native bars, not actual quote receipts/indicator causality or unexecuted gates |
| P3 independent continuous policy state comparison on unseen data, including no-signal intervals, exclusions/additions, timing, frozen outcomes and common costs | Still required; source/receipt/final/volume/funding/fill evidence is missing. Existing development/delay variants cannot satisfy this |
| Reviewed bounded archival retention/rotation and actual runtime activation | APIs and nondeleting pause exist; operator review/activation/rotation unperformed |
| P4 evidence-based promotion decision | Not qualified; E5 final outputs remain unchanged in all 48 prior contrasts, E5 entries zero, prior E3/E4 unresolved outcomes remain null |
| P5 authorized application against latest production with rollback and actual mode verification | Not performed or authorized |

Next technical implementation is exact production gate execution tracing and independent continuous comparison-state orchestration, preserving the already frozen policies and thresholds. Actual feed qualification waits specifically for official final/volume schema, independent native 5m and exact receipt histories. P3 required comparison and P4 qualified review cannot be reported complete using these six forming frames. P5 remains outside the current authorization. There is no additional 100-paper/30-day prerequisite, relaxed criterion, 1h/4h entry gate, or synthetic/proxy data substitution.
