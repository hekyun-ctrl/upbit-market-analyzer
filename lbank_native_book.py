"""On-demand public BTCUSDT perpetual snapshots from LBank's futures stream."""
import asyncio
import json
import math
import time

import aiohttp

from lbank_native_candles import WS_URL, decode_frame


TOPIC = 3
REQUEST_ID = "3000000001"
INSTRUMENT = "BTCUSDT_0.1_25"
EXT = {"bvc": "202", "isUsd": 1}
MAX_LEVELS = 25
MAX_AGE = 2.0


class NativeBookError(ValueError):
    """Fixed diagnostic code with no payload or network credentials."""


def parse_snapshot(frame, now):
    """Accept the correlated subscription response, never a push or cached book."""
    if not isinstance(frame, dict) or (frame.get("x"), frame.get("z"), frame.get("y")) != (TOPIC, 3, REQUEST_ID):
        raise NativeBookError("unmatched_snapshot")
    # The server echoes the request ID rather than the instrument. The socket
    # sends exactly one subscription, for the USDT perpetual named above.
    try:
        ext = json.loads(frame["e"])
        if ext != EXT or type(ext["isUsd"]) is not int:
            raise NativeBookError("wrong_market")
    except (KeyError, TypeError, json.JSONDecodeError):
        raise NativeBookError("wrong_market") from None
    stamp = frame.get("w")
    if type(stamp) is not int or not -0.25 <= now - stamp / 1000 <= MAX_AGE:
        raise NativeBookError("stale_snapshot")

    def prices(key):
        raw = frame.get(key)
        if not isinstance(raw, list) or not 1 <= len(raw) <= MAX_LEVELS:
            raise NativeBookError("missing_levels")
        result = []
        for level in raw:
            if (not isinstance(level, list) or len(level) != 2
                    or not all(type(x) in (str, int, float) for x in level)):
                raise NativeBookError("invalid_level")
            try:
                price, amount = map(float, level)
            except (ValueError, OverflowError):
                raise NativeBookError("invalid_level") from None
            if not (math.isfinite(price) and price > 0 and math.isfinite(amount) and amount >= 0):
                raise NativeBookError("invalid_level")
            if amount > 0:
                result.append(price)
        if not result:
            raise NativeBookError("missing_levels")
        return result

    bid, ask = max(prices("b")), min(prices("s"))
    if not bid < ask:
        raise NativeBookError("locked_or_crossed_book")
    return bid, ask


async def _read_snapshot():
    async with aiohttp.ClientSession() as session:
        async with session.ws_connect(WS_URL, max_msg_size=1024*1024,
                                      timeout=aiohttp.ClientWSTimeout(ws_close=.15)) as ws:
            await ws.send_json({"x": TOPIC, "z": 1, "y": REQUEST_ID,
                                "a": {"i": INSTRUMENT}, "e": json.dumps(EXT)})
            async for msg in ws:
                if msg.type not in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                    continue
                frame = decode_frame(msg.data)
                if isinstance(frame, dict) and (frame.get("x"), frame.get("z"), frame.get("y")) == (TOPIC, 3, REQUEST_ID):
                    return parse_snapshot(frame, time.time())
    raise NativeBookError("snapshot_not_received")


def fetch_native_book(timeout):
    """Use a new single-market subscription; the caller owns the overall budget."""
    return asyncio.run(asyncio.wait_for(_read_snapshot(), timeout=timeout))
