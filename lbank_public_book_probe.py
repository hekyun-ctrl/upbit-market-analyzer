"""Bounded public source discovery for a Railway pre-deploy run, no credentials."""
import asyncio
from html.parser import HTMLParser
import json
import logging
import re
from urllib.parse import urljoin, urlparse

import aiohttp


URL = "https://www.lbank.com/futures/btcusdt"
LOG = logging.getLogger("lbank_public_book_probe")
URL_RE = re.compile(r"(?:https?|wss)://[a-zA-Z0-9.-]+(?::\d+)?(?:/[a-zA-Z0-9_/.-]*)?")
METHOD_RE = re.compile(r"[\"']((?:/[a-zA-Z0-9._-]+){2,10})[\"']")
BOOK_WORDS = re.compile(r"order.?book|depth|marketOrder|handicap", re.I)


class ScriptPaths(HTMLParser):
    def __init__(self):
        super().__init__()
        self.urls = set()

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        path = (attrs.get("src") if tag == "script" else attrs.get("href")
                if tag == "link" and attrs.get("as") == "script" else None)
        if path:
            url = urljoin(URL, path)
            parsed = urlparse(url)
            if parsed.scheme == "https" and parsed.hostname and (
                    parsed.hostname == "lbank.com" or parsed.hostname.endswith(".lbank.com")):
                self.urls.add(url)


async def read(session, url, cap):
    async with session.get(url, allow_redirects=False) as response:
        if response.status != 200:
            LOG.info("VIDEO_BOOK_SOURCE_HTTP status=%d host=%s", response.status, urlparse(url).hostname)
            return ""
        out = bytearray()
        async for data in response.content.iter_chunked(32768):
            out.extend(data[:cap-len(out)])
            if len(out) >= cap:
                break
        return out.decode("utf-8", errors="replace")


async def scan(session, url):
    try:
        js = await read(session, url, 1_800_000)
        hosts = sorted(set(URL_RE.findall(js)))
        hosts = [h for h in hosts if re.search(r"(?:^wss://|rerrkvifj|lbkperp|marketOrder)", h, re.I)]
        paths = sorted({m for m in METHOD_RE.findall(js) if BOOK_WORDS.search(m)})
        for marker in ("activateOrderBook", "subscribeMarketOrder", "subscribeMarketDepth", "subscribeOrderBook", "38040:", "57292:", "32941:", "17148:"):
            for pos in [m.start() for m in re.finditer(re.escape(marker), js)][:3]:
                sample = js[max(0, pos-360):pos+1300]
                sample = re.sub(r"[A-Za-z0-9_-]{45,}", "<long>", sample)
                LOG.info("VIDEO_BOOK_PROTOCOL_HINT asset=%s marker=%s context=%r",
                         urlparse(url).path, marker, sample)
                if marker == "subscribeDepth":
                    earlier = js[max(0, pos-7200):pos]
                    if "[k]=y()" in earlier:
                        end = earlier.rfind("return t.current=l,i.current=o")
                        if end != -1:
                            context = earlier[max(0, end-3800):end+120]
                            for i in range(0, len(context), 1000):
                                LOG.info("VIDEO_BOOK_PROTOCOL_HOOK block=%d %r", i, re.sub(r"[A-Za-z0-9_-]{45,}", "<long>", context[i:i+1000]))
                    matches = list(re.finditer(r"(?:\bk\s*=|\[k,|\[k\]|\.A\.subscribe\w+|\bk:)", earlier))[-9:]
                    for m in matches:
                        sample = earlier[max(0, m.start()-160):m.start()+430]
                        LOG.info("VIDEO_BOOK_PROTOCOL_BINDING %r", re.sub(r"[A-Za-z0-9_-]{45,}", "<long>", sample))
                if marker in ("32941:", "17148:", "38040:", "57292:"):
                    module = re.split(r"\},\d+:\(\w+,\w+,\w+\)=>", js[pos:pos+30000], maxsplit=1)[0]
                    for m in list(re.finditer(r"(?:[Dd]epth|[Oo]rder[Bb]ook|[Mm]arket[Oo]rder|/ws/v3|[Bb]idList|[Aa]skList)", module))[:16]:
                        sample = module[max(0, m.start()-100):m.start()+230]
                        LOG.info("VIDEO_BOOK_PROTOCOL_WS module=%s %r", marker, re.sub(r"[A-Za-z0-9_-]{45,}", "<long>", sample))
        symbols = sorted({m.group(0) for m in re.finditer(
            r"[A-Za-z_$][A-Za-z0-9_$]{2,40}", js)
            if BOOK_WORDS.search(m.group(0))})
        return hosts[:16], paths[:16], symbols[:14] if hosts or paths else []
    except (OSError, asyncio.TimeoutError, aiohttp.ClientError, ValueError):
        return [], [], []


async def check_frontend_depth(session):
    """Check a public read path discovered in the exchange's own web app."""
    url = "https://lbkperp.lbank.com/cfd/openApi/v1/pub/instrument?productGroup=SwapU"
    try:
        data = json.loads(await read(session, url, 650000)).get("data", [])
        target = next(r for r in data if r.get("symbol", "").upper() == "BTCUSDT")
        exchange = target.get("exchangeID", "")
        if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,30}", exchange):
            return
        LOG.info("VIDEO_BOOK_PROTOCOL_INSTRUMENT exchange=%s", exchange)
        url = "https://uuapi.rerrkvifj.com/cfd/market/v1.0/SendQryMarketOrder"
        for params in ({"instrumentID": "BTCUSDT", "exchangeID": exchange, "isUsd": "1"},
                       {"symbol": "BTCUSDT", "depth": "20"}):
            async with session.get(url, params=params, allow_redirects=False) as resp:
                if resp.status != 200:
                    LOG.info("VIDEO_BOOK_PROTOCOL_TRIAL status=%s param_fields=%s", resp.status, sorted(params))
                    continue
                body = json.loads(await resp.content.read(96000))
                root = body if isinstance(body, dict) else {}
                inner = root.get("data", {})
                inner = inner if isinstance(inner, dict) else {}
                codes = {k: root[k] for k in ("code", "error_code", "success") if type(root.get(k)) in (int, bool)}
                LOG.info("VIDEO_BOOK_PROTOCOL_TRIAL status=200 codes=%s keys=%s data_keys=%s",
                         codes, sorted(root)[:30], sorted(inner)[:45])
    except (OSError, asyncio.TimeoutError, aiohttp.ClientError, StopIteration, ValueError, AttributeError, TypeError) as exc:
        LOG.info("VIDEO_BOOK_PROTOCOL_TRIAL unavailable=%s", type(exc).__name__)


async def run():
    timeout = aiohttp.ClientTimeout(total=9)
    async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(limit=10),
                                    headers={"User-Agent": "Mozilla/5.0"}) as session:
        html = await read(session, URL, 1_600_000)
        parser = ScriptPaths()
        parser.feed(html)
        paths = sorted(parser.urls, key=lambda u: (not bool(re.search(r"futures|_app|perp", u)), u))[:80]
        LOG.info("VIDEO_BOOK_SOURCE_ASSETS count=%d total=%d", len(paths), len(parser.urls))
        results = await asyncio.gather(*(scan(session, u) for u in paths))
        for url, (hosts, routes, symbols) in zip(paths, results):
            if hosts or routes:
                LOG.info("VIDEO_BOOK_SOURCE_CLUES asset=%s sockets=%s depth_paths=%s symbols=%s",
                         urlparse(url).path, hosts, routes, symbols)


def probe_once():
    try:
        asyncio.run(asyncio.wait_for(run(), timeout=16))
    except (OSError, asyncio.TimeoutError, aiohttp.ClientError, ValueError) as exc:
        LOG.info("VIDEO_BOOK_SOURCE_UNVERIFIED reason=%s", type(exc).__name__)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    probe_once()
