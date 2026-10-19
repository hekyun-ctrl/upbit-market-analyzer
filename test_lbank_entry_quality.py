import copy
import json
import unittest

from lbank_entry_quality import confirmation_quality, POLICY
from lbank_native_entry import SOURCE
from lbank_performance import PerformanceBook
from lbank_signal_risk import SignalRiskTracker


def future_confirmation_state(state, watch):
    """Synthetic future bars for boundary tests, never replay market data."""
    result = copy.deepcopy(state)
    d, p, start = watch['direction'], watch['price'], watch['native_watch_1m_start']
    for i in range(1,4):
        close = p + d*(35+25*(i-1))
        opening = close-d*5
        result['minute_bars'].append(dict(start=start+i*60,open=opening,
            high=max(opening,close)+2,low=min(opening,close)-2,close=close,
            quality='stream_closed',volume=1))
    result['prices'] = [b['close'] for b in result['minute_bars']]
    result['quote'] = p+d*85
    return result


class EntryQualityTests(unittest.TestCase):
    def fixture(self,d=1):
        p=80000.
        watch=dict(direction=d,price=p,native_watch_1m_start=600,
                   strategy='breakout',level=p-d*20,stop=p-d*200,
                   target1=p+d*600,target2=p+d*1000,quantity_btc=.01,net_loss=3)
        state=dict(source=SOURCE,quote=p,minute_bars=[dict(start=600,
            open=p,high=p+1,low=p-1,close=p,quality='stream_closed')])
        return future_confirmation_state(state,watch),watch

    def test_long_and_short_require_post_discovery_bars_without_mutation(self):
        for d in (1,-1):
            s,w=self.fixture(d);before=copy.deepcopy((s,w))
            ok,reason,evidence=confirmation_quality(s,w)
            self.assertTrue(ok,reason)
            self.assertEqual(evidence['confirmed_1m_starts'],[720,780])
            self.assertEqual((s,w),before)

    def test_existing_forming_minute_cannot_supply_first_confirmation(self):
        s,w=self.fixture();s['minute_bars']=s['minute_bars'][:-1]
        self.assertEqual(confirmation_quality(s,w)[1],'await_two_post_watch_closed_minutes')

    def test_regressing_or_flat_quote_is_not_confirmed_even_with_aligned_history(self):
        for d in (1,-1):
            for progress in (0,-25):
                s,w=self.fixture(d);s['quote']=w['price']+d*progress
                self.assertEqual(confirmation_quality(s,w)[1],'quote_not_progressing_from_watch')

    def test_closing_back_under_candidate_then_bouncing_does_not_pass(self):
        s,w=self.fixture();s['minute_bars'][-2].update(open=79980,low=79970,high=80001,close=79990)
        self.assertEqual(confirmation_quality(s,w)[1],'closed_minute_followthrough_missing')

    def test_stalling_or_reversing_new_close_does_not_pass(self):
        for d in (1,-1):
            s,w=self.fixture(d)
            b=s['minute_bars'][-1];b['open']=b['close']+d*2
            b['high']=max(b['high'],b['open']);b['low']=min(b['low'],b['open'])
            self.assertEqual(confirmation_quality(s,w)[1],'closed_minute_followthrough_missing')

    def test_original_breakout_level_must_hold_for_both_directions(self):
        for d in (1,-1):
            s,w=self.fixture(d);w['level']=w['price']+d*70
            self.assertEqual(confirmation_quality(s,w)[1],'breakout_level_not_held')

    def test_missing_frozen_level_cannot_reconstruct_a_more_convenient_one(self):
        s,w=self.fixture();del w['level']
        self.assertEqual(confirmation_quality(s,w)[1],'breakout_level_unavailable')

    def test_quote_must_still_hold_latest_closed_reaction(self):
        for d in (1,-1):
            s,w=self.fixture(d);s['quote']=w['price']+d*70
            self.assertEqual(confirmation_quality(s,w)[1],'quote_lost_closed_minute_reaction')

    def test_broken_candidate_stop_cannot_be_rescued_by_later_closes(self):
        for d in (1,-1):
            s,w=self.fixture(d)
            s['minute_bars'][-2]['low' if d==1 else 'high']=w['stop']-d
            self.assertEqual(confirmation_quality(s,w)[1],'watch_structure_stop_crossed')

    def test_gap_forming_corruption_and_missing_watch_anchor_fail_closed(self):
        for mutation in ('gap','forming','nan','missing'):
            s,w=self.fixture()
            if mutation=='gap':s['minute_bars'][-1]['start']+=60
            elif mutation=='forming':s['minute_bars'][-1]['quality']='forming'
            elif mutation=='nan':s['minute_bars'][-1]['close']=float('nan')
            else:del w['native_watch_1m_start']
            self.assertFalse(confirmation_quality(s,w)[0],mutation)

    def test_entry_evidence_is_frozen_in_persistent_accounting(self):
        s,w=self.fixture();w['entry_quality']=confirmation_quality(s,w)[2]
        book=PerformanceBook(':memory:',1000);book.add(1,w,1000)
        w['entry_quality']['confirmed_1m_closes'][0]=0
        saved=json.loads(book.db.execute('SELECT plan FROM signals LIMIT 1').fetchone()[0])
        self.assertEqual(saved['entry_quality']['policy'],POLICY)
        self.assertGreater(saved['entry_quality']['confirmed_1m_closes'][0],0)

    def test_qualified_entry_does_not_disable_early_risk_or_hard_stop(self):
        for d in (1,-1):
            s,w=self.fixture(d);w['entry_quality']=confirmation_quality(s,w)[2]
            tracker=SignalRiskTracker();tracker.add_sent_signal(w,1000)
            for t in (1015,1030):
                events=tracker.check([80000.]*5+[80000-d*150],t)
            self.assertEqual(events[0][1],'early_invalidation')
            events=tracker.check([80000-d*250],1045)
            self.assertEqual(events[0][1],'stop_breached')


if __name__=='__main__':unittest.main()
