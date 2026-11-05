"""Bounded public source discovery for a Railway pre-deploy run, no credentials."""
import asyncio
from html.parser import HTMLParser
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
        return hosts[:16], paths[:16]
    except (OSError, asyncio.TimeoutError, aiohttp.ClientError, ValueError):
        return [], []


async def run():
    timeout = aiohttp.ClientTimeout(total=3)
    async with aiohttp.ClientSession(timeout=timeout, connector=aiohttp.TCPConnector(limit=6),
                                    headers={"User-Agent": "Mozilla/5.0"}) as session:
        html = await read(session, URL, 1_600_000)
        parser = ScriptPaths()
        parser.feed(html)
        paths = sorted(parser.urls, key=lambda u: (not bool(re.search(r"futures|_app|perp", u)), u))[:18]
        LOG.info("VIDEO_BOOK_SOURCE_ASSETS count=%d", len(paths))
        for url, (hosts, routes) in zip(paths, await asyncio.gather(*(scan(session, u) for u in paths))):
            if hosts or routes:
                LOG.info("VIDEO_BOOK_SOURCE_CLUES asset=%s sockets=%s depth_paths=%s",
                         urlparse(url).path, hosts, routes)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    try:
        asyncio.run(asyncio.wait_for(run(), timeout=13))
    except (OSError, asyncio.TimeoutError, aiohttp.ClientError, ValueError) as exc:
        LOG.info("VIDEO_BOOK_SOURCE_UNVERIFIED reason=%s", type(exc).__name__)
