from contextlib import nullcontext
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock

from lbank_native_candles import NativeCandleStore
from lbank_video_alert import BookDataError, DeliveryLedger, PublicBook, process, read_window
from test_lbank_video_entry import ARCHIVE, breakout, mirror, reversal
from research.zerobase.video_entry import read_retained_archive


def with_receipts(rows):
    return [dict(b, received_at=b["start"]+301) for b in rows]


class VideoAlertTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.path = Path(tmp.name) / "delivery.db"
        self.ledger = DeliveryLedger(self.path)
        self.addCleanup(self.ledger.db.close)

    def decision(self, bars, *, quote=None, send=None, now_offset=2, clock_offset=0,
                 started=None):
        rows = with_receipts(bars)
        end = rows[-1]["start"]+300
        now = end+now_offset
        c = rows[-1]["close"]
        quote = quote or (lambda: (c-.1, c+.1))
        return process(rows, now=now, started=end-1 if started is None else started,
                       clock=lambda: now+clock_offset, get_book=quote, ledger=self.ledger, send=send)

    def test_each_strategy_can_send_either_direction_on_closed_5m_data(self):
        for index, (fixture, name) in enumerate(((breakout()[:46], "볼린저밴드·CCI"),
                                                (reversal(), "RSI 반전형"))):
            for j, (rows, side) in enumerate(((fixture, "롱"), (mirror(fixture), "숏"))):
                rows = [dict(b, start=b["start"]+(index*2+j)*86400) for b in rows]
                with self.subTest(name=name, side=side):
                    send = Mock(return_value=True)
                    status, _ = self.decision(rows, send=send)
                    self.assertEqual(status, "telegram_accepted")
                    send.assert_called_once()
                    msg = send.call_args.args[0]
                    for word in ("별도 영상 전략 알람", "5분봉", name, side,
                                 "목표", "손절", "허용 진입가", "손익비"):
                        self.assertIn(word, msg)

    def test_missing_stale_or_wide_book_never_sends(self):
        rows = breakout()[:46]
        for quote, delay, status in ((Mock(side_effect=TimeoutError), 0, "book_unavailable"),
                                     (lambda: (1003., 1003.), 0, "invalid_book"),
                                     (lambda: (1002., 1004.), 0, "spread_too_wide"),
                                     (lambda: (1002.9, 1003.1), 6, "book_too_slow"),
                                     (lambda: (1006., 1006.1), 0, "book_rejected"),
                                     (lambda: (999.9, 1000.), 0, "book_rejected")):
            send = Mock()
            logged = self.assertLogs("lbank_video_alert", "WARNING") if status == "book_unavailable" else nullcontext()
            with self.subTest(status=status), logged:
                self.assertEqual(self.decision(rows, quote=quote, send=send,
                                               clock_offset=delay)[0], status)
                send.assert_not_called()

    def test_restart_does_not_repeat_uncertain_telegram_delivery(self):
        rows = breakout()[:46]
        send = Mock(return_value=False)
        self.assertEqual(self.decision(rows, send=send)[0], "telegram_unconfirmed_no_retry")
        other = DeliveryLedger(self.path)
        self.addCleanup(other.db.close)
        self.assertFalse(other.claim(rows[-1]["start"], "rsi_reversal"))
        self.assertEqual(self.decision(rows, send=send)[0], "already_reserved")
        send.assert_called_once()

    def test_public_book_requires_exact_symbol_and_positive_size(self):
        data = dict(symbol="BTCUSDT", bids=[dict(price="0", volume="0"),
                    dict(price="99.5", volume="1")], asks=[dict(price="101", volume="1"),
                    dict(price="100.5", volume="3")])
        fetch = Mock(return_value={"data": data})
        self.assertEqual(PublicBook(fetch)(), (99.5, 100.5))
        fetch.assert_called_once_with({"symbol": "BTCUSDT", "depth": 20})
        data["symbol"] = "ETHUSDT"
        with self.assertRaises(ValueError):
            PublicBook(fetch)()

    def test_live_candidate_uses_alternate_depth_after_empty_and_crossed_snapshots(self):
        def payload(bid, ask):
            return {"data": dict(symbol="BTCUSDT", bids=[dict(price=bid, volume=bool(bid))],
                                 asks=[dict(price=ask, volume=1)])}
        fetch = Mock(side_effect=[payload(0, 1003.1), payload(1003.2, 1003.1),
                                  payload(1002.9, 1003.1), payload(1002.8, 1003.2)])
        book = PublicBook(fetch)
        sent = Mock(return_value=True)
        self.assertEqual(self.decision(breakout()[:46], quote=book, send=sent)[0],
                         "telegram_accepted")
        sent.assert_called_once()
        self.assertEqual(book(), (1002.8, 1003.2))
        self.assertEqual([c.args[0]["depth"] for c in fetch.call_args_list], [20, 10, 50, 50])

    def test_book_does_not_return_valid_quote_after_deadline(self):
        now = [0.]
        def slow(_):
            now[0] = 4.01
            return {"data": {"symbol": "BTCUSDT", "bids": [{"price": 100, "volume": 1}],
                             "asks": [{"price": 100.1, "volume": 1}]}}
        fetch = Mock(side_effect=slow)
        with self.assertRaisesRegex(BookDataError, "deadline"):
            PublicBook(fetch, clock=lambda: now[0])()
        fetch.assert_called_once()

    def test_book_bounds_attempts_and_rotates_after_total_failure(self):
        fetch = Mock(side_effect=TimeoutError)
        book = PublicBook(fetch)
        for _ in range(2):
            with self.assertRaisesRegex(BookDataError, "snapshot_attempts"):
                book()
        self.assertEqual([c.args[0]["depth"] for c in fetch.call_args_list],
                         [20, 10, 50, 5, 60, 1, 20, 10])

    def test_no_history_or_delayed_alert_and_disabled_is_observation_only(self):
        rows = breakout()[:46]
        send, quote = Mock(), Mock()
        self.assertEqual(self.decision(rows, quote=quote, send=send,
                                       started=rows[-1]["start"]+300)[0], "stale_or_before_start")
        self.assertEqual(self.decision(rows, quote=quote, send=send,
                                       now_offset=31)[0], "stale_or_before_start")
        self.assertEqual(self.decision(rows[:39], quote=quote, send=send)[0], "warmup")
        quote.assert_not_called()
        send.assert_not_called()
        self.assertEqual(self.decision(rows)[0], "send_disabled")
        self.assertTrue(self.ledger.claim(rows[-1]["start"], "bb_cci_breakout"))

    def test_sqlite_window_requires_exact_closed_bar_and_real_receipt(self):
        src = self.path.with_name("native.db")
        store = NativeCandleStore(str(src))
        self.addCleanup(store.db.close)
        with store.db:
            for b in with_receipts(breakout()[:46]):
                s = b["start"]
                store.db.execute("INSERT INTO native_bars VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (300,s,b["open"],b["high"],b["low"],b["close"],b["volume"],0,s+299,"stream_closed"))
                store.db.execute("INSERT INTO native_arrivals VALUES(?,?,?,?)",
                                 (300,s,b["received_at"],"lbank_native_1m_derived_5m"))
            store.db.execute("DELETE FROM native_arrivals WHERE start=?", (s-600,))
        self.assertEqual(read_window(src, s+300), [])
        self.assertEqual([b["start"] for b in read_window(src, s+302)], [s-300, s])

    def test_archived_bars_have_no_eligible_alerts(self):
        # Historical closes are assigned nominal receipts only for this test.
        # The live worker can read only receipts stored by its own recorder.
        archive, run, quotes, send = read_retained_archive(ARCHIVE), [], Mock(), Mock()
        for b in archive:
            if run and b["start"] != run[-1]["start"]+300:
                run.clear()
            run.append(b)
            status, _ = self.decision(run, quote=quotes, send=send)
            self.assertIn(status, {"warmup", "no_qualified_entry"})
        quotes.assert_not_called()
        send.assert_not_called()
