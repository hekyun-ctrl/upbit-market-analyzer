"""LBank BTCUSDT perpetual PUBLIC order-book shadow research.

No API key, account reads, Telegram messages, trade decisions or orders.
A separate daemon thread cannot block the existing ticker/position monitor.
All thresholds are research hypotheses; forward outcomes are estimates,
NOT realized PnL, actual fills, signed trade flow, or exchange OHLCV.
"""
import datetime
import json
import logging
import math
import os
import threading
import time
from lbank_operational_health import StorageReserve
import urllib.parse
import urllib.request

LOG = logging.getLogger("lbank_orderbook_shadow")
HOST = "https://lbkperp.lbank.com"
PATH = "/cfd/openApi/v1/pub/marketOrder"
TAKER_FEE = 0.0006
HORIZONS = (300, 900, 1800)


def _levels(raw, reverse):
    if not isinstance(raw, list) or not raw:
        raise ValueError("Missing depth levels")
    result = []
    for obj in raw[:20]:
        if not isinstance(obj, dict):
            raise ValueError("Malformed depth level")
        price, volume = float(obj["price"]), float(obj["volume"])
        if not all(math.isfinite(v) and v > 0 for v in (price, volume)):
            raise ValueError("Invalid price or depth volume")
        result.append((price, volume))
    return sorted(result, reverse=reverse)[:5]


def extract_features(payload):
    """Validate two-sided actual PERPETUAL depth before recording a quote."""
    data = payload.get("data", payload) if isinstance(payload, dict) else None
    if not isinstance(data, dict):
        raise ValueError("Unsupported book payload")
    asks = _levels(data.get("asks"), False)
    bids = _levels(data.get("bids"), True)
    ask, bid = asks[0][0], bids[0][0]
    if ask <= bid:
        raise ValueError("Crossed/reversed order book")
    mid = (ask + bid) / 2
    spread_bps = (ask - bid) / mid * 10000
    if spread_bps > 100:
        raise ValueError("Implausibly wide order book")
    bid_qty = sum(q for _, q in bids)
    ask_qty = sum(q for _, q in asks)
    imbalance = (bid_qty - ask_qty) / (bid_qty + ask_qty)
    best_bid_qty, best_ask_qty = bids[0][1], asks[0][1]
    top_imbalance = (best_bid_qty - best_ask_qty) / (best_bid_qty + best_ask_qty)
    micro = (ask * best_bid_qty + bid * best_ask_qty) / (best_bid_qty + best_ask_qty)
    return {
        "symbol": "BTCUSDT", "source": "lbank_perpetual_public_depth",
        "bid": bid, "ask": ask, "mid": mid,
        "spread_bps": round(spread_bps, 5),
        "depth_imbalance5": round(imbalance, 6),
        "depth_imbalance1": round(top_imbalance, 6),
        "microprice_offset_bps": round((micro - mid) / mid * 10000, 6),
        "bid_depth5": round(bid_qty, 8),
        "ask_depth5": round(ask_qty, 8),
        "limits": "snapshot_depth_only_not_trade_flow_or_ohlcv",
    }


def shadow_direction(features):
    """Arbitrary PRE-REGISTERED exploratory gate; NEVER a live entry gate."""
    imb = features["depth_imbalance5"]
    offset = features["microprice_offset_bps"]
    if features["spread_bps"] > 4:
        return 0
    if imb >= 0.25 and offset > 0:
        return 1
    if imb <= -0.25 and offset < 0:
        return -1
    return 0


def estimated_close_net_bps(candidate, future, fee=TAKER_FEE):
    """Hypothetical taker both sides using observed best quotes, not fills."""
    direction = candidate["direction"]
    if direction == 1:
        entry, exit_price = candidate["ask"], future["bid"]
    elif direction == -1:
        entry, exit_price = candidate["bid"], future["ask"]
    else:
        raise ValueError("Invalid direction")
    price_pnl = direction * (exit_price / entry - 1)
    fees = fee * (1 + exit_price / entry)
    return round(10000 * (price_pnl - fees), 4)


class OrderBookShadow:
    """Append-only immutable observations and 5/15/30m future assessments."""

    def __init__(self, directory):
        self.directory = directory
        self.pending = []
        self.last_candidate_at = float("-inf")
        self.sample_count = 0
        self.candidate_count = 0
        self.outcome_count = 0

    def _write(self, row, epoch):
        os.makedirs(self.directory, exist_ok=True)
        day = datetime.datetime.fromtimestamp(
            epoch, tz=datetime.timezone.utc).strftime("%Y-%m-%d")
        path = os.path.join(self.directory, f"orderbook_{day}.jsonl")
        with open(path, "a", encoding="utf-8") as output:
            output.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")
        os.chmod(path, 0o600)

    def observe(self, epoch, payload):
        metrics = extract_features(payload)
        epoch = float(epoch)
        self.sample_count += 1
        direction = shadow_direction(metrics)
        candidate = None
        if direction and epoch - self.last_candidate_at >= 180:
            self.last_candidate_at = epoch
            self.candidate_count += 1
            candidate = {**metrics, "at": epoch, "direction": direction,
                         "horizons_done": []}
            self.pending.append(candidate)
        self._write({"kind": "snapshot", "at": epoch, **metrics,
                     "shadow_side": direction,
                     "shadow_candidate": candidate is not None}, epoch)
        outcomes = []
        still_pending = []
        for original in self.pending:
            pending_horizons = []
            for horizon in HORIZONS:
                if horizon in original["horizons_done"]:
                    continue
                if epoch - original["at"] >= horizon:
                    result = {
                        "kind": "outcome", "at": epoch,
                        "entry_at": original["at"],
                        "horizon_seconds": horizon,
                        "direction": original["direction"],
                        "entry_mid": original["mid"], "exit_mid": metrics["mid"],
                        "entry_imbalance5": original["depth_imbalance5"],
                        "entry_spread_bps": original["spread_bps"],
                        "gross_mid_bps": round(
                            (metrics["mid"] / original["mid"] - 1) *
                            original["direction"] * 10000, 4),
                        "net_bps_taker_quote_model": estimated_close_net_bps(
                            original, metrics),
                        "funding": "not_included",
                        "fills": "not_verified",
                        "source": "lbank_perpetual_public_depth",
                    }
                    self._write(result, epoch)
                    outcomes.append(result)
                    self.outcome_count += 1
                    original["horizons_done"].append(horizon)
                else:
                    pending_horizons.append(horizon)
            if pending_horizons:
                still_pending.append(original)
        self.pending = still_pending
        return candidate, outcomes


def fetch_public_orderbook(timeout=5, opener=None):
    query = urllib.parse.urlencode({"depth": 5, "symbol": "BTCUSDT"})
    url = HOST + PATH + "?" + query
    request = urllib.request.Request(
        url, headers={"User-Agent": "LBankPublicOrderBookShadow/1.0",
                      "Accept": "application/json"})
    open_url = urllib.request.urlopen if opener is None else opener
    with open_url(request, timeout=timeout) as response:
        raw = response.read(150000)
    result = json.loads(raw)
    # The public endpoint may return {data: {...}} or bare depth.
    extract_features(result)
    return result


class OrderBookShadowThread:
    def __init__(self, directory=None, interval_seconds=30, fetcher=None):
        self.directory = directory or os.getenv(
            "LBANK_ORDERBOOK_SHADOW_DIR", "/data/lbank_orderbook_shadow")
        self.interval = max(20, int(interval_seconds))
        self.fetcher = fetcher or fetch_public_orderbook
        self.study = OrderBookShadow(self.directory)
        self.error_count = 0
        self.last_error_log = 0.0
        self.last_summary_log = 0.0
        self.storage_reserve = StorageReserve(self.directory)

    def _loop(self):
        LOG.info("ORDERBOOK_SHADOW_START interval=%ds source=perpetual_public_depth "
                 "mode=research_only NO signals NO orders", self.interval)
        while True:
            start = time.monotonic()
            try:
                if not self.storage_reserve.check()["research_writable"]:
                    # Do not record an outcome across an unobserved storage gap.
                    self.study.pending.clear()
                    time.sleep(self.interval)
                    continue
                payload = self.fetcher()
                now = time.time()
                candidate, outcomes = self.study.observe(now, payload)
                if candidate:
                    LOG.info("ORDERBOOK_SHADOW_CANDIDATE side=%s imbalance5=%.4f "
                             "microprice_offset_bps=%.4f spread_bps=%.3f "
                             "label=UNVERIFIED_SHADOW",
                             candidate["direction"], candidate["depth_imbalance5"],
                             candidate["microprice_offset_bps"],
                             candidate["spread_bps"])
                for result in outcomes:
                    LOG.info("ORDERBOOK_SHADOW_OUTCOME %s",
                             json.dumps(result, sort_keys=True))
                if now - self.last_summary_log >= 900:
                    LOG.info("ORDERBOOK_SHADOW_SUMMARY samples=%d candidates=%d "
                             "outcomes=%d errors=%d, NOT PnL",
                             self.study.sample_count, self.study.candidate_count,
                             self.study.outcome_count, self.error_count)
                    self.last_summary_log = now
            except (OSError, ValueError, TypeError, KeyError,
                    json.JSONDecodeError) as exc:
                self.error_count += 1
                now = time.time()
                if now - self.last_error_log >= 300:
                    LOG.warning("ORDERBOOK_SHADOW_UNAVAILABLE count=%d reason=%s: %s "
                                "(existing market alerts unaffected)",
                                self.error_count, type(exc).__name__, str(exc)[:180])
                    self.last_error_log = now
            duration = time.monotonic() - start
            time.sleep(max(1.0, self.interval - duration))

    def start(self):
        thread = threading.Thread(target=self._loop, daemon=True,
                                  name="lbank-perpetual-orderbook-shadow")
        thread.start()
        return thread
