"""Temporary live verification of the exchange frontend's public book topic."""
import asyncio
import json
import logging

import aiohttp

from lbank_native_candles import WS_URL, decode_frame

LOG = logging.getLogger(__name__)
ASSET = "https://www.lbank.com/static-client/lbank-front-trade/_next/static/chunks/7996-e25823ae3bcd3fae.js"


async def asset_map(session):
    try:
        async with session.get(ASSET, allow_redirects=False) as res:
            data = await res.text() if res.status == 200 else ""
        start = data.find('[r.KK.OrderBook]:{')
        if start != -1:
            LOG.info("VIDEO_BOOK_TRIAL_SCHEMA %r", data[start:start+1600])
    except (aiohttp.ClientError, ValueError, TimeoutError):
        pass


async def probe():
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=4)) as session:
        schema = asyncio.create_task(asset_map(session))
        try:
            async with session.ws_connect(WS_URL, max_msg_size=1024*1024) as ws:
                await ws.send_json({"x": 3, "y": "3000000001", "z": 1,
                                    "a": {"i": "BTCUSDT_0.1_25"},
                                    "e": json.dumps({"bvc": "202", "isUsd": 1})})
                LOG.info("VIDEO_BOOK_TRIAL_SOCKET connected=true")
                seen = 0
                async for msg in ws:
                    if msg.type not in (aiohttp.WSMsgType.TEXT, aiohttp.WSMsgType.BINARY):
                        continue
                    frame = decode_frame(msg.data)
                    if not isinstance(frame, dict):
                        continue
                    if frame.get("x") != 3:
                        if seen == 0:
                            LOG.info("VIDEO_BOOK_TRIAL_OTHER type=%s topic=%s keys=%s", frame.get("z"), frame.get("x"), sorted(frame))
                        continue
                    LOG.info("VIDEO_BOOK_TRIAL_FRAME %s", json.dumps(frame, ensure_ascii=True)[:2300])
                    seen += 1
                    if seen >= 4:
                        break
        finally:
            await schema


def probe_once():
    try:
        asyncio.run(asyncio.wait_for(probe(), 9))
    except (aiohttp.ClientError, OSError, ValueError, TimeoutError) as exc:
        LOG.info("VIDEO_BOOK_TRIAL_UNAVAILABLE reason=%s", type(exc).__name__)
