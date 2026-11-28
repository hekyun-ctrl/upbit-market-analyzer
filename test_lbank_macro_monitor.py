"""Offline regression tests for information-only official macro Telegram monitor."""
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from lbank_macro_monitor import (
    MacroMonitor, parse_rss, parse_bls_ical, parse_bea_json,
    event_key, release_message, news_message, fetch_official,
    bls_cached_schedule,
)


class MacroMonitorTests(unittest.TestCase):
    def test_bls_ical_tz_and_cpi_ppi_nonimportant_filtered(self):
        src = b"""BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Consumer Price Index for September 2026
DTSTART;TZID=America/New_York:20261014T083000
END:VEVENT
BEGIN:VEVENT
SUMMARY:Producer Price Index for September 2026
DTSTART;TZID=America/New_York:20261015T083000
END:VEVENT
BEGIN:VEVENT
SUMMARY:Real Earnings
DTSTART;TZID=America/New_York:20261014T083000
END:VEVENT
END:VCALENDAR"""
        found = parse_bls_ical(src)
        self.assertEqual(len(found),2)
        self.assertEqual(datetime.fromtimestamp(found[0]["at"],timezone.utc).hour,12)
        self.assertIn("21:30", release_message(found[0]))  # 12:30 UTC KST

    def test_ical_folded_line_and_dst(self):
        src = b"""BEGIN:VCALENDAR
BEGIN:VEVENT
SUMMARY:Employment Situation for October 
 2026
DTSTART:20261106T083000
END:VEVENT
END:VCALENDAR"""
        found = parse_bls_ical(src)
        self.assertEqual(len(found),1)
        self.assertEqual(datetime.fromtimestamp(found[0]["at"],timezone.utc).hour,13)
        self.assertIn("고용", "고용")

    def test_bea_calendar_json_utc_and_pce(self):
        body=json.dumps({"Personal Income and Outlays":{"release_dates":["2026-10-29T12:30:00+00:00"]},
                         "Gross Domestic Product":{"release_dates":["2026-10-29T12:30:00+00:00"]},
                         "Other":{"release_dates":["2026-10-29T12:30:00+00:00"]}}).encode()
        events=parse_bea_json(body)
        self.assertEqual(len(events),2)
        self.assertIn("PCE", events[0]["title"])
        self.assertIn("21:30",release_message(events[0]))

    def test_rss_official_deduplicated_and_dated(self):
        xml=b"""<rss version="2.0"><channel><title>BLS</title><item>
<title>CPI rose 0.2%</title><link>https://www.bls.gov/news.release/cpi.htm</link>
<pubDate>Thu, 15 Oct 2026 12:30:00 GMT</pubDate>
</item></channel></rss>"""
        items=parse_rss("BLS CPI",xml)
        self.assertEqual(len(items),1)
        self.assertIn("발표",news_message(items[0]))
        self.assertEqual(items[0]["uid"],parse_rss("BLS CPI",xml)[0]["uid"])

    def test_atom_feed_with_insecure_link_dropped(self):
        xml=b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><title>FOMC action</title>
<updated>2026-10-15T12:30:00Z</updated>
<link href="https://www.federalreserve.gov/newsevents/pressreleases/monetary20261015a.htm" />
</entry><entry><title>Fake</title><updated>2026-10-15T12:30:00Z</updated>
<link href="https://evil.example.test/fomc" /></entry></feed>"""
        self.assertEqual(len(parse_rss("연준 통화정책",xml)),1)

    def test_official_host_only(self):
        with self.assertRaises(ValueError):
            fetch_official("http://www.bls.gov/feed/cpi.rss")
        with self.assertRaises(ValueError):
            fetch_official("https://evil.example/feed.xml")

    def test_alert_not_before_window_and_no_duplicate_after_restart(self):
        with tempfile.TemporaryDirectory() as t:
            at=datetime(2026,10,16,12,30,tzinfo=timezone.utc).timestamp()
            event={"source":"BLS","title":"Producer Price Index for September 2026",
                   "at":at,"url":"https://www.bls.gov/schedule/news_release/bls.ics"}
            messages=[]
            path=os.path.join(t,"state.json")
            mm=MacroMonitor(lambda msg: messages.append(msg) or True,path,fetcher=lambda u:b"")
            mm.calendar=[event]
            mm.check_calendar(at-60*60)
            self.assertEqual(len(messages),0)
            mm.check_calendar(at-30*60)
            mm.check_calendar(at-10*60)
            self.assertEqual(len(messages),1)
            other=MacroMonitor(lambda msg: messages.append(msg) or True,path,fetcher=lambda u:b"")
            other.calendar=[event]
            other.check_calendar(at-10*60)
            self.assertEqual(len(messages),1)

    def test_failed_send_retries_and_unrelated_old_headlines_not_blasted(self):
        with tempfile.TemporaryDirectory() as t:
            now=datetime(2026,10,15,12,30,tzinfo=timezone.utc).timestamp()
            body=b"""<rss><channel><item><title>Old CPI</title>
<link>https://www.bls.gov/news.release/cpi.htm</link>
<pubDate>Wed, 14 Oct 2026 12:30:00 GMT</pubDate></item></channel></rss>"""
            calls=[]
            mm=MacroMonitor(lambda msg: calls.append(msg) or False,
                            os.path.join(t,"state.json"),fetcher=lambda u:body)
            mm.refresh_news(now)
            self.assertEqual(len(calls),0)
            body=b"""<rss><channel><item><title>Fresh CPI</title>
<link>https://www.bls.gov/news.release/cpi.htm</link>
<pubDate>Thu, 15 Oct 2026 12:30:00 GMT</pubDate></item></channel></rss>"""
            mm.refresh_news(now)
            self.assertTrue(calls)
            self.assertFalse(mm.seen)
            mm.sender=lambda msg: True
            mm.refresh_news(now)
            self.assertTrue(mm.seen)


    def test_cached_bls_schedule_when_official_server_blocks_cloud(self):
        with tempfile.TemporaryDirectory() as t:
            now=datetime(2026,10,15,12,0,tzinfo=timezone.utc).timestamp()
            self.assertEqual(len(bls_cached_schedule(now)),10)
            events=bls_cached_schedule(now)
            ppi=next(e for e in events if "Producer Price" in e["title"])
            self.assertIn("21:30",release_message(ppi))
            self.assertIn("재확인 필요",release_message(ppi))
            calls=[]
            def fetch(url):
                if url.endswith("bls.ics"):
                    raise OSError("HTTP 403")
                return json.dumps({}).encode()
            mm=MacroMonitor(lambda msg: calls.append(msg) or True,
                            os.path.join(t,"state.json"),fetcher=fetch)
            mm.refresh_calendar(now)
            self.assertIn("BLS", {x["source"] for x in mm.calendar})
            mm.check_calendar(now)
            self.assertEqual(len(calls),1)
            mm.check_calendar(now)
            self.assertEqual(len(calls),1)


    def test_feed_failure_does_not_stop_other_sources(self):
        with tempfile.TemporaryDirectory() as t:
            sample=b"""<rss><channel><item><title>Policy decision</title>
<link>https://www.federalreserve.gov/newsevents.htm</link>
<pubDate>Thu, 15 Oct 2026 12:30:00 GMT</pubDate></item></channel></rss>"""
            times=datetime(2026,10,15,12,30,tzinfo=timezone.utc).timestamp()
            def fetch(url):
                if "bls.gov" in url:
                    raise TimeoutError("feed blocked")
                return sample
            alerts=[]
            mm=MacroMonitor(lambda msg: alerts.append(msg) or True,
                            os.path.join(t,"state.json"),fetcher=fetch)
            mm.refresh_news(times)
            self.assertGreaterEqual(len(alerts),1)
            self.assertIn("연준",alerts[0])


if __name__=="__main__":
    unittest.main()
