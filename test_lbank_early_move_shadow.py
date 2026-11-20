"""No future bar, stale-entry bypass, forced Telegram, or 5m confirmation wait."""
import unittest
from lbank_early_move_shadow import early_move_shadow, rejected_entry_review


def bars(closes):
    data=[]
    for i,c in enumerate(closes):
        o=(closes[i-1] if i else c+5)
        data.append(dict(start=i*60,received_at=(i+1)*60+2,
                         quality="stream_closed",open=o,close=c,
                         low=min(o,c)-1,high=max(o,c)+1))
    return data


class EarlyMoveTests(unittest.TestCase):
    def test_early_downside_break_and_time_integrity(self):
        series=bars([1000,1000,1000,1001,999,997,996,996,994,991])
        event=early_move_shadow(series,605,direction=-1,
                                trend15=-1,trend60=1)
        self.assertEqual(event["status"],"early_break_shadow")
        self.assertEqual(event["context"],"mixed")
        self.assertFalse(event["telegram_send"])
        self.assertFalse(event["entry_approved"])
        future=early_move_shadow(series,601,direction=-1)
        self.assertEqual(future["status"],"invalid_or_gapped_native_minutes")

    def test_no_early_chase_override_and_duplicate_cooldown(self):
        series=bars([1000,1000,1000,1001,999,997,996,996,994,991])
        event=early_move_shadow(series,605,direction=-1,
                                prior_notice_at=600,prior_notice_direction=-1)
        self.assertEqual(event["status"],"duplicate_suppressed")
        chase=bars([1000,1000,1000,1001,999,997,996,996,985,975])
        result=early_move_shadow(chase,605,direction=-1)
        self.assertEqual(result["status"],"late_move_chase_prohibited")

    def test_native_gap_and_open_minute_are_rejected(self):
        series=bars([1000,1000,1000,1001,999,997,996,996,994,991])
        series[-1]["start"]+=60
        self.assertEqual(early_move_shadow(series,700)["status"],
                         "invalid_or_gapped_native_minutes")
        series=bars([1000,1000,1000,1001,999,997,996,996,994,991])
        series[-1]["quality"]="forming"
        self.assertEqual(early_move_shadow(series,700)["status"],
                         "invalid_or_gapped_native_minutes")

    def test_stale_price_is_rechecked_not_approved(self):
        row=bars([1000])[-1]
        test=rejected_entry_review("price_deviation",1000,996,
                                   observed_at=65,latest_closed_bar=row)
        self.assertEqual(test["status"],"fresh_structure_reassessment_needed")
        self.assertTrue(test["stale_entry_disallowed"])
        self.assertFalse(test["entry_approved"])
        self.assertFalse(test["telegram_send"])
        test=rejected_entry_review("price_deviation",1000,996,
                                   observed_at=61,latest_closed_bar=row)
        self.assertEqual(test["status"],"await_fresh_closed_minute")

    def test_repeated_chase_is_research_only(self):
        test=rejected_entry_review("avoid_late_5m_impulse_chase",
                                   61774.8,61704.4,
                                   observed_at=1000,latest_closed_bar=None)
        self.assertEqual(test["status"],"research_earlier_signal_only_no_chase_override")


if __name__=="__main__":
    unittest.main()
