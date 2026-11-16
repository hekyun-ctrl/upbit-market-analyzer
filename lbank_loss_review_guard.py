"""Research-only A/B/C independent evaluation and hard production-entry review lock.

A/B/C can occur independently on a completed 5m bar. This is a diagnostic
journal, not an order decision, Telegram trade approval, or claim of win rate.
2026-11-16: formal trade recommendations are LOCKED until a new reviewed
change explicitly lifts this lock after real-fill replay verification.
"""
import hashlib
import json
import math
from statistics import median

POLICY = "zb_loss_review_v1"
MIN_NET_RR = 2.0
FORMAL_ENTRY_LOCKED = True
LOCK_REASON = "seven_real_trades_loss_review_and_out_of_sample_replay_pending"


def entry_delivery_allowed():
    # Intentionally not an environment flag: a Railway variable change cannot
    # silently turn unverified 50x recommendations back on.
    return not FORMAL_ENTRY_LOCKED


def parallel_observation(bars):
    """All three lanes evaluated using only preceding completed 5m candles.

    Each lane has its own status, including no-hit / extended / reversal-risk.
    Never derives targets outside known historical price barriers.
    """
    result = {"policy": POLICY, "entry_approved": False}
    if len(bars) < 21:
        return dict(result, status="insufficient_history")
    rows = bars[-21:]
    try:
        prior = None
        for b in rows:
            t = b["start"]
            if (type(t) is not int or t % 300 or
                    (prior is not None and t != prior + 300) or
                    b.get("quality") != "stream_closed"):
                return dict(result, status="invalid_or_gapped_candles")
            nums = [float(b[k]) for k in ("open","high","low","close","volume")]
            o,h,l,c,v = nums
            receipt = b.get("received_at")
            if (not all(math.isfinite(x) for x in nums) or
                    not 0 < l <= min(o,c) <= max(o,c) <= h or v < 0 or
                    type(receipt) not in (int,float) or receipt < t+300):
                return dict(result, status="invalid_or_gapped_candles")
            prior = t
    except (KeyError,ValueError,TypeError,OverflowError):
        return dict(result, status="invalid_or_gapped_candles")
    history,now = rows[:-1],rows[-1]
    base=median(float(b["volume"]) for b in history)
    if base<=0:
        return dict(result,status="missing_volume_reference")
    trs=[]
    for first,second in zip(history[-15:-1],history[-14:]):
        trs.append(max(second["high"]-second["low"],
                       abs(second["high"]-first["close"]),
                       abs(second["low"]-first["close"])))
    atr=sum(trs)/len(trs)
    if atr<=0:
        return dict(result,status="invalid_atr")
    c,o,h,l,v = (float(now[k]) for k in ("close","open","high","low","volume"))
    support=min(float(b["low"]) for b in history[-6:])
    resistance=max(float(b["high"]) for b in history[-12:])
    prior_low=min(float(b["low"]) for b in history)
    prior_high=max(float(b["high"]) for b in history)
    buf=max(c*.00035,atr*.25)
    multiple=v/base
    extended=abs(c-o)>1.8*atr
    # Distinguishes a downtrend bounce from a fresh, actionable short.
    rebound_risk=(c-min(float(b["low"]) for b in rows[-7:])) >= 1.4*atr
    a_hit=h>=resistance-buf and c<resistance-buf and c<o and multiple>=1.2
    b_hit=c<support-.1*buf and c<o and multiple>=1.5
    c_hit=c>resistance+.1*buf and c>o and multiple>=1.5
    lanes={}
    for lane,hit in (("A",a_hit),("B",b_hit),("C",c_hit)):
        if not hit:
            status="no_event"
        elif extended:
            status="already_extended"
        elif lane in ("A","B") and rebound_risk:
            status="reversal_risk"
        else:
            status="observed_not_entry"
        # Do not project a theoretical 2R target where none is historically
        # visible, and never represent any observation as a qualified trade.
        known_barrier=(prior_low if lane in ("A","B") else prior_high)
        if (lane in ("A","B") and known_barrier>=c or
                lane=="C" and known_barrier<=c):
            known_barrier=None
        lanes[lane]={"triggered":hit,"status":status,
                     "known_barrier":known_barrier}
    sample=dict(start=now["start"],close=c,support=support,
                resistance=resistance,volume_multiple=round(multiple,3),
                atr14=round(atr,3),rebound_risk=rebound_risk,
                large_body=extended,lanes=lanes)
    result.update(sample)
    return dict(result,status="observed")


def persist_parallel_review(ledger,bars):
    """Write immutable, reproducible diagnostics; never send Telegram/orders."""
    sample=parallel_observation(bars)
    if sample["status"]!="observed":
        return sample
    payload=json.dumps(sample,sort_keys=True,allow_nan=False)
    digest=hashlib.sha256(payload.encode("utf-8")).hexdigest()
    with ledger.db:
        ledger.db.execute("""CREATE TABLE IF NOT EXISTS zb_parallel_review(
          bar_start INTEGER PRIMARY KEY, policy TEXT NOT NULL,
          input_hash TEXT NOT NULL, evidence TEXT NOT NULL)""")
        ledger.db.execute(
          "INSERT OR IGNORE INTO zb_parallel_review VALUES(?,?,?,?)",
          (sample["start"],POLICY,digest,payload))
        ledger.db.execute("DELETE FROM zb_parallel_review WHERE bar_start<?",
                          (sample["start"]-32*86400,))
    return sample
