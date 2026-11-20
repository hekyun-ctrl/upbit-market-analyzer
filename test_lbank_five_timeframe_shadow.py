"""Deterministic tests for independently aligned sealed multi-timeframe candles."""
import copy
import unittest
from lbank_five_timeframe_shadow import five_timeframe_shadow


def bars(period, count, end, step):
    out = []
    for i in range(count):
        start = end - (count-i)*period
        close = 10000.0+i*step
        o=close-step
        out.append(dict(start=start,updated=start+period,received_at=start+period,
                        quality="stream_closed",open=o,close=close,
                        high=max(o,close)+2,low=min(o,close)-2))
    return out


class FiveTimeframeTests(unittest.TestCase):
    def setUp(self):
        self.now=1728000
        self.state=dict(minute_bars=bars(60,360,self.now,4),
                        five_bars=bars(300,250,self.now,20),
                        quote=20000.0)

    def test_all_five_resolved_from_closed_bars(self):
        before=copy.deepcopy(self.state)
        out=five_timeframe_shadow(self.state,self.now)
        self.assertEqual([out[t]["direction"] for t in ("1m","5m","15m","1h","4h")],
                         ["up","up","up","up","up"])
        self.assertFalse(out["entry_approved"])
        self.assertEqual(out["higher_conflict"],"not_opposed")
        self.assertEqual(self.state,before)

    def test_no_latest_4h_when_source_missing(self):
        state=copy.deepcopy(self.state)
        # Remove 5m candle inside the last sealed 4h block; other timeframes
        # should remain observable when their local windows are unaffected.
        state["five_bars"][-40]["start"]-=1
        out=five_timeframe_shadow(state,self.now)
        self.assertEqual(out["4h"]["direction"],"unverified")
        self.assertEqual(out["1m"]["direction"],"up")

    def test_future_closed_candle_never_accepted(self):
        state=copy.deepcopy(self.state)
        state["minute_bars"][-1]["updated"]=self.now+50
        out=five_timeframe_shadow(state,self.now)
        self.assertEqual(out["1m"]["direction"],"unverified")
        self.assertEqual(out["4h"]["direction"],"up")

    def test_late_receipt_is_not_backdated_to_exchange_update(self):
        self.state["minute_bars"][-1]["received_at"] = self.now + 1
        out = five_timeframe_shadow(self.state, self.now)
        self.assertEqual(out["1m"]["direction"], "unverified")
        self.assertEqual(out["main_bias"], "long")

    def test_duplicate_five_bar_cannot_replace_actual_observation(self):
        self.state["five_bars"].append(copy.deepcopy(self.state["five_bars"][-1]))
        self.assertEqual(five_timeframe_shadow(self.state, self.now)["5m"]["direction"],
                         "unverified")

    def test_asof_4h_not_partial_4h(self):
        # One minute into a new 4h period: use last COMPLETE 4h, never form
        # an artificial partial 4h candle.
        now=self.now+60
        out=five_timeframe_shadow(self.state,now)
        self.assertEqual(out["4h"]["direction"],"up")
        self.assertEqual(out["4h"]["return_pct"],five_timeframe_shadow(self.state,self.now)["4h"]["return_pct"])


if __name__=="__main__":
    unittest.main()
