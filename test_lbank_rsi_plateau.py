"""Regression tests for RSI first-turn → sideways pause → confirmed resume."""
import unittest
from unittest.mock import patch
from lbank_rsi_plateau import consolidation_reversal_resume
from lbank_futures_monitor import (confirmed_sampled_5m_plateau_resume,
                                   extreme_5m_rsi_entry_gate,
                                   five_minute_rsi_entry_gate)
from test_lbank_strict_rsi_entry import observed


def base_series(direction=1):
    if direction == 1:
        prices = ([83000.]*14 + [82000., 82045.,
                  82048., 82046., 82052., 82042., 82110.])
        rsis = ([50.]*14 + [27., 31., 31.5, 31.3, 31.8, 32.2, 35.])
    else:
        prices = ([83000.]*14 + [84000., 83945.,
                  83948., 83940., 83955., 83950., 83880.])
        rsis = ([50.]*14 + [73., 69., 68.8, 69.3, 68.2, 68.7, 66.])
    return prices, rsis


class PlateauReversalTests(unittest.TestCase):
    def test_long_one_turn_four_sideways_then_resume(self):
        p,v = base_series(1)
        self.assertTrue(consolidation_reversal_resume(p,v,1)[0])
        self.assertFalse(consolidation_reversal_resume(p[:-1],v[:-1],1)[0])

    def test_short_one_turn_four_sideways_then_resume(self):
        p,v=base_series(-1)
        self.assertTrue(consolidation_reversal_resume(p,v,-1)[0])
        self.assertFalse(consolidation_reversal_resume(p[:-1],v[:-1],-1)[0])

    def test_requires_real_rsi_extreme_no_neutral_near_misses(self):
        p,v=base_series(1)
        v[14]=32.
        self.assertFalse(consolidation_reversal_resume(p,v,1)[0])
        p,v=base_series(-1)
        v[14]=68.
        self.assertFalse(consolidation_reversal_resume(p,v,-1)[0])

    def test_fails_on_break_of_original_pivot_or_wide_pause(self):
        p,v=base_series(1)
        p[18]=81965.
        self.assertFalse(consolidation_reversal_resume(p,v,1)[0])
        p,v=base_series(1)
        p[18]=82280.
        self.assertFalse(consolidation_reversal_resume(p,v,1)[0])
        p,v=base_series(-1)
        p[18]=84035.
        self.assertFalse(consolidation_reversal_resume(p,v,-1)[0])

    def test_no_early_entry_on_flat_rsi_or_false_breakout(self):
        p,v=base_series(1)
        v[-1]=32.6
        self.assertFalse(consolidation_reversal_resume(p,v,1)[0])
        p,v=base_series(1)
        p[-1]=82064.
        self.assertFalse(consolidation_reversal_resume(p,v,1)[0])
        p,v=base_series(-1)
        v[-1]=68.4
        self.assertFalse(consolidation_reversal_resume(p,v,-1)[0])

    def test_stale_extreme_and_missing_samples_fail_closed(self):
        p,v=base_series(1)
        # Prior extreme older than 12 plateau buckets.
        p=p[:16]+[82045.]*14+[82110.]
        v=v[:16]+[32.]*14+[35.]
        self.assertFalse(consolidation_reversal_resume(p,v,1)[0])
        obs=observed([83000.]*32)
        # missing a complete bucket must not produce an authorization.
        bad_bucket=int(obs[47][0]//300)
        obs=[item for item in obs if int(item[0]//300)!=bad_bucket]
        self.assertFalse(confirmed_sampled_5m_plateau_resume(obs,1)[0])

    def test_incomplete_current_bucket_cannot_create_breakout(self):
        obs=observed([83000.]*32)
        prev=confirmed_sampled_5m_plateau_resume(obs,1)
        obs[-1]=(obs[-1][0],98000.)
        self.assertEqual(prev, confirmed_sampled_5m_plateau_resume(obs,1))

    def test_global_gate_and_strategy_gate_share_plateau_path(self):
        for side in (1,-1):
            p,v=base_series(side)
            mock={"closes":p,"rsi":v}
            with patch("lbank_futures_monitor.sampled_five_minute_rsi",return_value=mock):
                ok,reason,_=extreme_5m_rsi_entry_gate([],side)
                self.assertTrue(ok,reason)
                self.assertEqual(reason,"5m_extreme_then_first_turn_plateau_resume")
                for strategy in ("range_rebound","trend_retest","breakout"):
                    allowed,rr,_=five_minute_rsi_entry_gate(
                        [],side,setup={"strategy":strategy})
                    self.assertTrue(allowed,(strategy,rr))


if __name__=="__main__":
    unittest.main()
