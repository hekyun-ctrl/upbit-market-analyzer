"""Additive evidence ledger for hypothetical LBank performance.

The ledger never turns quote observations into fills or account PnL.  Missing
history remains NULL and ambiguous intrabar order is censored prospectively.
"""
import json
import math
from collections import Counter
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def schema(db):
    db.executescript("""
    CREATE TABLE IF NOT EXISTS trade_evidence(
      id TEXT PRIMARY KEY, opened REAL NOT NULL, frozen_plan TEXT NOT NULL,
      mfe REAL, mae REAL, quote_count INTEGER NOT NULL DEFAULT 0,
      max_quote_gap REAL, execution_verified INTEGER NOT NULL DEFAULT 0);
    CREATE TABLE IF NOT EXISTS model_legs(
      id TEXT, policy TEXT, ts REAL, kind TEXT, fraction REAL, detail TEXT,
      PRIMARY KEY(id,policy,ts,kind));
    CREATE TABLE IF NOT EXISTS warning_applications(
      id TEXT, ts REAL, result TEXT, detail TEXT, PRIMARY KEY(id,ts,result));
    CREATE TABLE IF NOT EXISTS evaluation_events(
      ts REAL, stage TEXT, reason TEXT, detail TEXT,
      PRIMARY KEY(ts,stage));
    """)


def freeze_plan(db, identity, opened, plan):
    payload = json.dumps(plan, ensure_ascii=False, sort_keys=True, allow_nan=False)
    db.execute("INSERT OR IGNORE INTO trade_evidence(id,opened,frozen_plan) VALUES(?,?,?)",
               (identity, opened, payload))


def observe_quote(db, identity, direction, entry, ts, price, previous_at):
    if direction not in (-1, 1) or not all(math.isfinite(float(x)) for x in (entry, ts, price)):
        return
    favorable = direction * (price-entry)
    adverse = max(0., -favorable)
    favorable = max(0., favorable)
    gap = max(0., ts-previous_at) if previous_at is not None else None
    db.execute("""UPDATE trade_evidence SET
      mfe=CASE WHEN mfe IS NULL OR ? > mfe THEN ? ELSE mfe END,
      mae=CASE WHEN mae IS NULL OR ? > mae THEN ? ELSE mae END,
      quote_count=quote_count+1,
      max_quote_gap=CASE WHEN ? IS NULL THEN max_quote_gap
        WHEN max_quote_gap IS NULL OR ?>max_quote_gap THEN ? ELSE max_quote_gap END
      WHERE id=?""", (favorable,favorable,adverse,adverse,gap,gap,gap,identity))


def record_leg(db, identity, policy, ts, kind, fraction, detail):
    db.execute("INSERT OR IGNORE INTO model_legs VALUES(?,?,?,?,?,?)",
               (identity,policy,ts,kind,fraction,json.dumps(detail,ensure_ascii=False,
                sort_keys=True,allow_nan=False)))


def record_warning(db, identity, ts, result, detail=None):
    db.execute("INSERT OR IGNORE INTO warning_applications VALUES(?,?,?,?)",
               (identity,ts,result,json.dumps(detail or {},ensure_ascii=False,sort_keys=True,allow_nan=False)))


def record_evaluation(db, ts, stage, reason, detail=None):
    db.execute("INSERT OR REPLACE INTO evaluation_events VALUES(?,?,?,?)",
               (ts,stage,reason,json.dumps(detail or {},ensure_ascii=False,sort_keys=True,allow_nan=False)))


def intrabar_uncertainty(state, direction, stop, target, since, now):
    """Return stop/target/both for wholly observed completed 1m bars only."""
    if not state or direction not in (-1,1):
        return None
    hits=[]
    for bar in state.get("minute_bars",[]):
        try:
            start=float(bar["start"]); end=start+60
            if (bar.get("quality")!="stream_closed" or start < since or end > now):
                continue
            low,high=float(bar["low"]),float(bar["high"])
            if not (0 < low <= high and math.isfinite(low) and math.isfinite(high)):
                continue
            stop_hit = low <= stop if direction==1 else high >= stop
            target_hit = high >= target if direction==1 else low <= target
            if stop_hit and target_hit:return "both"
            if stop_hit:hits.append("stop")
            if target_hit:hits.append("target")
        except (KeyError,TypeError,ValueError,OverflowError):
            continue
    return hits[0] if hits else None


def evaluation_summary(db, start, end):
    rows=db.execute("SELECT stage,reason FROM evaluation_events WHERE ts>=? AND ts<? ORDER BY ts,stage",
                    (start,end)).fetchall()
    by_stage={}
    for row in rows:
        by_stage.setdefault(row["stage"],Counter())[row["reason"]]+=1
    return {"checks":len(rows),"stages":{s:dict(v) for s,v in by_stage.items()},
            "reconciled":sum(sum(v.values()) for v in by_stage.values())==len(rows)}


def coverage(db):
    row=db.execute("SELECT MIN(ts) lo,MAX(ts) hi,COUNT(*) n FROM samples").fetchone()
    return {"from":row["lo"],"to":row["hi"],"samples":row["n"],
            "continuous_verified":False,"retention_is_backtest":False}


def trade_rows(db):
    tables={r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    has_evidence="trade_evidence" in tables;has_legs="model_legs" in tables
    rows=[]
    for r in db.execute("SELECT * FROM signals ORDER BY opened,id,policy"):
        p=json.loads(r["plan"]); ev=(db.execute("SELECT * FROM trade_evidence WHERE id=?",(r["id"],)).fetchone()
                                      if has_evidence else None)
        legs=[]
        leg_rows=(db.execute("SELECT * FROM model_legs WHERE id=? AND policy=? ORDER BY ts,kind",(r["id"],r["policy"]))
                  if has_legs else [])
        for x in leg_rows:
            legs.append({"ts":x["ts"],"kind":x["kind"],"fraction":x["fraction"],"detail":json.loads(x["detail"])})
        rows.append({"id":r["id"],"policy":r["policy"],"opened":r["opened"],"closed":r["closed"],
          "status":r["status"],"net_usdt_model":r["net"],"direction":p.get("direction"),
          "entry":p.get("price"),"stop":p.get("stop"),"target1":p.get("target1"),
          "target2":p.get("target2"),"strategy":p.get("strategy"),"legs":legs,
          "mfe_quote_usdt_per_btc":ev["mfe"] if ev else None,
          "mae_quote_usdt_per_btc":ev["mae"] if ev else None,
          "quote_count":ev["quote_count"] if ev else None,
          "max_quote_gap_seconds":ev["max_quote_gap"] if ev else None,
          "frozen_evidence_available":bool(ev),"execution_verified":False,"actual_account_pnl":False})
    return rows


def paired_comparison(db, baseline="warning_exit", candidate="trend_runner"):
    pairs=db.execute("""SELECT a.id,a.net baseline,b.net candidate,a.status baseline_status,b.status candidate_status
      FROM signals a JOIN signals b ON a.id=b.id WHERE a.policy=? AND b.policy=?""",
      (baseline,candidate)).fetchall()
    result={"completed_pairs":0,"improved":0,"worsened":0,"unchanged":0,"excluded":0,
            "baseline_net":0.,"candidate_net":0.,"difference_usdt":0.,"actual_account_pnl":False}
    for p in pairs:
        if p["baseline"] is None or p["candidate"] is None:
            result["excluded"]+=1;continue
        d=p["candidate"]-p["baseline"];result["completed_pairs"]+=1
        result["baseline_net"]+=p["baseline"];result["candidate_net"]+=p["candidate"]
        result["difference_usdt"]+=d
        result["improved" if d>1e-9 else "worsened" if d<-1e-9 else "unchanged"]+=1
    return result


def closing_day_summary(db, day):
    start=datetime.fromisoformat(day).replace(tzinfo=KST).timestamp();end=start+86400
    rows=db.execute("SELECT opened,net FROM signals WHERE closed>=? AND closed<? AND net IS NOT NULL",(start,end)).fetchall()
    return {"day":day,"closed_trades":len(rows),"whole_trade_net_usdt_model":sum(r["net"] for r in rows),
            "carry_in":sum(r["opened"]<start for r in rows),"mark_to_market_account_day":False,
            "actual_account_pnl":False}


def study_diagnostics(db):
    reasons=Counter()
    for r in db.execute("SELECT detail FROM decisions"):
        try:
            d=json.loads(r["detail"]);ctx=d.get("study_context") or {}
            reason=ctx.get("reason") or ("ready_no_setup" if ctx.get("ready") else "not_recorded")
            reasons[reason]+=1
        except Exception:reasons["invalid_record"]+=1
    return dict(reasons)


def split_report(message, limit=3900):
    """Deterministic paragraph/line splitting below Telegram's 4096 limit."""
    if not isinstance(message,str) or limit<100:
        raise ValueError("invalid report")
    chunks=[];current=""
    for line in message.splitlines(keepends=True):
        while len(line)>limit:
            if current:chunks.append(current);current=""
            chunks.append(line[:limit]);line=line[limit:]
        if current and len(current)+len(line)>limit:
            chunks.append(current);current=""
        current+=line
    if current or not chunks:chunks.append(current)
    return chunks
