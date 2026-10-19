"""Meaningful causality, gap, execution and refusal boundaries for research."""
import copy
import json
import unittest
from datetime import datetime,timezone
from pathlib import Path
from unittest.mock import patch
from research.lbank_strategy_rebuild import (segments,hypotheses,hourly_direction,net_unit,
    plan,trade,promotion_gate,run,NAMES)

DATA=Path(__file__).parent/'research/fixtures/lbank_strategy_rebuild_native_20261020.json'


def row(start=0,o=100,h=103,l=98,c=101,v=10,delay=1):
    return dict(bar=dict(period=300,start=start,open=o,high=h,low=l,close=c,volume=v,quality='stream_closed'),
                available_at=datetime.fromtimestamp(start+300+delay,timezone.utc).isoformat())


class StrategyRebuildTests(unittest.TestCase):
    def test_gaps_split_and_conflicting_duplicates_fail(self):
        rows=[row(0),row(300),row(900)]
        self.assertEqual([len(s) for s in segments(rows)],[2,1])
        self.assertEqual(len(segments(rows+[copy.deepcopy(rows[0])])[0]),2)
        bad=copy.deepcopy(rows[0]);bad['bar']['close']=102
        with self.assertRaises(ValueError):segments(rows+[bad])

    def test_forming_future_published_and_invalid_geometry_do_not_enter_research(self):
        for key,value in [('quality','forming'),('high',90),('volume',-1)]:
            bad=row();bad['bar'][key]=value
            with self.assertRaises(ValueError):segments([bad])
        bad=row(delay=-1)
        with self.assertRaises(ValueError):segments([bad])

    def test_hour_direction_requires_three_complete_aligned_hours(self):
        bars=[row(i*300,c=100+i,h=105+i,l=90+i,o=99+i)['bar'] for i in range(36)]
        self.assertEqual(hourly_direction(bars),1)
        self.assertEqual(hourly_direction(bars[:-1]),0)
        self.assertEqual(hourly_direction(bars[:10]+bars[11:]),0)

    def test_decisions_are_unchanged_by_unseen_future_mutations(self):
        data=json.loads(DATA.read_text());part=segments(data['bars'])[0]
        before=[]
        for n in range(22,len(part)+1):
            events=hypotheses(part[:n])
            if events:
                before.append((n,events))
        self.assertTrue(before)
        n,expected=before[0];mutated=copy.deepcopy(part)
        for r in mutated[n:]:
            for key in ('open','high','low','close'):r['bar'][key]*=2
        self.assertEqual(hypotheses(mutated[:n]),expected)
        self.assertGreaterEqual(expected[0]['available_at'],part[n-1]['bar']['start']+300)

    def test_invalid_or_already_passed_target_is_not_forced_farther_away(self):
        event=dict(direction=1,anchor=101.8,target=101.,atr=.1,rolling_20bar_typical_price_vwap=102.)
        p,why=plan(event,102.)
        self.assertIsNone(p);self.assertEqual(why,'target_already_passed')
        event.update(target=102.01)
        p,why=plan(event,102.)
        self.assertIsNone(p);self.assertEqual(why,'target_has_no_modeled_net_room')

    def test_tight_structure_widens_only_to_atr_floor_then_respects_stop_cap(self):
        event=dict(direction=1,anchor=99.99,target=102.,atr=.1,rolling_20bar_typical_price_vwap=100.)
        p,why=plan(event,100.);self.assertIsNone(why);self.assertAlmostEqual(p['stop'],99.85)
        event['atr']=1.
        self.assertEqual(plan(event,100.)[1],'stop_cap')

    def test_stop_target_same_bar_and_gap_are_not_optimistic_target_fills(self):
        for d in (1,-1):
            p=dict(direction=d,entry=100.,stop=100.-d,target=100.+2*d,
                   unit_net_risk=-net_unit(d,100.,100.-d))
            both=[row(h=103,l=97)]
            result=trade(p,both,0)
            self.assertEqual(result['reason'],'ambiguous_stop_first');self.assertAlmostEqual(result['r'],-1.)
            price=100.-d*3
            gap=[row(o=price,h=price+1,l=price-1,c=price)]
            result=trade(p,gap,0)
            self.assertEqual(result['exit'],price);self.assertLess(result['r'],-1.)

    def test_incomplete_horizon_is_censored_and_only_full_horizon_has_time_exit(self):
        p=dict(direction=1,entry=100.,stop=98.,target=105.,unit_net_risk=2.2)
        rows=[row(i*300,h=101,l=99,c=100) for i in range(24)]
        self.assertIsNone(trade(p,rows[:23],0)['r'])
        self.assertEqual(trade(p,rows,0)['reason'],'time_exit')

    def test_stress_charges_both_sides_and_slippage_and_funding(self):
        for d in (1,-1):
            self.assertLess(net_unit(d,100,100+d,2),net_unit(d,100,100+d))
            self.assertLess(net_unit(d,100,100),0)

    def test_missing_forward_and_risk_telemetry_cannot_pass_with_an_impressive_paper_win_rate(self):
        metrics=dict(closed=1000,profit_factor=8.,first_target_wilson_lower=.9,
                     stress_2x_expectancy_r=2.,max_drawdown_r=1.)
        result=promotion_gate(metrics)
        self.assertFalse(result['eligible'])
        for name in ('not_untouched_forward','insufficient_forward_days','net_expectancy_not_established',
                     'early_urgent_risk_not_validated','execution_not_validated'):
            self.assertIn(name,result['failures'])

    def test_gate_accepts_complete_declared_evidence_but_blocks_bad_frequency(self):
        m=dict(untouched_forward=True,forward_days=30,closed=100,net_mean_lower_95=.01,
               profit_factor=1.3,first_target_wilson_lower=.51,urgent_5m_fraction=.08,
               signals_per_day=2.,active_day_fraction=.7,stress_2x_expectancy_r=.01,
               max_drawdown_r=5.,quote_latency_validated=True)
        self.assertTrue(promotion_gate(m)['eligible'])
        m['signals_per_day']=0
        self.assertFalse(promotion_gate(m)['eligible'])

    def test_historical_data_is_development_only_and_one_alert_gap_remains_unknown(self):
        result=run(DATA)
        self.assertFalse(result['untouched_out_of_sample']);self.assertFalse(result['production_monitor_changed'])
        self.assertFalse(any(g['eligible'] for g in result['promotion'].values()))
        audit=result['actual_alert_audit']
        self.assertEqual((audit['entries'],audit['risk_followups'],audit['time_followups']),(5,4,1))
        self.assertEqual(audit['target_before_followup_confirmed_absent'],4)
        self.assertEqual(audit['unknown'],1)

    def test_execution_waits_for_first_full_bar_after_publication_not_unavailable_prior_open(self):
        data=dict(bars=[row(i*300) for i in range(30)],entries=[],followups=[])
        import tempfile
        # Synthetic detector emits once; the bar opened at 6600 is BEFORE the
        # actual signal publication 6601, so the valid scheduled open is 6900.
        def detector(prefix):
            if len(prefix)!=22:return []
            return [dict(strategy=NAMES[0],direction=1,anchor=99.7,target=103.,atr=.1,
                         rolling_20bar_typical_price_vwap=100.,available_at=6601.,bar_start=6300)]
        with tempfile.TemporaryDirectory() as td:
            path=Path(td)/'data.json';path.write_text(json.dumps(data))
            with patch('research.lbank_strategy_rebuild.hypotheses',side_effect=detector):result=run(path)
        trade_row=result['trades'][NAMES[0]][0]
        self.assertEqual(trade_row['entry_at'],6900)


if __name__=='__main__':unittest.main()
