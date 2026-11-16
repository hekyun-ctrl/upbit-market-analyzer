"""Read-only, cost-aware audit of actual fills and two identical-data policies.

Usage:
 python -m research.zb_trade_review --native-db /data/video_alert_native.sqlite3 \
   --fills /data/private_lbank_fills.csv --output /data/private_trade_review.json

CSV: trade_id,time_kst,action,side,price,quantity,realized_pnl,fee
action=open|close, side=long|short. Fees are independent of realized_pnl
by default. Source fills and their attribution to advice require verification.
This tool NEVER authorizes trading, modifies exchange state or sends Telegram.
"""
import argparse
import csv
from datetime import datetime,timedelta,timezone
from decimal import Decimal,InvalidOperation
import json
import os
from pathlib import Path
import sqlite3
from collections import defaultdict
from research.zerobase.video_entry import Costs,replay

KST=timezone(timedelta(hours=9))
SOURCE="lbank_native_1m_derived_5m"


def load_native(dbpath, period=300):
    """Only independently received, sealed native stream bars, no interpolation."""
    dbpath=Path(dbpath)
    if not dbpath.is_file():
        return []
    with sqlite3.connect(dbpath.resolve().as_uri()+"?mode=ro",uri=True) as db:
        db.row_factory=sqlite3.Row
        vals=db.execute("""
            SELECT b.*, a.received AS received_at FROM native_bars b
            JOIN native_arrivals a ON a.period=b.period AND a.start=b.start
            WHERE b.period=? AND b.quality='stream_closed'
              AND a.source=? ORDER BY b.start
            """,(period, SOURCE if period==300 else "lbank_native_stream_1m")).fetchall()
    out=[]
    for val in vals:
        row=dict(val)
        if (row["received_at"]>=row["start"]+period
                and row["received_at"]<=row["start"]+period+30):
            out.append(row)
    return out


def fill_csv(path):
    """Use Decimal for exact screenshot/account fee and PnL reconciliation."""
    result=[]
    with open(path,encoding="utf-8-sig",newline="") as fh:
        read=csv.DictReader(fh)
        required={"trade_id","time_kst","action","side","price","quantity",
                  "realized_pnl","fee"}
        if not required.issubset(read.fieldnames or []):
            raise ValueError("missing_fill_columns")
        for row in read:
            action=row["action"].strip().lower()
            side=row["side"].strip().lower()
            if action not in ("open","close") or side not in ("long","short"):
                raise ValueError("invalid_action_or_side")
            try:
                dt=datetime.strptime(row["time_kst"].strip(),
                                     "%Y-%m-%d %H:%M:%S").replace(tzinfo=KST)
                nums={k:Decimal(row[k]) for k in
                      ("price","quantity","realized_pnl","fee")}
            except (ValueError,InvalidOperation,TypeError) as exc:
                raise ValueError("invalid_fill") from exc
            if not row["trade_id"].strip() or nums["price"]<=0 or nums["quantity"]<=0 or nums["fee"]<0:
                raise ValueError("invalid_fill_values")
            if action=="open" and nums["realized_pnl"]!=0:
                raise ValueError("open_pnl_must_be_zero")
            result.append(dict(trade_id=row["trade_id"],time_kst=dt,
                               timestamp=dt.timestamp(),action=action,side=side,**nums))
    return result


def group_fills(fills):
    grouped=defaultdict(list)
    for f in fills:
        grouped[f["trade_id"]].append(f)
    result=[]
    for id,group in sorted(grouped.items(),
                            key=lambda x:min(f["timestamp"] for f in x[1])):
        group=sorted(group,key=lambda z:(z["timestamp"],0 if z["action"]=="open" else 1))
        sides={f["side"] for f in group}
        opens=[x for x in group if x["action"]=="open"]
        closes=[x for x in group if x["action"]=="close"]
        qty_open=sum((x["quantity"] for x in opens),Decimal(0))
        qty_close=sum((x["quantity"] for x in closes),Decimal(0))
        fees=sum((x["fee"] for x in group),Decimal(0))
        gross=sum((x["realized_pnl"] for x in closes),Decimal(0))
        matched=(len(sides)==1 and bool(opens) and bool(closes) and
                 abs(qty_open-qty_close)<=Decimal(".00001") and
                 max(f["timestamp"] for f in opens)<=min(f["timestamp"] for f in closes))
        result.append(dict(id=id,side=next(iter(sides)) if len(sides)==1 else "mixed",
            entered_kst=min((f["time_kst"].strftime("%Y-%m-%d %H:%M:%S") for f in opens),
                            default=None),
            first_entry_ts=min((f["timestamp"] for f in opens),default=None),
            exit_kst=max((f["time_kst"].strftime("%Y-%m-%d %H:%M:%S") for f in closes),
                         default=None),
            fill_rows=len(group),entry_quantity=float(qty_open),closed_quantity=float(qty_close),
            gross_realized_usdt=float(gross),fee_usdt=float(fees),
            estimated_net_usdt=float(gross-fees),matched=matched,
            source_note="Assumes reported realized PnL excludes separately shown fees"))
    return result


def _last_contiguous(bars, before, period):
    """Only bars truly CLOSED and RECEIVED before a fill; never future info."""
    candidates=[b for b in bars if b["start"]+period<=before and
                b["received_at"]<=before]
    if not candidates:
        return []
    tail=[candidates[-1]]
    for b in reversed(candidates[:-1]):
        if b["start"]!=tail[-1]["start"]-period:break
        tail.append(b)
    return list(reversed(tail))


def _aggregate_completed(fives, period):
    n=period//300
    groups=defaultdict(list)
    for b in fives:
        groups[b["start"]//period*period].append(b)
    completed=[]
    for key,rows in sorted(groups.items()):
        if (len(rows)==n and rows[0]["start"]==key and
                all(y["start"]==x["start"]+300 for x,y in zip(rows,rows[1:]))):
            completed.append(rows[-1]["close"])
    return completed


def entry_context(trade,fives,minutes):
    """Causal pre-entry snapshot, not a judgment of fault or a trade signal."""
    if trade["first_entry_ts"] is None:
        return {"status":"missing_entry"}
    t=trade["first_entry_ts"]
    bars=_last_contiguous(fives,t,300)
    ones=_last_contiguous(minutes,t,60)
    result={"entry_5m_bars":len(bars),"entry_1m_bars":len(ones),
            "minute_data": "available" if len(ones)>=21 else "insufficient",
            "five_data": "available" if len(bars)>=21 else "insufficient"}
    if len(bars)<21:
        return dict(result,status="insufficient_historical_5m")
    history=bars[-21:]
    prev=history[-1]
    trs=[max(y["high"]-y["low"],abs(y["high"]-x["close"]),
             abs(y["low"]-x["close"])) for x,y in zip(history[-15:-1],history[-14:])]
    atr=sum(trs)/len(trs)
    close=prev["close"]
    result.update(status="observed",asof_5m_close_kst=datetime.fromtimestamp(
                  prev["start"]+300,KST).strftime("%Y-%m-%d %H:%M:%S"),
                  close_5m=close,atr14=round(atr,2),
                  prior_6_low=min(b["low"] for b in history[-6:]),
                  prior_12_high=max(b["high"] for b in history[-12:]),
                  first_1m_close=ones[-1]["close"] if ones else None,
                  completed_15m=len(_aggregate_completed(bars,900)),
                  completed_1h=len(_aggregate_completed(bars,3600)))
    return result


def model_summary(replay_result):
    """Nonoverlapping next-open model entries, not exchange executions."""
    candidates=[x for x in replay_result["events"]
                if x["status"]=="qualified" and
                x.get("outcome",{}).get("entry_start") is not None]
    candidates.sort(key=lambda e:e["outcome"]["entry_start"])
    accepted=[]
    occupied_until=0
    rejected={"overlap":0,"unknown_outcome":0}
    for e in candidates:
        out=e["outcome"]
        start=out["entry_start"]
        if start<occupied_until:
            rejected["overlap"]+=1
            continue
        resolved=out["status"] in ("stop","target")
        if not resolved:
            rejected["unknown_outcome"]+=1
            occupied_until=start+12*300
            continue
        occupied_until=out["at"]+300
        accepted.append(out["model_pnl_per_btc"]/out["entry"])
    wins=[x for x in accepted if x>0]
    losses=[x for x in accepted if x<0]
    gross_win=sum(wins)
    gross_loss=-sum(losses)
    return dict(qualified_candidates=len(candidates),resolved_nonoverlap=len(accepted),
        profitable=len(wins),losing=len(losses),win_rate=(len(wins)/len(accepted)
        if accepted else None),net_return_sum=sum(accepted),
        profit_factor=gross_win/gross_loss if gross_loss>0 else None,
        skipped=rejected,qualification_note="Next-5m-open model, no live quote; not realized PnL")


def compare_policies(fives):
    """Strict same native 5m input/receipts. No fabricated or nominal candles."""
    return {
      "baseline_1p5":model_summary(replay(fives,nominal=False,costs=Costs(min_rr=1.5),
                                           rsi_sequence=True)),
      "research_2p0":model_summary(replay(fives,nominal=False,costs=Costs(min_rr=2.0),
                                           rsi_sequence=True)),
      "input_completed_fives":len(fives),
      "data_source":"LBank native stream inferred, NOT independent REST certified",
      "result_type":"OFFLINE_MODEL_ONLY_NOT_LIVE_FILL",
      "strategy_approved":False,
    }


def make_report(native_db,fills_csv=None):
    fives=load_native(native_db,300)
    minutes=load_native(native_db,60)
    r={"scope":"privacy_preserving_internal_audit","live_trading_approved":False,
       "complete_native_fives":len(fives),"complete_native_minutes":len(minutes),
       "policy_comparison":compare_policies(fives)}
    if fills_csv is not None:
        positions=group_fills(fill_csv(fills_csv))
        for pos in positions:
            pos["pre_entry"]=entry_context(pos,fives,minutes)
        complete=[x for x in positions if x["matched"]]
        r["actual_positions"]=positions
        r["actual_totals"]=dict(complete_trades=len(complete),incomplete_trades=len(positions)-len(complete),
            wins=sum(x["estimated_net_usdt"]>0 for x in complete),
            losses=sum(x["estimated_net_usdt"]<0 for x in complete),
            gross_realized_usdt=sum(x["gross_realized_usdt"] for x in complete),
            fees_usdt=sum(x["fee_usdt"] for x in complete),
            estimated_net_usdt=sum(x["estimated_net_usdt"] for x in complete))
        r["attribution_note"]="Cannot infer which trades came from assistant recommendations."
    else:
        r["actual_totals"]="NO_PRIVATE_FILL_CSV_AVAILABLE"
    return r


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument("--native-db",required=True)
    parser.add_argument("--fills")
    parser.add_argument("--output")
    opt=parser.parse_args()
    report=make_report(opt.native_db,opt.fills)
    payload=json.dumps(report,ensure_ascii=False,indent=2,allow_nan=False)
    if opt.output:
        dest=Path(opt.output)
        dest.parent.mkdir(parents=True,exist_ok=True)
        fd=os.open(dest,os.O_CREAT|os.O_TRUNC|os.O_WRONLY,0o600)
        with os.fdopen(fd,"w",encoding="utf-8") as fh:
            fh.write(payload+"\n")
        print("AUDIT_SAVED_private=true input_bars="+str(report["complete_native_fives"]))
    else:
        print(payload)


if __name__=="__main__":
    main()
