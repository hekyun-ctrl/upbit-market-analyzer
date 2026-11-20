"""Conservative cost-aware replay tests; synthetic fixtures, NOT PnL."""
import unittest
from research.zb_counterfactual_replay import (
    ReplaySignal, replay_one, summarize_replays)


def minute(start, o, high, low, close):
    return dict(start=start, open=o, high=high, low=low, close=close,
                quality="stream_closed")


class CounterfactualTests(unittest.TestCase):
    def test_next_minute_entry_and_target(self):
        sig=ReplaySignal(observed_at=61,side=1,stop=99,target=103,
                         strategy="C",why_blocked="target_barrier")
        series=[minute(60,100,110,80,100),
                minute(120,100,101,99.5,100.5),
                minute(180,100.5,103.5,100,103)]
        result=replay_one(sig,series)
        self.assertEqual(result["classification"],"target_first")
        self.assertGreater(result["net_usdt_per_btc"],0)
        self.assertEqual(result["held_minutes"],2)
        self.assertEqual(result["entry"],100.03)

    def test_both_hit_same_bar_assumes_loss(self):
        sig=ReplaySignal(observed_at=61,side=-1,stop=102,target=98)
        series=[minute(120,100,103,97,100)]
        out=replay_one(sig,series)
        self.assertEqual(out["classification"],"stop_first")
        self.assertTrue(out["ambiguous"])
        self.assertLess(out["net_usdt_per_btc"],0)

    def test_missing_next_open_not_backfilled(self):
        sig=ReplaySignal(observed_at=61,side=1,stop=99,target=103)
        out=replay_one(sig,[minute(240,100,104,98,103)])
        self.assertEqual(out["classification"],"missing_next_entry_bar")

    def test_gap_and_incomplete_do_not_create_results(self):
        sig=ReplaySignal(observed_at=61,side=1,stop=95,target=105)
        series=[minute(120,100,102,98,101),minute(240,101,106,100,105)]
        self.assertEqual(replay_one(sig,series)["classification"],
                         "data_gap_or_invalid_candle")
        series[1]["quality"]="forming"
        self.assertEqual(replay_one(sig,series)["classification"],
                         "data_gap_or_invalid_candle")

    def test_fee_can_turn_gross_gain_into_net_loss(self):
        sig=ReplaySignal(observed_at=61,side=1,stop=99,target=100.1)
        out=replay_one(sig,[minute(120,100,100.2,99.9,100.1)],
                       fee_fraction=.001,exit_slippage_fraction=.0003)
        self.assertEqual(out["classification"],"target_first")
        self.assertLess(out["net_usdt_per_btc"],0)

    def test_recorded_signal13_fixture_cannot_fake_unseen_future_profit(self):
        import json
        from pathlib import Path
        fixture=json.loads((Path(__file__).parent /
            "research/fixtures/lbank_signal13_replay_inputs_20261020.json").read_text())
        assert fixture["actual_account_data"] is False
        columns=fixture["bar_columns"]
        rows=[dict(zip(columns,row)) for row in fixture["native_rows"] if row[0]==60]
        record=fixture["signal"]
        plan=record["plan"]
        signal=ReplaySignal(observed_at=int(record["opened"]),
                            side=int(plan["direction"]),
                            stop=plan["stop"],target=plan["target1"],
                            strategy=plan["strategy"],why_blocked="fixture_only")
        outcome=replay_one(signal,rows)
        self.assertEqual(outcome["classification"],"no_future_bars")
        self.assertNotIn("net_usdt_per_btc",outcome)

    def test_summary_excludes_untestable(self):
        sig=ReplaySignal(observed_at=61,side=1,stop=99,target=101)
        a=replay_one(sig,[minute(120,100,101.5,99.5,101)])
        b=replay_one(sig,[])
        s=summarize_replays([a,b])
        self.assertEqual(s["resolved"],1)
        self.assertEqual(s["untestable"],1)
        self.assertEqual(s["wins"],1)


if __name__=="__main__":
    unittest.main()
