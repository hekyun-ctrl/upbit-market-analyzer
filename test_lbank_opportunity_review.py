"""Fail-closed and at-most-once tests for prioritized candidate delivery."""
import sqlite3
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch
from lbank_opportunity_review import evaluate_review, send_review, announce_release


class OpportunityReviewTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.ledger = SimpleNamespace(db=self.db)
        self.start = 1794700000 // 300 * 300
        self.bars = []
        for i in range(90):
            t = self.start + i*300
            self.bars.append(dict(start=t, period=300, open=10000.,
                high=10008., low=9990., close=10000., volume=50.,
                quality="stream_closed", received_at=t+301))
        self.now = self.bars[-1]["start"]+303
        self.obs = dict(status="observed", atr14=45.,
            resistance=10050., support=9990., volume_multiple=2.5,
            lanes={"A":{"status":"no_event"}, "B":{"status":"observed_not_entry"},
                   "C":{"status":"no_event"}})
        self.rank = dict(status="ready", trend="bearish",
                         directions={"5m":-1,"15m":-1,"1h":-1})

    def check(self, *, price=(9999.9, 10000.1), barriers=True,
              count=90, rank=None, obs=None, return_quote=False):
        bars = self.bars[-count:]
        b = [{"price":9400.,"available_at":self.now-10}] if barriers else []
        quote = Mock(return_value=price)
        with patch("lbank_opportunity_review.parallel_observation",return_value=obs or self.obs), \
             patch("lbank_opportunity_review.trend_priority",return_value=rank or self.rank) as trend, \
             patch("lbank_opportunity_review.available_barriers",return_value=b):
            result = evaluate_review(bars, now=self.now,
                                     get_book=quote, clock=lambda:self.now)
        return (result, trend, quote) if return_quote else result


    def test_20_bars_do_not_trigger_early_candidate(self):
        x, trend, quote = self.check(count=20, return_quote=True)
        self.assertEqual(x["status"], "insufficient_structure_history")
        self.assertEqual(x["required"], 21)
        trend.assert_not_called()
        quote.assert_not_called()

    def test_21_bars_allow_early_review_without_72_bar_gate(self):
        x, trend, quote = self.check(count=21, return_quote=True)
        self.assertEqual(x["status"], "review_candidate")
        self.assertEqual(x["review_grade"], "early_structure_only")
        self.assertEqual(x["trend"], "unknown")
        self.assertFalse(x["higher_timeframe_confirmed"])
        self.assertEqual(x["bars_seen"], 21)
        self.assertGreaterEqual(x["net_rr_assumed"], 2.0)
        trend.assert_not_called()
        quote.assert_called_once()

    def test_71_bars_remain_early_not_falsely_hourly_confirmed(self):
        x, trend, _ = self.check(count=71, return_quote=True)
        self.assertEqual(x["review_grade"], "early_structure_only")
        trend.assert_not_called()

    def test_72_bars_use_higher_timeframe_filter(self):
        conflict = dict(self.rank, trend="bullish")
        x = self.check(count=72, rank=conflict)
        self.assertEqual(x["status"], "no_high_priority_structure")
        y = self.check(count=72)
        self.assertEqual(y["review_grade"], "higher_timeframe_aligned")
        self.assertTrue(y["higher_timeframe_confirmed"])

    def test_21_bar_early_message_is_explicitly_not_an_entry(self):
        from lbank_opportunity_review import format_review
        x = self.check(count=21)
        message = format_review(x)
        self.assertIn("상위봉 미확인", message)
        self.assertIn("15분·1시간 합의는 미검증", message)
        self.assertIn("정식 진입 승인 아님", message)
        self.assertIn("연속 5분봉 21개", message)

    def test_21_bar_candidate_without_confirmed_target_does_not_pass(self):
        x = self.check(count=21, barriers=False)
        self.assertEqual(x["status"], "screen_rejected")

    def test_72_bar_unusable_higher_timeframe_data_fails_closed(self):
        invalid = {"status":"invalid_source", "trend":"unknown", "directions":{}}
        x, _, quote = self.check(count=72, rank=invalid, return_quote=True)
        self.assertEqual(x["status"], "higher_timeframe_source_invalid")
        quote.assert_not_called()

    def test_21_bar_no_actual_trigger_never_calls_quote(self):
        obs = dict(self.obs, lanes={lane:{"status":"no_event"} for lane in ("A","B","C")})
        x, _, quote = self.check(count=21, obs=obs, return_quote=True)
        self.assertEqual(x["status"], "no_high_priority_structure")
        quote.assert_not_called()

    def test_gap_in_21_structure_window_fails_closed(self):
        rows = [dict(x) for x in self.bars[-21:]]
        rows[8]["start"] += 300
        self.assertEqual(evaluate_review(rows, now=self.now,
            get_book=Mock())["status"], "invalid_or_gapped_candles")

    def test_eligible_short_has_real_stop_and_observed_target(self):
        x = self.check()
        self.assertEqual(x["status"], "review_candidate")
        self.assertEqual(x["lane"], "B")
        self.assertGreater(x["stop"], x["quote"])
        self.assertLess(x["target"], x["quote"])
        self.assertGreaterEqual(x["net_rr_assumed"], 2.)

    def test_unknown_target_never_qualifies(self):
        self.assertEqual(self.check(barriers=False)["status"],"screen_rejected")

    def test_crossed_book_never_qualifies(self):
        self.assertEqual(self.check(price=(10000.,10000.))["status"],
                         "stale_or_wide_book")

    def test_stale_candle_cannot_be_sent(self):
        self.assertEqual(evaluate_review(self.bars,now=self.now+60,
                         get_book=Mock()), {"status":"stale_or_unverified"})

    def test_no_candidate_no_message(self):
        sender=Mock()
        self.assertEqual(send_review(self.ledger,self.bars,now=self.now,
            started=self.now-300,get_book=Mock(),send=sender,
            enabled=False,final_status="no_qualified_entry"),"disabled")
        sender.assert_not_called()

    def test_manual_signal_lock_wins(self):
        sender=Mock()
        self.assertEqual(send_review(self.ledger,self.bars,now=self.now,
            started=self.now-300,get_book=Mock(),send=sender,
            enabled=True,final_status="telegram_accepted"),"formal_alert_priority")
        sender.assert_not_called()

    def test_early_review_delivery_dedupes_and_keeps_warning(self):
        candidate = self.check(count=21)
        sender = Mock(return_value=True)
        with patch("lbank_opportunity_review.evaluate_review",
                   return_value=candidate):
            first = send_review(
                self.ledger, self.bars[-21:], now=self.now,
                started=self.now-1000, get_book=Mock(),
                send=sender, enabled=True,
                final_status="no_qualified_entry", clock=lambda:self.now)
            again = send_review(
                self.ledger, self.bars[-21:], now=self.now,
                started=self.now-1000, get_book=Mock(),
                send=sender, enabled=True,
                final_status="no_qualified_entry", clock=lambda:self.now)
        self.assertEqual(first, "B:accepted")
        self.assertEqual(again, "cooldown")
        sender.assert_called_once()
        self.assertIn("상위봉 미확인", sender.call_args.args[0])
        self.assertIn("정식 진입 승인 아님", sender.call_args.args[0])

    def test_failed_screen_reports_reason_without_crashing_worker(self):
        sender=Mock()
        with patch("lbank_opportunity_review.evaluate_review",
                   return_value={"status":"screen_rejected",
                                 "reasons":["no_known_target"]}):
            with self.assertLogs("lbank_opportunity_review","INFO") as logs:
                outcome=send_review(
                    self.ledger,self.bars,now=self.now,started=self.now-1000,
                    get_book=Mock(),send=sender,enabled=True,
                    final_status="no_qualified_entry")
        self.assertEqual(outcome,"screen_rejected")
        self.assertTrue(any("no_known_target" in line for line in logs.output))
        sender.assert_not_called()

    def test_release_notice_dedupes_across_restarts(self):
        sender=Mock(return_value=True)
        self.assertEqual(announce_release(self.ledger,revision="abc",
            now=self.now,send=sender,enabled=True),"accepted")
        self.assertEqual(announce_release(self.ledger,revision="abc",
            now=self.now,send=sender,enabled=True),"already_reserved")
        self.assertEqual(sender.call_count,1)
        self.assertIn("진입 신호가 아닙니다",sender.call_args.args[0])




class FiveBarCheckinTests(unittest.TestCase):
    def setUp(self):
        from lbank_five_bar_checkin import _slot_end
        from datetime import datetime, timezone
        self.db = sqlite3.connect(":memory:")
        self.addCleanup(self.db.close)
        self.ledger = SimpleNamespace(db=self.db)
        # 2026-11-18 13:20 KST is exactly a 25-minute boundary.
        self.slot = int(datetime(2026,11,18,4,20,tzinfo=timezone.utc).timestamp())
        self.assertEqual(_slot_end(self.slot+15), self.slot)
        self.now = self.slot+15
        self.bars = []
        for i in range(21):
            t = self.slot - 21*300 + i*300
            self.bars.append(dict(start=t, period=300, open=64000.,
                high=64020., low=63980., close=64000., volume=15.,
                quality="stream_closed", received_at=t+302))

    def send(self, bars=None, **opts):
        from lbank_five_bar_checkin import send_five_bar_checkin
        sender = opts.pop("sender", Mock(return_value=True))
        now = opts.pop("now", self.now)
        out = send_five_bar_checkin(
            self.ledger, self.bars if bars is None else bars,
            now=now, started=self.slot-1500,
            get_book=Mock(return_value=(63999.9,64000.1)),
            send=sender, enabled=True, clock=lambda:now, **opts)
        return out,sender

    def test_five_bar_checkin_sent_with_no_entry(self):
        with patch("lbank_five_bar_checkin.parallel_observation",
                   return_value={"status":"observed",
                       "lanes":{a:{"status":"no_event"} for a in ("A","B","C")}}), \
             patch("lbank_five_bar_checkin.evaluate_review",
                   return_value={"status":"no_high_priority_structure"}):
            result,sender=self.send()
        self.assertEqual(result,"no_entry:accepted")
        self.assertIn("진입 보류",sender.call_args.args[0])
        self.assertIn("5분봉 5개",sender.call_args.args[0])
        self.assertIn("64,000.0",sender.call_args.args[0])
        self.assertIn("진입가·손절가·목표가",sender.call_args.args[0])

    def test_no_five_closed_bars_wait_then_report_missing(self):
        before,sender=self.send(bars=[],now=self.slot+15)
        self.assertEqual(before,"waiting_for_fresh_bar")
        sender.assert_not_called()
        status,sender=self.send(bars=[],now=self.slot+45)
        self.assertEqual(status,"source_unavailable:accepted")
        self.assertIn("진입 금지",sender.call_args.args[0])
        self.assertIn("시세 자료 부족",sender.call_args.args[0])

    def test_25_minute_boundaries_not_hourly(self):
        from lbank_five_bar_checkin import _slot_end
        self.assertEqual(_slot_end(self.slot+1499),self.slot)
        self.assertEqual(_slot_end(self.slot+1500),self.slot+1500)
        result,sender=self.send(now=self.slot+150)
        self.assertEqual(result,"not_due")
        sender.assert_not_called()

    def test_25m_reservation_stops_duplicate_after_restart(self):
        with patch("lbank_five_bar_checkin.parallel_observation",
                   return_value={"status":"observed",
                       "lanes":{a:{"status":"no_event"} for a in ("A","B","C")}}), \
             patch("lbank_five_bar_checkin.evaluate_review",
                   return_value={"status":"no_high_priority_structure"}):
            sender=Mock(return_value=True)
            self.assertEqual(self.send(sender=sender)[0],"no_entry:accepted")
            self.assertEqual(self.send(sender=sender)[0],"already_reserved")
        sender.assert_called_once()

    def test_25m_candidate_is_not_trade_approval(self):
        event={"status":"review_candidate", "lane":"B", "direction":-1,
             "quote":63999.9, "stop":64080., "target":63500.,
             "net_rr_assumed":2.2}
        observed={"status":"observed",
                 "lanes":{a:{"status":"observed_not_entry" if a=="B" else "no_event"}
                          for a in ("A","B","C")}}
        with patch("lbank_five_bar_checkin.parallel_observation",
                   return_value=observed), \
             patch("lbank_five_bar_checkin.evaluate_review",return_value=event):
            result,sender=self.send()
        self.assertEqual(result,"candidate:accepted")
        body=sender.call_args.args[0]
        self.assertIn("숏 최우선 관찰",body)
        self.assertIn("손절 가설",body)
        self.assertIn("2.20:1",body)
        self.assertIn("실전 진입 미승인",body)

    def test_bars_with_gap_cannot_justify_entry(self):
        from lbank_five_bar_checkin import _validate_five
        wrong=[dict(x) for x in self.bars]
        wrong[-3]["start"]+=300
        self.assertFalse(_validate_five(wrong,self.slot,self.now))
        out,sender=self.send(bars=wrong,now=self.slot+45)
        self.assertEqual(out,"source_unavailable:accepted")
        self.assertIn("진입 금지",sender.call_args.args[0])

    def test_invalid_receipt_rejected(self):
        from lbank_five_bar_checkin import _validate_five
        x=[dict(a) for a in self.bars]
        x[-1]["received_at"]-=400
        self.assertFalse(_validate_five(x,self.slot,self.now))

    def test_network_timeout_reserved_no_duplicate(self):
        sender=Mock(side_effect=TimeoutError("network"))
        with patch("lbank_five_bar_checkin.parallel_observation",
                   return_value={"status":"observed",
                       "lanes":{a:{"status":"no_event"} for a in ("A","B","C")}}), \
             patch("lbank_five_bar_checkin.evaluate_review",
                   return_value={"status":"no_high_priority_structure"}):
            self.assertEqual(self.send(sender=sender)[0],"no_entry:unconfirmed_no_retry")
            self.assertEqual(self.send(sender=sender)[0],"already_reserved")
        sender.assert_called_once()

    def test_not_enabled_cannot_send(self):
        from lbank_five_bar_checkin import send_five_bar_checkin
        sender=Mock()
        result=send_five_bar_checkin(
            self.ledger,self.bars,now=self.now,started=self.slot-1500,
            get_book=Mock(),send=sender,enabled=False)
        self.assertEqual(result,"disabled")
        sender.assert_not_called()


if __name__ == "__main__":
    unittest.main()
