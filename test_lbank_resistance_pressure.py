import copy,unittest
from lbank_resistance_pressure import resistance_pressure,PressureObserver
from test_lbank_signal_thesis import fixture
class PressureTests(unittest.TestCase):
 def test_invalid_never_grants_permission(self):
  for s in (None,{}, {'quote':float('nan')}):
   x=resistance_pressure(s,1,9000);self.assertFalse(x['ready']);self.assertFalse(x['entry_override'])
 def test_completed_fixture_immutable_and_no_permission(self):
  s,p=fixture(1);s['quote']=p['price'];old=copy.deepcopy(s)
  x=resistance_pressure(s,1,9000);self.assertTrue(x['ready']);self.assertIsNone(x['strength_comparison']);self.assertEqual(s,old)
  self.assertFalse(x['live_gate']);self.assertFalse(x['entry_override'])
 def test_stale_and_unverified_quality_rejected(self):
  s,p=fixture(1);s['quote']=p['price']
  self.assertFalse(resistance_pressure(s,1,9600)['ready'])
  s['five_bars'][-1]['quality']='unknown'
  self.assertFalse(resistance_pressure(s,1,9000)['ready'])
 def test_dedup_per_closed_bucket(self):
  s,p=fixture(1);s['quote']=p['price'];o=PressureObserver()
  self.assertEqual(len(o.step(s,9000)),2);s['quote']+=1;self.assertEqual(o.step(s,9015),[])
 def test_direction_symmetry_features(self):
  s,p=fixture(1);s['quote']=p['price']
  a=resistance_pressure(s,1,9000);b=resistance_pressure(s,-1,9000)
  self.assertAlmostEqual(a['signed_three_bar_progress_atr'],-b['signed_three_bar_progress_atr'])
  self.assertEqual(a['three_bar_volume_to_prior20_median'],b['three_bar_volume_to_prior20_median'])
if __name__=='__main__':unittest.main()

