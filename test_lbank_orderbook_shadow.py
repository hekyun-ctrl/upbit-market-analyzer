"""No-network checks for independently polled LBank futures order-book research."""
import json
import os
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from lbank_orderbook_shadow import (
    OrderBookShadow, OrderBookShadowThread,
    estimated_close_net_bps, extract_features,
    fetch_public_orderbook, shadow_direction,
)


def book(mid=80000.25, bid_qty=8, ask_qty=1, wrap=False):
    bids = [{"price": mid - 0.25 - i * 0.5, "volume": bid_qty}
            for i in range(5)]
    asks = [{"price": mid + 0.25 + i * 0.5, "volume": ask_qty}
            for i in range(5)]
    value = {"symbol": "BTCUSDT", "bids": bids, "asks": asks}
    return {"success": True, "data": value} if wrap else value


class PublicDepthShadowTests(unittest.TestCase):
    def test_low_storage_pauses_fetch_and_discards_gap_before_recovery(self):
        class StopLoop(BaseException):pass
        with tempfile.TemporaryDirectory() as directory:
            fetcher=MagicMock(return_value=book())
            worker=OrderBookShadowThread(directory=directory,fetcher=fetcher)
            worker.study.pending=[{'incomplete_before_storage_gap':True}]
            worker.storage_reserve=MagicMock()
            worker.storage_reserve.check.side_effect=[{'research_writable':False},
                                                     {'research_writable':True}]
            with patch('lbank_orderbook_shadow.time.sleep',side_effect=[None,StopLoop()]):
                with self.assertRaises(StopLoop):worker._loop()
            fetcher.assert_called_once()
            self.assertEqual(worker.study.sample_count,1)
            self.assertEqual(worker.study.outcome_count,0)

    def test_public_depth_is_validated_with_or_without_data_wrapper(self):
        for wrapped in (False, True):
            f = extract_features(book(wrap=wrapped))
            self.assertEqual(f["source"], "lbank_perpetual_public_depth")
            self.assertGreater(f["spread_bps"], 0)
            self.assertLess(f["spread_bps"], 1)
            self.assertGreater(f["depth_imbalance5"], 0.5)
            self.assertGreater(f["microprice_offset_bps"], 0)
            self.assertEqual(shadow_direction(f), 1)

    def test_short_side_is_symmetrical(self):
        f = extract_features(book(bid_qty=1, ask_qty=8))
        self.assertEqual(shadow_direction(f), -1)
        self.assertLess(f["microprice_offset_bps"], 0)

    def test_malformed_and_crossed_books_are_rejected(self):
        for obj in ({"asks": [], "bids": []},
                    {"asks": [{"price": 1, "volume": 1}],
                     "bids": [{"price": 2, "volume": 1}]},
                    {"asks": [{"price": 2, "volume": -1}],
                     "bids": [{"price": 1, "volume": 1}]},
                    {"asks": [{"price": 80000, "volume": 1}],
                     "bids": [{"price": 79000, "volume": 1}]}):
            with self.assertRaises(ValueError):
                extract_features(obj)

    def test_balanced_book_is_not_signal_even_if_spread_is_small(self):
        f = extract_features(book(bid_qty=2, ask_qty=2))
        self.assertEqual(shadow_direction(f), 0)

    def test_actual_taker_fee_and_spread_make_flat_trade_net_negative(self):
        f = extract_features(book())
        for direction in (-1, 1):
            self.assertLess(estimated_close_net_bps(
                {**f, "direction": direction}, f), -12)

    def test_horizon_results_no_lookahead_and_persistent_snapshot_log(self):
        with tempfile.TemporaryDirectory() as directory:
            shadow = OrderBookShadow(directory)
            candidate, results = shadow.observe(0, book())
            self.assertEqual(candidate["direction"], 1)
            self.assertEqual(results, [])
            balanced = book(bid_qty=1, ask_qty=1)
            _, results = shadow.observe(299, balanced)
            self.assertEqual(results, [])
            _, results = shadow.observe(300, balanced)
            self.assertEqual([x["horizon_seconds"] for x in results], [300])
            _, results = shadow.observe(900, balanced)
            self.assertEqual([x["horizon_seconds"] for x in results], [900])
            _, results = shadow.observe(1800, balanced)
            self.assertEqual([x["horizon_seconds"] for x in results], [1800])
            self.assertEqual(shadow.pending, [])
            self.assertEqual(shadow.outcome_count, 3)
            p = os.path.join(directory, "orderbook_1970-01-01.jsonl")
            with open(p, encoding="utf-8") as handle:
                lines = [json.loads(line) for line in handle]
            self.assertEqual(len([v for v in lines if v["kind"] == "outcome"]), 3)
            self.assertTrue(all(v["fills"] == "not_verified"
                                for v in lines if v["kind"] == "outcome"))

    def test_no_live_signal_or_order_method_on_shadow_thread(self):
        worker = OrderBookShadowThread(interval_seconds=30, fetcher=lambda: book())
        self.assertEqual(worker.interval, 30)
        self.assertFalse(hasattr(worker, "telegram"))
        self.assertFalse(hasattr(worker, "create_order"))

    def test_http_uses_only_documented_public_order_book_endpoint(self):
        content = json.dumps(book(wrap=True)).encode("utf-8")
        class Response:
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, limit):
                return content
        def opener(request, timeout):
            self.assertEqual(timeout, 5)
            self.assertTrue(request.full_url.startswith(
                "https://lbkperp.lbank.com/cfd/openApi/v1/pub/marketOrder?"))
            self.assertIn("depth=5", request.full_url)
            self.assertIn("symbol=BTCUSDT", request.full_url)
            return Response()
        result = fetch_public_orderbook(opener=opener)
        self.assertGreater(extract_features(result)["depth_imbalance5"], 0)


if __name__ == "__main__":
    unittest.main()
