import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from capture_native_quote_baseline import classify, capture, load_parser, summarize, PUBLIC_URL


def book():
    return {"symbol": "BTCUSDT", "asks": [{"price": 100.01,"volume": 2}], "bids": [{"price": 100,"volume": 3}]}


class Clock:
    def __init__(self): self.t = 0
    def __call__(self): return self.t
    def sleep(self, s): self.t += s
    def now(self):
        from datetime import datetime,timezone
        return datetime.fromtimestamp(1700000000+self.t,timezone.utc).isoformat()


class NativeBaselineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls): cls.extractor = staticmethod(load_parser())

    def test_bare_and_wrapped_depth(self):
        for p in [book(), {"success": True,"error_code": 0,"data":book()}]:
            r = classify(json.dumps(p).encode(),self.extractor)
            self.assertTrue(r["response_symbol_verified"])
            self.assertGreater(r["features"]["spread_bps"],0)

    def test_wrong_instrument_rejected(self):
        p=book();p["symbol"]="ETHUSDT"
        with self.assertRaises(ValueError):classify(json.dumps(p).encode(),self.extractor)

    def test_crossed_depth_rejected(self):
        p=book();p["asks"][0]["price"]=99
        with self.assertRaises(ValueError):classify(json.dumps(p).encode(),self.extractor)

    def test_explicit_API_failure_rejected(self):
        with self.assertRaises(ValueError):classify(json.dumps({"success":False,"data":book()}).encode(),self.extractor)

    def test_timestamp_candidate_not_book_age(self):
        p=book();p["timestamp"]=1700000000
        r=classify(json.dumps(p).encode(),self.extractor)
        self.assertEqual(len(r["timestamp_candidates"]),1)
        self.assertFalse(r["exchange_book_event_time_verified"])
        self.assertIsNone(r["event_age_seconds"])

    def test_identical_book_different_metadata(self):
        p=book();a=classify(json.dumps(p).encode(),self.extractor)
        p["time"]=123;b=classify(json.dumps(p).encode(),self.extractor)
        self.assertEqual(a["book_sha256"],b["book_sha256"])

    def test_bounded_fake_capture_no_live_network(self):
        clock=Clock()
        def get(url,timeout):
            self.assertEqual(url,PUBLIC_URL);self.assertLessEqual(timeout,5)
            clock.t+=.1
            return 200,{"date":"example"},json.dumps(book()).encode(),url
        with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
            r=capture(Path(d)/"capture",get,clock,clock.sleep,clock.now,self.extractor)
            self.assertEqual(r["summary"]["attempted"],12)
            self.assertEqual(r["summary"]["book_duplicate_observations"],11)
            self.assertEqual(r["summary"]["unknown_book_freshness"],12)
            self.assertAlmostEqual(clock.t,55.1)
            with self.assertRaises(FileExistsError):capture(Path(d)/"capture",get,clock,clock.sleep,clock.now,self.extractor)

    def test_permission_and_rate_limit_stop(self):
        for status in [403,429]:
            clock=Clock()
            def get(url,timeout):return status,{},b'blocked',url
            with tempfile.TemporaryDirectory() as d, contextlib.redirect_stdout(io.StringIO()):
                r=capture(Path(d)/"capture",get,clock,clock.sleep,clock.now,self.extractor)
                self.assertEqual(r["summary"]["attempted"],1)
                self.assertEqual(r["summary"]["unattempted"],11)
                self.assertEqual(r["summary"]["successful_depth"],0)


if __name__ == "__main__":unittest.main()
