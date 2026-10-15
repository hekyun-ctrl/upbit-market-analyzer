"""Atomic bounded cache of LBank *perpetual ticker* price observations.

This is NOT exchange OHLCV: no actual trades/volumes can be inferred.
Also preserves the daily alert cap and cooldown across short redeploys.
A corrupt/stale cache is ignored so it can never create an entry signal.
"""
import json
import logging
import math
import os
import time

LOG = logging.getLogger("lbank_sample_store")
DEFAULT_ALERT = {"day": None, "count": 0, "last_time": None, "last_side": None, "last_reversal_preview": None}


def load_snapshots(path, symbol, poll, now=None):
    """Return validated (timestamp, last_price) pairs and a validated alert gate."""
    current = time.time() if now is None else float(now)
    alert = dict(DEFAULT_ALERT)
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or data.get("schema") != 1:
            return [], alert
        if data.get("symbol") != symbol or data.get("poll") != poll:
            return [], alert

        gate = data.get("alert", {})
        if isinstance(gate, dict):
            day = gate.get("day")
            count = gate.get("count")
            last = gate.get("last_time")
            side = gate.get("last_side")
            if day is None or (isinstance(day, str) and len(day) == 10):
                alert["day"] = day
            if type(count) is int and 0 <= count <= 8:
                alert["count"] = count
            if last is None or (type(last) in (int, float) and
                                math.isfinite(last) and 0 <= last <= current + 30):
                alert["last_time"] = last
            if side in (None, "롱", "숏"):
                alert["last_side"] = side
            preview = gate.get("last_reversal_preview")
            if preview is None or (type(preview) in (int, float) and
                                   math.isfinite(preview) and
                                   0 <= preview <= current + 30):
                alert["last_reversal_preview"] = preview

        raw = data.get("samples", [])
        if not isinstance(raw, list):
            return [], alert
        samples = []
        for row in raw[-360:]:
            if not isinstance(row, list) or len(row) != 2:
                return [], alert
            ts, price = float(row[0]), float(row[1])
            if (not math.isfinite(ts) or not math.isfinite(price) or
                price <= 0 or ts > current + 30 or ts < 0):
                return [], alert
            samples.append((ts, price))
        if not samples or current - samples[-1][0] > max(poll * 2.5, 150):
            return [], alert
        # Recover only the latest continuous run; a long outage must warm up.
        start = len(samples) - 1
        while start and 20 <= samples[start][0] - samples[start-1][0] <= max(poll * 2.5, 150):
            start -= 1
        return samples[start:], alert
    except FileNotFoundError:
        return [], alert
    except (OSError, ValueError, TypeError, OverflowError, json.JSONDecodeError):
        LOG.warning("Price sample cache invalid; starting fresh")
        return [], alert


def save_snapshots(path, symbol, poll, samples, alert):
    """Best-effort atomic store: never interrupts trading-monitor read cycle."""
    try:
        directory = os.path.dirname(path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        payload = {
            "schema": 1, "symbol": symbol, "poll": poll,
            "samples": [[ts, price] for ts, price in list(samples)[-360:]],
            "alert": alert,
        }
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except (OSError, ValueError, TypeError):
        LOG.warning("Could not persist price sample cache")
