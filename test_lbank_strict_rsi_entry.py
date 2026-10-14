"""Regression tests: neutral RSI must not produce any trade-entry review."""
import unittest
from unittest.mock import patch
from lbank_futures_monitor import (
    extreme_5m_rsi_entry_gate, classify, confirm_watch,
    format_entry_alert)


def observed(closes):
    base = (1760000000 // 300) * 300
    obs = [(base + i * 300 + j * 60 + 55, float(x))
           for i,x in enumerate(closes) for j in range(5)]
    obs.append((base + len(closes) * 300 + 30, float(closes[-1])))
    return obs


class StrictFiveMinuteEntryTests(unittest.TestCase):
    def test_long_only_after_rsi_30_and_two_completed_rebound_buckets(self):
        down = [83000. - 90 * i for i in range(30)]
        for closes in (down, down + [down[-1] + 140]):
            self.assertFalse(extreme_5m_rsi_entry_gate(observed(closes),1)[0])
        up = [down[-1] + 140 * (i+1) for i in range(6)]
        self.assertTrue(extreme_5m_rsi_entry_gate(observed(down+up),1)[0])
        self.assertFalse(extreme_5m_rsi_entry_gate(observed(down+up),-1)[0])

    def test_short_only_after_rsi_70_and_two_completed_rejection_buckets(self):
        up = [80000. + 100*i for i in range(25)]
        for closes in (up, up + [up[-1]-100]):
            self.assertFalse(extreme_5m_rsi_entry_gate(observed(closes),-1)[0])
        down = [up[-1]-100*(i+1) for i in range(5)]
        self.assertTrue(extreme_5m_rsi_entry_gate(observed(up+down),-1)[0])
        self.assertFalse(extreme_5m_rsi_entry_gate(observed(up+down),1)[0])

    def test_no_extreme_no_entry_in_any_direction(self):
        flat=[83000. + (80 if i%2 else 0) for i in range(30)]
        for d in (1,-1):
            ok,reason,_=extreme_5m_rsi_entry_gate(observed(flat),d)
            self.assertFalse(ok)
            self.assertIn("not_observed",reason)

    def test_new_entry_gate_ignores_unfinished_last_5m_bucket(self):
        flat=[83000.+(80 if i%2 else 0) for i in range(30)]
        obs=observed(flat)
        previous=[extreme_5m_rsi_entry_gate(obs,d) for d in (1,-1)]
        obs[-1]=(obs[-1][0],98000.)
        self.assertEqual(previous,[extreme_5m_rsi_entry_gate(obs,d) for d in (1,-1)])

    def test_all_pattern_previews_are_blocked_by_strict_gate(self):
        flat=[83000.+(80 if i%2 else 0) for i in range(30)]
        obs=observed(flat)
        prices=[83000.+(80 if i%2 else 0) for i in range(70)]
        for strategy in ("range_rebound","trend_retest","breakout"):
            for direction in (1,-1):
                with patch("lbank_futures_monitor.detect_structure",
                           return_value={"direction":direction,"strategy":strategy}), \
                     patch("lbank_futures_monitor.sampled_context_gate",
                           return_value=(True,"ok",{})), \
                     patch("lbank_futures_monitor.fast_sample_rsi_gate",
                           return_value=(True,"ok",50.)), \
                     patch("lbank_futures_monitor.five_minute_rsi_entry_gate",
                           return_value=(True,"legacy_ok",50.)), \
                     patch("lbank_futures_monitor.calculate_plan") as plan:
                    self.assertIsNone(classify(prices,observations=obs))
                    plan.assert_not_called()

    def test_second_stage_watches_cannot_bypass_neutral_protection(self):
        flat=[83000.+(80 if i%2 else 0) for i in range(30)]
        obs=observed(flat)
        prices=[83000.+(80 if i%2 else 0) for i in range(70)]
        for direction in (1,-1):
            watch={"direction":direction,"strategy":"trend_retest","price":prices[-1]}
            with patch("lbank_futures_monitor.confirm_structure",return_value=True), \
                 patch("lbank_futures_monitor.sampled_context_gate",
                       return_value=(True,"ok",{})), \
                 patch("lbank_futures_monitor.fast_sample_rsi_gate",
                       return_value=(True,"ok",50.)), \
                 patch("lbank_futures_monitor.five_minute_rsi_entry_gate",
                       return_value=(True,"legacy_ok",50.)), \
                 patch("lbank_futures_monitor.calculate_plan") as plan:
                self.assertIsNone(confirm_watch(prices,watch,60,observations=obs))
                plan.assert_not_called()

    def test_all_entry_alert_labels_require_extreme_rsi(self):
        p={"side":"롱","direction":1,"strategy":"trend_retest",
           "price":83000.,"stop":82880.,"target1":83400.,"target2":83600.,
           "margin":10.,"notional":500.,"net_loss":5.,"net_gain1":8.,
           "net_gain2":13.,"rr1":1.6,"rr2":2.6,"fee_roundtrip_est":.5,
           "fee_stop_est":.5,"maker_potential_savings":.2,
           "m5":.1,"m15":.3,"rsi":55.,"sampled_rsi5":45.}
        msg=format_entry_alert(p,"2026-10-14T19:00:00Z")
        self.assertIn("30 이하",msg)
        self.assertIn("중립 RSI 진입 차단",msg)
        p["side"]="숏";p["direction"]=-1
        msg=format_entry_alert(p,"2026-10-14T19:00:00Z")
        self.assertIn("70 이상",msg)


if __name__=="__main__":
    unittest.main()
