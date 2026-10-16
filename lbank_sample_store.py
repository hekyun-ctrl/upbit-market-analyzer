"""Crash-safe bounded cache of LBank *perpetual ticker* observations.

Two independently atomic replicas protect a short redeploy from interrupted or
overlapping writes. Every loaded observation is revalidated for symbol, time,
freshness and continuity; backups never bypass market warm-up after a real gap.
This is NOT exchange OHLCV. Cooldown / alert count are stored with samples.
"""
import json
import logging
import math
import os
import tempfile
import time

LOG = logging.getLogger("lbank_sample_store")
DEFAULT_ALERT = {"day": None, "count": 0, "last_time": None,
                 "last_side": None, "last_reversal_preview": None}


def _read_one(path, symbol, poll, current):
    """Return (validated recent samples, validated alert) or None on corruption."""
    try:
        with open(path, encoding="utf-8") as handle:
            data = json.load(handle)
        if not isinstance(data, dict) or data.get("schema") != 1:
            return None
        if data.get("symbol") != symbol or data.get("poll") != poll:
            return None
        alert = dict(DEFAULT_ALERT)
        gate = data.get("alert", {})
        if isinstance(gate, dict):
            day, count = gate.get("day"), gate.get("count")
            last, side = gate.get("last_time"), gate.get("last_side")
            if day is None or (isinstance(day, str) and len(day) == 10):
                alert["day"] = day
            if type(count) is int and 0 <= count <= 8:
                alert["count"] = count
            if (last is None or
                    type(last) in (int, float) and math.isfinite(last)
                    and 0 <= last <= current + 30):
                alert["last_time"] = last
            if side in (None, "롱", "숏"):
                alert["last_side"] = side
            preview = gate.get("last_reversal_preview")
            if (preview is None or
                    type(preview) in (int, float) and math.isfinite(preview)
                    and 0 <= preview <= current + 30):
                alert["last_reversal_preview"] = preview
        raw = data.get("samples", [])
        if not isinstance(raw, list):
            return None
        samples = []
        for item in raw[-360:]:
            if not isinstance(item, list) or len(item) != 2:
                return None
            ts, price = float(item[0]), float(item[1])
            if (not math.isfinite(ts) or not math.isfinite(price) or
                    price <= 0 or ts > current + 30 or ts < 0):
                return None
            samples.append((ts, price))
        if not samples or current - samples[-1][0] > max(poll * 2.5, 150):
            return [], alert
        start = len(samples) - 1
        while start and 20 <= samples[start][0] - samples[start - 1][0] <= max(poll * 2.5, 150):
            start -= 1
        return samples[start:], alert
    except FileNotFoundError:
        return None
    except (OSError, ValueError, TypeError, OverflowError, json.JSONDecodeError):
        return None


def load_snapshots(path, symbol, poll, now=None):
    """Validate primary and backup; prefer the freshest VALID continuous sample.

    If both caches are stale or corrupt, return no observations, never synthetic
    bars. Alert count/cooldown is preserved from whichever valid state survived.
    """
    current = time.time() if now is None else float(now)
    primary = _read_one(path, symbol, poll, current)
    backup = _read_one(path + ".bak", symbol, poll, current)
    valid = [("primary", primary), ("backup", backup)]
    candidates = [(name, data) for name, data in valid if data is not None and data[0]]
    if candidates:
        # If a deployment writes backup then crashes before replacing primary,
        # the valid copy with the latest observation is the one to resume from.
        name, chosen = max(candidates, key=lambda pair: pair[1][0][-1][0])
        if name == "backup":
            LOG.warning("Price sample cache recovered from validated backup; samples=%d",
                        len(chosen[0]))
        return chosen
    for name, data in valid:
        if data is not None:
            LOG.warning("Price sample cache has no fresh continuous samples; safe warm-up")
            return [], data[1]
    LOG.warning("Price sample cache invalid or absent in both replicas; safe warm-up")
    return [], dict(DEFAULT_ALERT)


def _atomic_json(path, payload):
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix="." + os.path.basename(path) + ".",
                                suffix=".tmp", dir=folder)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False)
            handle.flush()
            os.fsync(handle.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            os.unlink(tmp)


def save_snapshots(path, symbol, poll, samples, alert):
    """Write complete validated snapshots to backup and primary atomically.

    Unique temporary names avoid cross-deployment collisions in the same
    mounted Railway volume. Both copies contain the SAME observations and
    cooldown; no historical candles or missing bars are manufactured.
    """
    try:
        payload = {
            "schema": 1, "symbol": symbol, "poll": poll,
            "samples": [[ts, price] for ts, price in list(samples)[-360:]],
            "alert": alert,
        }
        _atomic_json(path + ".bak", payload)
        _atomic_json(path, payload)
    except (OSError, ValueError, TypeError):
        LOG.warning("Could not persist price sample cache replicas")
