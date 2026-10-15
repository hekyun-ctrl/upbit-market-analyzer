"""Official US macro calendar + release headlines for LBank Telegram.

Information-only notifications; never changes classify/confirm/watch/orders.
Official BLS, BEA, Fed feeds. No scraped forecasts and no inferred actual/consensus.
Network I/O runs in a dedicated daemon thread, never in the market-risk loop.
"""
import hashlib
import html
import json
import logging
import os
import re
import threading
import time
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from zoneinfo import ZoneInfo

LOG = logging.getLogger("lbank_macro")
UTC = timezone.utc
KST = ZoneInfo("Asia/Seoul")
EASTERN = ZoneInfo("America/New_York")
FEEDS = {
    "BLS CPI": "https://www.bls.gov/feed/cpi.rss",
    "BLS PPI": "https://www.bls.gov/feed/ppi.rss",
    "BLS 고용": "https://www.bls.gov/feed/empsit.rss",
    "연준 통화정책": "https://www.federalreserve.gov/feeds/press_monetary.xml",
    "연준 연설": "https://www.federalreserve.gov/feeds/speeches.xml",
}
BLS_ICS = "https://www.bls.gov/schedule/news_release/bls.ics"
BEA_JSON = "https://apps.bea.gov/API/signup/release_dates.json"
CALENDAR_CHECK_SECONDS = 4 * 3600
HEADLINE_CHECK_SECONDS = 5 * 60
LOOKBACK_SECONDS = 2 * 3600
MAX_SEEN = 600
MAX_BYTES = 1_000_000
KEYWORDS = (
    "consumer price index", "producer price index", "employment situation",
    "job openings and labor turnover", "employment cost index",
)
ALLOWED_HOSTS = {"www.bls.gov", "bls.gov", "www.federalreserve.gov",
                 "federalreserve.gov", "www.bea.gov", "bea.gov", "apps.bea.gov"}


def fetch_official(url):
    parsed = urllib.parse.urlparse(url)
    if parsed.scheme != "https" or parsed.hostname not in ALLOWED_HOSTS:
        raise ValueError("refuse non-official macro data source")
    req = urllib.request.Request(url, headers={
        "User-Agent": "Mozilla/5.0 (compatible; MacroEventMonitor/1.0)",
        "Accept": "application/rss+xml,application/xml,text/xml,text/calendar,application/json,*/*",
    })
    with urllib.request.urlopen(req, timeout=7) as response:
        if response.geturl().startswith("http://"):
            raise ValueError("refuse insecure redirect")
        return response.read(MAX_BYTES + 1)[:MAX_BYTES]  # bounded


def _text(node, name):
    value = node.findtext(name)
    return value.strip() if value else ""


def parse_rss(source, payload):
    """Rely on official feed timestamps, never on the time we first saw an item."""
    root = ET.fromstring(payload)
    items = []
    if root.tag.lower().endswith("rss") or root.find("channel") is not None:
        nodes = root.findall(".//channel/item")
        for x in nodes:
            title, link, raw_date = _text(x, "title"), _text(x, "link"), _text(x, "pubDate")
            try:
                dt = parsedate_to_datetime(raw_date)
                if dt.tzinfo is None:
                    continue
                timestamp = dt.timestamp()
            except (TypeError, ValueError, OverflowError):
                continue
            items.append({"source": source, "title": title, "url": link,
                          "published": timestamp})
    else:
        ns = "{http://www.w3.org/2005/Atom}"
        for x in root.findall(ns + "entry"):
            link = ""
            for l in x.findall(ns + "link"):
                if l.get("rel", "alternate") == "alternate":
                    link = l.get("href", "")
                    break
            raw_date = _text(x, ns + "published") or _text(x, ns + "updated")
            try:
                timestamp = datetime.fromisoformat(raw_date.replace("Z", "+00:00")).timestamp()
            except (TypeError, ValueError, OverflowError):
                continue
            items.append({"source": source, "title": _text(x, ns + "title"),
                          "url": link, "published": timestamp})
    valid = []
    for x in items:
        link = urllib.parse.urlparse(x["url"])
        if not x["title"] or link.scheme != "https" or link.hostname not in ALLOWED_HOSTS:
            continue
        x["title"] = html.unescape(re.sub(r"<[^>]+>", "", x["title"]))[:150]
        x["uid"] = hashlib.sha256(
            (source + "|" + x["url"] + "|" + str(int(x["published"]))).encode()
        ).hexdigest()[:24]
        valid.append(x)
    return valid


def _unfold_ical(payload):
    text = payload.decode("utf-8-sig")
    return re.sub(r"\r?\n[ \t]", "", text).replace("\r", "").split("\n")


def parse_bls_ical(payload):
    """Only explicit dated BLS high-impact VEVENT entries; correct DST via zoneinfo."""
    lines = _unfold_ical(payload)
    raw_events = []
    event = None
    for line in lines:
        if line == "BEGIN:VEVENT":
            event = {}
        elif line == "END:VEVENT":
            if event:
                raw_events.append(event)
            event = None
        elif event is not None and ":" in line:
            key, value = line.split(":", 1)
            event[key] = value.replace(r"\,", ",").replace(r"\n", " ")
    result = []
    for item in raw_events:
        title = item.get("SUMMARY", "").strip()
        if not any(keyword in title.casefold() for keyword in KEYWORDS):
            continue
        key = next((k for k in item if k.startswith("DTSTART")), None)
        if key is None:
            continue
        val = item[key]
        try:
            if not ("T" in val and len(val) >= 15):
                continue
            if val.endswith("Z"):
                stamp = datetime.strptime(val, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
            else:
                stamp = datetime.strptime(val[:15], "%Y%m%dT%H%M%S").replace(tzinfo=EASTERN)
            result.append({"title": title, "at": stamp.timestamp(),
                           "source": "BLS", "url": BLS_ICS})
        except (ValueError, TypeError):
            continue
    return result


def parse_bea_json(payload):
    raw = json.loads(payload)
    result = []
    for title in ("Personal Income and Outlays", "Gross Domestic Product"):
        for value in raw.get(title, {}).get("release_dates", []):
            try:
                dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
                if dt.tzinfo is None:
                    continue
                result.append({"title": title + (" (PCE 포함)" if "Income" in title else ""),
                               "at": dt.timestamp(), "source": "BEA",
                               "url": "https://www.bea.gov/news/schedule"})
            except (TypeError, ValueError):
                continue
    return result



# Official BLS schedule cached from published 2026 release pages on 2026-10-15.
# Last resort when BLS blocks cloud servers; NOT an automatically updated calendar.
# See https://www.bls.gov/schedule/2026/home.htm
BLS_SCHEDULE_CACHE = (
    ("2026-10-15", "08:30", "Producer Price Index for September 2026"),
    ("2026-10-30", "08:30", "Employment Cost Index for Q3 2026"),
    ("2026-11-03", "10:00", "Job Openings and Labor Turnover for September 2026"),
    ("2026-11-06", "08:30", "Employment Situation for October 2026"),
    ("2026-11-10", "08:30", "Consumer Price Index for October 2026"),
    ("2026-11-13", "08:30", "Producer Price Index for October 2026"),
    ("2026-12-01", "10:00", "Job Openings and Labor Turnover for October 2026"),
    ("2026-12-04", "08:30", "Employment Situation for November 2026"),
    ("2026-12-10", "08:30", "Consumer Price Index for November 2026"),
    ("2026-12-15", "08:30", "Producer Price Index for November 2026"),
)


def bls_cached_schedule(now):
    result = []
    for date, clock, name in BLS_SCHEDULE_CACHE:
        dt = datetime.strptime(date + " " + clock, "%Y-%m-%d %H:%M").replace(tzinfo=EASTERN)
        ts = dt.timestamp()
        if -4 * 3600 <= ts - now <= 100 * 86400:
            result.append({
                "title": name, "at": ts, "source": "BLS",
                "url": "https://www.bls.gov/schedule/2026/home.htm",
                "cached": True,
            })
    return result


def event_key(event):
    return hashlib.sha256(
        (event["source"] + "|" + event["title"] + "|" +
         str(int(event["at"]))).encode()).hexdigest()[:24]


def format_time(timestamp):
    return datetime.fromtimestamp(timestamp, tz=KST).strftime("%Y-%m-%d %H:%M KST")


def release_message(event):
    return (
        "[LBank BTCUSDT | 미국 매크로 발표 예정 · 진입 신호 아님]\n"
        f"지표: {event['title']}\n"
        f"한국시간: {format_time(event['at'])}\n"
        f"공식 일정: {event['source']}" + (" (사전 확인한 일정표·변경 여부 재확인 필요)\n" if event.get("cached") else "\n")
        "참고: 발표 전후 가격 급변, 체결 지연·슬리피지 위험을 확인하세요.\n"
        "매매 방향·컨센서스·실제 발표치는 아직 미확인입니다.\n"
        f"원문: {event['url']}"
    )


def news_message(item):
    return (
        "[LBank BTCUSDT | 미국 공식 매크로 발표 · 진입 신호 아님]\n"
        f"출처: {item['source']}\n"
        f"제목: {item['title']}\n"
        f"공개시각: {format_time(item['published'])}\n"
        "중요: 제목만으로 예상치 대비 상회/하회·BTC 방향을 판정하지 않습니다.\n"
        f"원문: {item['url']}"
    )


class MacroMonitor:
    def __init__(self, sender, state_path="/data/lbank_macro_state.json",
                 fetcher=fetch_official):
        self.sender = sender
        self.fetcher = fetcher
        self.state_path = state_path
        self.seen = self._load()
        self.calendar = []
        self.last_calendar = 0
        self.last_news = 0
        self.feeds_ok = set()

    def _load(self):
        try:
            with open(self.state_path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("version") != 1 or not isinstance(data.get("seen"), dict):
                return {}
            return {str(k): float(v) for k, v in data["seen"].items()}
        except (FileNotFoundError, OSError, TypeError, ValueError, OverflowError):
            return {}

    def _save(self):
        try:
            directory = os.path.dirname(self.state_path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp = self.state_path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"version": 1, "seen": self.seen}, f)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, self.state_path)
        except (OSError, TypeError, ValueError):
            LOG.warning("MACRO state persist failed")

    def _sent(self, key, message, now):
        if key in self.seen:
            return False
        if not self.sender(message):
            LOG.warning("MACRO Telegram delivery failed; will retry %s", key)
            return False
        self.seen[key] = now
        self.seen = dict(sorted(self.seen.items(), key=lambda v: v[1])[-MAX_SEEN:])
        self._save()
        LOG.info("MACRO ALERT SENT key=%s", key)
        return True

    def refresh_calendar(self, now):
        entries = []
        for name, url, parser in (
            ("BLS-calendar", BLS_ICS, parse_bls_ical),
            ("BEA-calendar", BEA_JSON, parse_bea_json),
        ):
            try:
                found = parser(self.fetcher(url))
                entries.extend(found)
                self.feeds_ok.add(name)
                LOG.info("MACRO SOURCE OK name=%s events=%d", name, len(found))
            except Exception as exc:
                LOG.warning("MACRO SOURCE FAILED name=%s exception=%s", name, type(exc).__name__)
        if "BLS-calendar" not in self.feeds_ok:
            cached = bls_cached_schedule(now)
            entries.extend(cached)
            LOG.warning("MACRO BLS calendar inaccessible: using verified 2026 cached schedule events=%d; schedule could change", len(cached))
        if entries:
            unique = {event_key(x): x for x in entries}
            self.calendar = list(unique.values())
        self.last_calendar = now

    def refresh_news(self, now):
        for name, url in FEEDS.items():
            try:
                items = parse_rss(name, self.fetcher(url))
                self.feeds_ok.add(name)
                LOG.info("MACRO SOURCE OK name=%s entries=%d", name, len(items))
            except Exception as exc:
                LOG.warning("MACRO SOURCE FAILED name=%s exception=%s", name, type(exc).__name__)
                continue
            for item in sorted(items, key=lambda v: v["published"]):
                age = now - item["published"]
                if not (0 <= age <= LOOKBACK_SECONDS):
                    continue
                self._sent("N" + item["uid"], news_message(item), now)
        self.last_news = now

    def check_calendar(self, now):
        for event in sorted(self.calendar, key=lambda v: v["at"]):
            until = event["at"] - now
            # One advance notice per event, ~30m before (5-minute polling).
            if 5 * 60 <= until <= 35 * 60:
                self._sent("C" + event_key(event), release_message(event), now)

    def once(self, now=None):
        now = time.time() if now is None else float(now)
        if not self.last_calendar or now - self.last_calendar >= CALENDAR_CHECK_SECONDS:
            self.refresh_calendar(now)
        self.check_calendar(now)
        if not self.last_news or now - self.last_news >= HEADLINE_CHECK_SECONDS:
            self.refresh_news(now)

    def run(self):
        LOG.info("MACRO MONITOR started official BLS/Fed RSS and BLS/BEA calendar; information-only")
        while True:
            try:
                self.once()
            except Exception as exc:
                LOG.warning("MACRO monitor cycle error exception=%s", type(exc).__name__)
            time.sleep(HEADLINE_CHECK_SECONDS)

    def start(self):
        thread = threading.Thread(target=self.run, name="lbank-macro-news", daemon=True)
        thread.start()
        return thread
