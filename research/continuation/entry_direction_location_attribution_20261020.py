"""Reproduce accepted-path target-room semantics on archived real incident inputs.
No indicator fitting, new entry rule, live connection or order placement.
"""
import argparse, ast, hashlib, json
from pathlib import Path
from decimal import Decimal

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--input", required=True)
    ap.add_argument("--output", required=True)
    args=ap.parse_args()
    raw=Path(args.input).read_bytes(); data=json.loads(raw)
    source=data["target_room_source"]; bs=source.encode()
    blob=hashlib.sha1(b"blob "+str(len(bs)).encode()+b"\0"+bs).hexdigest()
    assert blob==data["source_blob"]=="488d61985ae5bf54d139689fc4664cd933d2da2d"
    tree=ast.parse(source)
    keep={"confirmed_pivot_barriers","observed_target_room"}
    nodes=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in keep]
    assert {n.name for n in nodes}==keep
    ns={}; exec(compile(ast.Module(body=nodes,type_ignores=[]),"<pinned-source>","exec"),ns)
    band14=next(r for r in data["band"]["results"] if r["id"]==14)
    bars=band14["inputs"]
    assert len(bars)==20 and all(b["start"]-a["start"]==300 for a,b in zip(bars,bars[1:]))
    assert bars[-1]["close"]==81974.1
    quotes=[81971.0,82011.8,82014.0]
    # Same bars and SAME target for a controlled price-only probe.
    controlled=[{"quote":p,"target":82627.4,**ns["observed_target_room"](bars,1,p,82627.4)} for p in quotes]
    assert controlled[0]["target_room"]=="conflict" and controlled[0]["observed_barrier"]==81987.0
    assert all(r["target_room"]=="unknown" and r["observed_barrier"] is None for r in controlled[1:])
    # Correct comparison control: known target BEFORE resistance is clear.
    target_before_barrier=ns["observed_target_room"](bars,1,81971.0,81972.0)
    assert target_before_barrier["target_room"]=="clear"
    assert Decimal(str(bars[-1]["close"]))<Decimal("81987")<Decimal("82011.8")
    rows=[]
    for c in data["old_cases"]["audit"]["cases"]:
        row=dict(c, audit_type="reuse_prior_preserved_outcome_no_rerun",
                 policy_version_comparable_to_current=False,
                 frozen_entry_direction_evidence=None,original_first_qualification=None)
        row["entry_message"]=next(r["message"] for r in data["old_cases"]["entries"] if r["at"]==c["entry_at"])
        rows.append(row)
    for e in data["recent"]["entries"]:
        number=e["reference"]
        prefix="reference="+str(number)+" "
        events=[x for x in data["recent"]["delivery_events"] if prefix in x["message"]]
        row={"reference":number,"entry_at":e["time"],"entry":e["entry"],"higher_direction_intact":e["upper"],
             "target_distance_atr":e["target_atr"],"overrides":e["overrides"],
             "first_target_before_followup":None,"actual_fill_verified":False,
             "policy_version_comparable_to_current":number>=14,
             "followup_events":events,
             "entry_basis_complete_replay_available":False,
             "original_first_qualification":None,
             "band_position":next({k:v for k,v in b.items() if k!="inputs"} for b in data["band"]["results"] if b["id"]==number)}
        rows.append(row)
    assert sorted(r["reference"] for r in rows)==list(range(8,16))
    report={"schema":data["schema"],"input_sha256":hashlib.sha256(raw).hexdigest(),"source_blob":blob,
      "all_retained_cases":rows,
      "reference14_fixed_bars_price_only_probe":controlled,
      "control_target_before_barrier":target_before_barrier,
      "last_completed_5m_close":81974.1,
      "old_resistance":81987.0,
      "completed_close_above_old_resistance":False,
      "what_is_reproduced":"Original target-room function changes conflict to unknown when quote crosses a known pivot while completed 5m inputs and target remain fixed. Actual logs show blocked 12:35 and qualified/sent 12:37 under same last5 start; 1m inputs and other plan fields also changed, so the complete accepted-path causal contribution is NOT identified.",
      "full_classifier_replay":False,
      "missing_replay_inputs":["Original 65-360 closed 1m bars at each #14 observation","Full original 5m snapshot at each observation rather than only last20 used in this component probe","All contemporaneous quotes and publication times for a true first possible qualification"],
      "testable_correction_hypothesis":"Track earlier known resistance/support and distinguish quote crossing, retest and confirmed structural acceptance rather than silently dropping a crossed pivot from candidate attribution. Determine the strategy-specific entry contract and compare opportunities and outcomes before selecting any live veto.",
      "new_live_veto_implemented":False,"production_code_changed":False,"replacement_certified":False,
      "new_backtest_or_independent_profit_evidence":False,
      "next_step":"Capture/recover complete snapshots if retained; otherwise instrument non-gating frozen candidate evidence capture on the approved source, then fixed-test one explicit correction including missed-opportunity effects. No invented missing minute bars or proof of counterfactual profits.",
      "limits":["8 cases were already observed and policies differ; no single failure-rate/alpha estimate.",
                "#8 later target after earlier invalidation is not reclassified as valid-alert TP1 success.",
                "#9 missing coverage remains unknown. #13-15 TP1 outcomes not newly measured here.",
                "#15 urgent is user-reported but not matched to retrieved delivery logs; not marked successful.",
                "Unknown target room is not labeled clear; current eligibility may still allow the plan.",
                "A candle-close confirmation requirement or all-higher-direction veto is not deployed or proved profitable."]}
    Path(args.output).write_text(json.dumps(report,ensure_ascii=False,indent=2)+"\n")
    print(json.dumps({"retained_cases":len(rows),"component_probe":"conflict -> unknown -> unknown","source_blob_verified":blob,
                      "full_classifier_replay":False,"production_code_changed":False}))
if __name__=="__main__": main()

