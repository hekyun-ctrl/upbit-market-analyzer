import unittest
import json
from decimal import Decimal
import numpy as np
from evaluate_pinbar_transfer import signals,follow,replay,cash

def bars(n=32):
    return np.array([[i*60,100,100.1,99.9,100,100] for i in range(n)],dtype=float)

class PinbarTests(unittest.TestCase):
    def test_strict_original_threshold_and_zero_range(self):
        a=np.array([[0,840,1000,0,850,36],[60,840,1000,0,851,36],
                    [120,100,100,100,100,40],[180,100,100.4,100,100.39,35]],float)
        self.assertEqual(signals(a).tolist(),[0,1,0,0])

    def test_next_open_not_signal_close(self):
        a=bars();a[0,2:5]=[100.4,99.9,100.39];a[1:,1:5]=[101,101.1,100.9,101]
        rows,_=replay(a,np.ones(len(a),bool),np.empty((0,2)))
        self.assertEqual(rows[0]['entry_at'],60);self.assertEqual(rows[0]['entry_reference'],101)
        self.assertEqual(rows[0]['exit_at'],1560)

    def test_first_entry_bar_loss_retained(self):
        a=bars();a[1,3]=99
        r=follow(a,np.ones(len(a),bool),1,1,[])
        self.assertEqual(r['reason'],'stop');self.assertLess(cash(r,1)['net_R'],-1)

    def test_stop_first_when_both_touched(self):
        a=bars();a[1,2:4]=[102,98]
        r=follow(a,np.ones(len(a),bool),1,1,[])
        self.assertTrue(r['same_bar_both_touched']);self.assertEqual(r['reason'],'stop')

    def test_gap_unknown_never_fictional_close(self):
        a=bars();v=np.ones(len(a),bool);v[4]=False
        r=follow(a,v,1,1,[])
        self.assertEqual(r['status'],'unknown_gap');self.assertNotIn('exit_reference',r)

    def test_end_unknown_not_closed(self):
        r=follow(bars(4),np.ones(4,bool),1,-1,[])
        self.assertEqual(r['status'],'unknown_end')

    def test_open_gap_uses_worse_open(self):
        a=bars();a[2,1:5]=[99,99.1,98.9,99]
        r=follow(a,np.ones(len(a),bool),1,1,[])
        self.assertEqual(r['reason'],'stop_open_gap');self.assertEqual(r['exit_reference'],99)

    def test_funding_entry_boundary_and_credits_not_income(self):
        a=bars();r=follow(a,np.ones(len(a),bool),1,1,[(60,.1),(120,.001),(180,-.1)])
        self.assertAlmostEqual(r['funding_debit_unit'],.1)
        self.assertEqual(len(r['funding_events']),2)

    def test_decimal_cash_and_double_fixed_path(self):
        row=dict(side=1,entry_reference=100.,exit_reference=100.7,funding_debit_unit=.01)
        value=cash(row,1)
        expected=(Decimal('100.7')*Decimal('.9998')-Decimal('100')*Decimal('1.0002')-
            Decimal('.0006')*(Decimal('100.7')*Decimal('.9998')+Decimal('100')*Decimal('1.0002'))-Decimal('.01'))/Decimal('.35')
        self.assertAlmostEqual(value['net_R'],float(expected),places=11)
        self.assertLess(cash(row,2)['net_R'],value['net_R'])
        self.assertEqual(cash(row,2)['gross_R'],value['gross_R'])

    def test_future_append_preserves_closed_prefix(self):
        a=bars(90);a[0,2:5]=[100.4,99.9,100.39];a[1,2]=101
        short,_=replay(a[:45],np.ones(45,bool),[])
        full,_=replay(a,np.ones(90,bool),[])
        self.assertEqual(short,full)

    def test_full_replay_ledger_serializes_native_indices(self):
        a=bars();a[0,2:5]=[100.4,99.9,100.39]
        rows,_=replay(a,np.ones(len(a),bool),[])
        self.assertTrue(rows)
        self.assertEqual(json.loads(json.dumps(rows)),rows)

    def test_short_stop_direction(self):
        a=bars();a[1,2]=101
        r=follow(a,np.ones(len(a),bool),1,-1,[])
        self.assertEqual(r['reason'],'stop');self.assertGreater(r['exit_reference'],100)

if __name__=='__main__':unittest.main()
