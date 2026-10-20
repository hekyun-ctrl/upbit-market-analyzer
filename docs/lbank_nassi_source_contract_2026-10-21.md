# Nassi grouped-decline source reconciliation — 2026-10-21 03:30 KST

Finite source-contract step completed; no new trading rule, performance calculation or deployment. Machine-readable card: research/continuation/nassi_source_contract_20261021_0330.json.

## What was actually read
Pinned lbank_public_techniques.py blob1c6ca9de1c42a49feb154a554fba3f9d78f3c7b8 at production25388b4, plus prior docs/lbank_four_traders_review_2026-10-19.md. Prior document attests7fullcaption reviews, but transcripts/timestamps are not stored there.
Two original video URLs from that document were attempted: https://www.youtube.com/watch?v=rxRaZowvSBA and https://www.youtube.com/watch?v=JqMbhhwKnWE . Web retrieval failed; purpose-built vidIQ Korean transcript requests each returned INVALID_ARGUMENT without transcript. No retries after those errors, no alternative-language substitution, no login or access bypass. Do not claim full videos were watched/read in this run, or fabricate timestamps.
Readable primary firsthand interview: https://weekly.donga.com/economy/article/all/11/2913751/1 ,published2021-09-11. This is the author's spoken explanation in interview, not independent performance audit. Page original byteSHA unavailable; publication/retrieval and scope recorded, not claimed as immutable bytearchive.

## Primary-supported contract (paraphrase)
The interview describes a countertrend long rebound after three substantial downward steps on5minute bars. For beginners it discusses entry at the third bar's close; more advanced intrabar low identification uses a1minute view. Other timeframes are discussed for different volatility. Initial fixed stop, exact material-step/reset formula, TP1/TP2 and a complete mechanical exit contract are not specified in the accessible source. Averaging-down management is discussed in the original, but is outside the authorized fixed-risk implementation. Headline personal returns are unverified author claims, not accepted BTCUSDT strategy evidence.

## Actual implementation differences
| Element | Actual code | Current primary verification |
| --- | --- | --- |
| 5minute timeframe | completed5m | supported as an original timeframe; not itself an invented adaptation |
| Material decline | max(half prior20 median TR,1bp quote) | numeric rule not verified |
| Observation window |10completed bars | not verified |
| Counting | exactly3 material red-close steps; near closes merge | exact quantitative grouping not verified |
| Reset | recovery by one computed step resets | not verified |
| Trigger delay | closed1m candle breaks previous3minute extreme and setup level | not verified as original trigger |
| Additional gates | context,RSI slope,price deviation,plan barriers/cost/stop | current-system additions |
| Watch | recheck30–240seconds | current-system addition |
| Protection | bounded initialstop and Rtargets/partialmanagement, no averagingdown | explicit transfer contract, not original replica |
| Market/timeframe switches | fixed5m study | original qualitative adaptation not replicated |

The accessible source supports qualitative grouping/timing, not our numeric implementation. Our2024/2025 sparse/negative results evaluate this composite hypothesis; they neither prove nor disprove original trader profitability. Prior metadata/third-party summaries were used only to discover sources, not as rule or return proof. No original instructions to expand leverage, margin or loss are imported.

## Concrete decision
Complete reproducible original strategy contract is missing. No profitable candidate or deployment. A correspondence/performance test that claims faithful original replication would require inventing missing fields; therefore none was run. No further unchanged Nassi retrieval or calendar replays absent material new source access.
Next existing source: Kim Jikseon doubleBB, original listed video eGaaM0Pz2Lk and prior documented settings20/2close +4/4open +20SMA direction. Verify primary trigger/stop/exit once; distinguish original parameter evidence from our fixed-risk transfer. If those discretionary fields stay unavailable, do not claim replica or spend repeated runs auditing the same source. Freeze one bounded existing-method comparison (source band candidate timing versus current extra1m/watch confirmation, identical risk/cost/native-quality gates) BEFORE computing any new outcomes, explicitly label it a transfer hypothesis, and use paired episodes/failure controls. No new techniques or unconstrained weights. This is a proposed research comparison, not authorization to weaken live gates.
Existing reference alerts remain enabled; newest observed deployment4bbda6c7 SUCCESS/source25388b4 unchanged. No source update or operational restart.
