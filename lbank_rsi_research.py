"""Prospective, ticker-sampled 5-minute RSI hypotheses. SHADOW ONLY.

LBank perpetual minute snapshots are not genuine exchange OHLCV or order flow.
These features NEVER place orders or independently authorize entry/exit.
"""
import json
import logging
import math
import os
from dataclasses import dataclass, asdict

LOG = logging.getLogger("lbank_neutral_shadow")


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


@dataclass
class NeutralExitCandidate:
    bucket: int
    side: int
    variant: str
    reference_price: float
    reference_rsi: float
    ambiguous: bool


class NeutralExitStudy:
    """Prospective, non-executable study of neutral-stall exits.

    Tests the existing warning separately from a STRICTER DIRECTIONAL
    confirmation. Collects 15/30/60m *closed-sample* moves, never suggests
    filling a trade or actually liquidates a position.
    """
    SCHEMA = 1
    HORIZONS = (3, 6, 12)
    COOLDOWN_BUCKETS = 6

    def __init__(self, path=None):
        self.path = path
        self.pending = []
        self.last_emitted = {}
        self.last_bucket = None
        self._restore()

    def _restore(self):
        if not self.path:
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                state = json.load(f)
            if state.get("schema") != self.SCHEMA:
                return
            pending = []
            for item in state.get("pending", [])[-100:]:
                candidate = NeutralExitCandidate(**item)
                if (candidate.side not in (1, -1) or
                    candidate.variant not in ("neutral", "direction_confirm") or
                    not math.isfinite(candidate.reference_price) or
                    candidate.reference_price <= 0 or
                    not math.isfinite(candidate.reference_rsi)):
                    raise ValueError("invalid neutral study entry")
                pending.append(candidate)
            self.pending = pending
            self.last_emitted = {str(k): int(v)
                                 for k, v in state.get("last_emitted", {}).items()}
            seen = state.get("last_bucket")
            self.last_bucket = None if seen is None else int(seen)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError, OverflowError, AttributeError):
            LOG.warning("Neutral study cache invalid; starting new research window")

    def _save(self):
        if not self.path:
            return
        try:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"schema": self.SCHEMA, "last_bucket": self.last_bucket,
                           "last_emitted": self.last_emitted,
                           "pending": [asdict(c) for c in self.pending]},
                          f, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except (OSError, ValueError, TypeError):
            LOG.warning("Could not persist neutral exit research; no trading effect")

    def observe(self, bucket, closes, rsi_values):
        """Return only structured logs. Never create trade or Telegram alerts."""
        if (len(closes) < 4 or len(rsi_values) < 4 or
            # RSI(14) starts after its price lookback; only tails align.
            not all(math.isfinite(float(x)) for x in
                    (closes[-1], closes[-2], rsi_values[-1], rsi_values[-2])) or
            min(closes[-1], closes[-2]) <= 0 or
            (self.last_bucket is not None and bucket <= self.last_bucket)):
            return []
        events = []
        live = []
        price = float(closes[-1])
        for item in self.pending:
            age = bucket - item.bucket
            if age in self.HORIZONS:
                # A positive signed move favors holding the given side.
                # Negative: exiting at signal price beats waiting to this
                # horizon, ignoring fees and hypothetical order execution.
                signed = item.side * (price / item.reference_price - 1) * 100
                events.append({"event":"horizon", "variant":item.variant,
                               "side":item.side, "start_bucket":item.bucket,
                               "end_bucket":bucket,"minutes":age*5,
                               "signed_hold_move_pct":round(signed,4),
                               "exit_now_advantage_gross_pct":round(-signed,4),
                               "reference_price":item.reference_price,
                               "end_price":price,
                               "ambiguous":item.ambiguous,
                               "source":"sampled_ticker_not_ohlcv",
                               "fills":"unverified"})
            if 0 <= age < max(self.HORIZONS):
                live.append(item)
        self.pending = live
        long_neutral = neutral_stall(closes, rsi_values, 1)
        short_neutral = neutral_stall(closes, rsi_values, -1)
        direction_ambiguous = long_neutral and short_neutral
        for side in (1, -1):
            now_neutral = long_neutral if side == 1 else short_neutral
            previous_neutral = neutral_stall(closes[:-1], rsi_values[:-1], side)
            adverse_5m_pct = side * (price / closes[-2] - 1) * 100
            adverse_rsi_pts = side * (rsi_values[-1] - rsi_values[-2])
            # Both gates were predeclared on exploratory logs; independent
            # prospective 7/30-day validation is required before promotion.
            directional_confirmation = (
                previous_neutral and adverse_5m_pct <= -.025 and
                adverse_rsi_pts <= -1.0
            )
            for variant, eligible in (("neutral",now_neutral),
                                       ("direction_confirm",directional_confirmation)):
                if not eligible:
                    continue
                key = f"{variant}:{side}"
                prior = self.last_emitted.get(key, -999999999)
                if bucket - prior < self.COOLDOWN_BUCKETS:
                    continue
                self.last_emitted[key] = bucket
                candidate = NeutralExitCandidate(bucket, side, variant,
                                                 price, float(rsi_values[-1]),
                                                 direction_ambiguous)
                self.pending.append(candidate)
                events.append({"event":"candidate", "variant":variant,
                               "side":side, "bucket":bucket,
                               "price":price, "rsi":round(rsi_values[-1], 3),
                               "ambiguous":direction_ambiguous,
                               "adverse_5m_pct":round(adverse_5m_pct,4),
                               "adverse_rsi_pts":round(adverse_rsi_pts,3),
                               "source":"sampled_ticker_not_ohlcv",
                               "fills":"unverified"})
        self.last_bucket = bucket
        self._save()
        return events
