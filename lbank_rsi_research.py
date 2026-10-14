"""Prospective, ticker-sampled 5-minute RSI hypotheses. SHADOW ONLY.

LBank perpetual minute snapshots are not genuine exchange OHLCV or order flow.
These features NEVER place orders or independently authorize entry/exit.
"""
from dataclasses import dataclass


def reversal_hypotheses(closes, values):
    """Predeclared 30/70 and near-miss ranges; never use open 5m buckets."""
    if len(values) < 14 or len(closes) < 20:
        return {}
    last = values[-1]
    before = values[-12:-2]
    low, high = min(before), max(before)
    # Compare *completed* bucket closes only; no intra-bucket top/bottom.
    d1 = closes[-1] / closes[-2] - 1
    d2 = closes[-1] / closes[-3] - 1
    r1 = values[-1] - values[-2]
    r2 = values[-1] - values[-3]
    def classify(side):
        prior = low if side == 1 else high
        zone = ("basic" if (prior <= 30 if side == 1 else prior >= 70)
                else "near_miss" if (30 < prior <= 35 if side == 1 else 65 <= prior < 70)
                else "none")
        if zone == "none":
            return None
        # 1-bar is observation only, never actionable from this module.
        one = side * r1 >= 1.0 and side * d1 >= .0002
        two = (side * (values[-1] - values[-2]) > 0 and
               side * (values[-2] - values[-3]) > 0 and
               side * r2 >= 3.0 and side * d2 >= .0008 and
               side * (closes[-2] - closes[-3]) > 0 and
               side * (closes[-1] - closes[-2]) > 0 and
               (30 < last < 60 if side == 1 else 50 < last < 70))
        return {"zone": zone, "prior_rsi": round(prior, 2),
                "first_turn": bool(one), "two_closed_turns": bool(two)}
    return {"long": classify(1), "short": classify(-1)}


def neutral_stall(closes, values, direction):
    """Indication only: two neutral RSI buckets + stalled price/RSI progress.

    Neutrality alone is NOT an exit trigger. A price sampled at one instant
    per minute cannot prove a candle high/low or continuous order flow.
    """
    if direction not in (1, -1) or len(values) < 3 or len(closes) < 3:
        return False
    if not all(40 <= x <= 60 for x in values[-2:]):
        return False
    if (max(closes[-3:]) - min(closes[-3:])) / closes[-1] > .001:
        return False
    if abs(values[-1] - values[-3]) > 3.0:
        return False
    if direction == 1:
        no_progress = closes[-1] <= max(closes[-3:-1]) + .0001 * closes[-1]
    else:
        no_progress = closes[-1] >= min(closes[-3:-1]) - .0001 * closes[-1]
    return bool(no_progress)


@dataclass
class ShadowCandidate:
    bucket: int
    side: int
    zone: str
    entry: float


class ResearchTracker:
    """Forward-check 15/30/60-minute future outcomes, no self-fulfilling trades."""
    def __init__(self):
        self.pending = []
        self.last_by_side_zone = {}
    def observe(self, bucket, close, closes, rsi_values):
        results = []
        carry = []
        for p in self.pending:
            age = bucket - p.bucket
            if age in (3, 6, 12):
                gross = p.side * (close / p.entry - 1)
                # Hypothetical two taker fills at 0.06% each, no slippage.
                net = gross - .0012
                results.append({"event":"horizon", "start_bucket":p.bucket,
                                "bucket":bucket,"side":p.side,"zone":p.zone,
                                "minutes":age*5,"gross_pct":round(gross*100,4),
                                "net_taker_only_pct":round(net*100,4)})
            if age >= 12:
                continue
            if age > 0 and age not in range(1,13):
                continue
            carry.append(p)
        self.pending = carry
        verdict = reversal_hypotheses(closes, rsi_values)
        for side, name in ((1,"long"),(-1,"short")):
            x = verdict.get(name)
            if not x or not x["two_closed_turns"]:
                continue
            key = (side,x["zone"])
            if bucket - self.last_by_side_zone.get(key,-999999) < 6:
                continue
            self.last_by_side_zone[key] = bucket
            self.pending.append(ShadowCandidate(bucket,side,x["zone"],close))
            results.append({"event":"candidate", "bucket":bucket, "side":side,
                            "zone":x["zone"],"price":close,"rsi":rsi_values[-1]})
        return verdict, results
