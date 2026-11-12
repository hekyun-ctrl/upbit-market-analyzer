"""Early *completed 1m* short-breakout shadow for ZB diagnosis only.

This observer reads the native LBank 1m stream already stored by the bot and
does not send Telegram messages, issue orders or override entry qualification.
A 1m bar is eligible only after receipt of its stream-inferred close.
"""
from contextlib import closing
from hashlib import sha256
import json
import math
from pathlib import Path
import sqlite3
from statistics import median

from lbank_b_retest_research import _atr, trend_priority
from lbank_structural_target_plan import available_barriers, entry_bound, rounded
from research.zerobase.video_entry import Costs, judge_entry

POLICY = "b_first_minute_shadow_v1"
SOURCE = "lbank_native_stream_1m"
MIN_MINUTE_BARS = 21
MAX_MINUTE_AGE = 30
HISTORY = 61
RETENTION = 7*86400


def read_closed_1m_window(path, now):
    """Read latest 61 contiguous, acknowledged 1m candles, no future values."""
    expected = int(now//60)*60-60
    if not Path(path).is_file():
        return []
    with closing(sqlite3.connect(Path(path).resolve().as_uri()+"?mode=ro",
                                 uri=True, timeout=1)) as db:
        db.row_factory = sqlite3.Row
        raw = db.execute("""SELECT b.*,a.received AS received_at
            FROM native_bars b JOIN native_arrivals a
            ON a.period=b.period AND a.start=b.start
            WHERE b.period=60 AND b.quality='stream_closed'
                AND a.source=? AND b.start<=?
            ORDER BY b.start DESC LIMIT ?""",
            (SOURCE, expected, HISTORY)).fetchall()
    result = []
    for row in raw:
        if (row["start"]!=expected or
                not expected+60<=row["received_at"]<=expected+60+30 or
                row["received_at"]>now):
            break
        result.append(dict(row))
        expected-=60
    return result[::-1]


def _valid_minutes(rows):
    if len(rows)<MIN_MINUTE_BARS:
        return False
    try:
        for i,b in enumerate(rows):
            if (type(b["start"]) is not int or b["start"]%60 or
                    i and b["start"]!=rows[i-1]["start"]+60 or
                    b.get("period",60)!=60 or b.get("quality")!="stream_closed"):
                return False
            o,h,l,c,v=(float(b[k]) for k in ("open","high","low","close","volume"))
            receipt=b.get("received_at")
            if (not all(math.isfinite(x) for x in (o,h,l,c,v))
                    or l<=0 or v<0 or not l<=min(o,c)<=max(o,c)<=h
                    or type(receipt) not in (float,int)
                    or not math.isfinite(receipt)
                    or not b["start"]+60<=receipt<=b["start"]+90):
                return False
    except (KeyError, ValueError, OverflowError, TypeError):
        return False
    return True


def evaluate_first_minute_break(minute_bars,five_bars):
    """Require prior 5m regime and first 1m support breach; no second retest.

    No unverified trading claim is inferred from a price-path observation.
    """
    out=dict(policy=POLICY,status="insufficient_minute_history")
    if not _valid_minutes(minute_bars[-HISTORY:]):
        return out
    now_bar=minute_bars[-1]
    # A just-sealed 5m candle may contain the 1m decision candle itself
    # (especially at xx:00/05/10 boundaries). It is NOT historical evidence
    # until after that minute opened. Trim by candle close AND arrival time,
    # rather than incorrectly rejecting every fifth minute or leaking future
    # information into the support/target calculations.
    prior_fives=[b for b in five_bars
                 if b["start"]+300<=now_bar["start"]
                 and b.get("received_at",float("inf"))<=now_bar["received_at"]]
    if len(prior_fives)<96:
        return dict(out,status="insufficient_5m_history")
    from lbank_b_retest_research import _valid
    if not _valid(prior_fives[-min(240,len(prior_fives)):]):
        return dict(out,status="invalid_5m_source")
    if not prior_fives[-1]["start"]+300<=now_bar["start"]<prior_fives[-1]["start"]+600:
        return dict(out,status="misaligned_5m_clock")
    five_bars=prior_fives
    rank=trend_priority(five_bars)
    out.update(trend=rank["trend"],priority=(rank["priorities"] or ["관망"])[0])
    if rank["trend"]!="bearish":
        return dict(out,status="not_bearish")
    baseline=median(float(b["volume"]) for b in minute_bars[-21:-1])
    if baseline<=0:
        return dict(out,status="missing_minute_volume_baseline")
    vol_multiple=now_bar["volume"]/baseline
    support=min(b["low"] for b in five_bars[-6:])
    atr=_atr(five_bars[-16:])
    if not atr or atr<=0:
        return dict(out,status="missing_5m_atr")
    buffer=max(.10*atr,now_bar["close"]*.00010)
    close=now_bar["close"]
    out.update(close=close,broken_support=support,volume_multiple=round(vol_multiple,3),
               minute_start=now_bar["start"],atr_5m=atr,
               initial_break_threshold=support-buffer)
    if now_bar["close"]>=now_bar["open"]:
        return dict(out,status="not_bearish_minute_candle")
    if close>=support-buffer:
        return dict(out,status="support_not_broken")
    if minute_bars[-2]["close"]<support-buffer:
        return dict(out,status="not_first_break_minute")
    if vol_multiple<1.5:
        return dict(out,status="early_volume_below_threshold")
    # A candidate exists even if target or stop fail -- log why, no retroactive
    # upgrade based on a next candle. Stop is fixed from PRE-SIGNAL bars.
    out["early_candidate"]=True
    stop=rounded(max(
        max(b["high"] for b in minute_bars[-4:-1]),
        max(b["high"] for b in five_bars[-2:]))
        +max(.25*atr,close*.00035),.1,up=True)
    out["provisional_stop"]=stop
    try:
        barriers=available_barriers(five_bars[-240:],-1,close,
                                   now_bar["received_at"])
    except (ValueError,KeyError,TypeError,OverflowError):
        return dict(out,status="target_source_invalid")
    if not barriers:
        return dict(out,status="no_known_lower_target")
    target=rounded(barriers[0]["price"]+max(.1,close*.00015),.1,up=True)
    if target>=close:
        return dict(out,status="target_not_below_current_price")
    costs=Costs(min_rr=2.0)
    plan=dict(lane="b_first_minute_shadow",direction=-1,stop=stop,
              target=target)
    verdict, rr=judge_entry(plan,close,costs)
    bound=entry_bound(-1,stop,target,costs.pnl,2.0)
    out.update(stop=stop,target=target,barrier=barriers[0]["price"],
               barrier_available_at=barriers[0]["available_at"],
               minimum_short_entry=bound,net_rr=rr)
    return dict(out,status="qualified_shadow" if verdict=="qualified" else verdict)


def persist_first_minute_shadow(ledger,minutes,fives,*,now,started):
    """Keep immutable one-per-minute audit, no Telegram or broker actions."""
    if not minutes:
        return dict(status="no_minute_data")
    last=minutes[-1];start=last["start"]
    if not start+60<=now<=start+60+MAX_MINUTE_AGE or start+60<=started:
        return dict(status="stale_or_before_start")
    result=evaluate_first_minute_break(minutes,fives)
    fingerprint=sha256(json.dumps(
        [{"start":b["start"],"o":b["open"],"h":b["high"],"l":b["low"],
          "c":b["close"],"v":b["volume"],"quality":b["quality"]}
         for b in minutes[-MIN_MINUTE_BARS:]],
        sort_keys=True,separators=(",",":"),allow_nan=False).encode()).hexdigest()
    info=dict(result,
              source="lbank_native_stream_1m_stream_inferred_not_REST_verified",
              input_sha256=fingerprint,recorded_at=now,
              delivery="shadow_only_NO_TELEGRAM_OR_ORDER",
              cost_rate_assumption="0.12pct_round_trip_plus_model_slippage")
    with ledger.db:
        ledger.db.execute("""CREATE TABLE IF NOT EXISTS
            video_b_first_minute_shadow(
                bar_start INTEGER PRIMARY KEY,evaluated_at REAL NOT NULL,
                status TEXT NOT NULL,evidence TEXT NOT NULL)""")
        ledger.db.execute("DELETE FROM video_b_first_minute_shadow WHERE bar_start<?",
                          (start-RETENTION,))
        ledger.db.execute("""INSERT OR IGNORE INTO video_b_first_minute_shadow
            VALUES(?,?,?,?)""", (start,now,result["status"],
                                json.dumps(info,sort_keys=True,allow_nan=False,
                                           ensure_ascii=False)))
    return result

