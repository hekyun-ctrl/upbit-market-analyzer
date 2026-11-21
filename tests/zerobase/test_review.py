"""Acceptance regressions for the six independently reproduced review findings."""
import unittest,tempfile,json
from dataclasses import replace
from lbank_zb.core import *
from lbank_zb.engine import Engine
from lbank_zb.store import Store,snapshot,restore
from test_core import C,bar,candidate,position

class ReviewFixes(unittest.TestCase):
 def test_R1_rearrival_restart_and_product_validation(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=tmp+'/db';s=Store(path,Engine(C));b=bar();s.ingest(b,60);before=snapshot(s.engine)
   self.assertFalse(s.ingest(replace(b,received=61,source='reconnected'),61));self.assertEqual(before,snapshot(s.engine));s.close()
   s=Store(path,Engine(C));self.assertFalse(s.ingest(replace(b,received=62),62));self.assertEqual(s.db.execute('SELECT count(*) FROM corrections').fetchone()[0],0)
   payload=json.loads(s.db.execute('SELECT payload FROM inputs').fetchone()[0]);self.assertEqual(payload['received'],60)
   with self.assertRaises(ValueError):s.ingest(replace(b,received=63,market_type='spot'),63)
   with self.assertRaisesRegex(ValueError,'REVISION_REQUIRES_REPLAY_VERSION'):s.ingest(replace(b,c=D('100.5'),received=64),64)
   self.assertEqual(s.db.execute('SELECT count(*) FROM corrections').fetchone()[0],1);self.assertEqual(snapshot(s.engine),before);s.close()
 def test_R2_minute_gap_cannot_recover_on_one_good_five(self):
  e=Engine(C);t=449*300;e.five=[bar(i*300,300,v='5') for i in range(449)];missing=t-600
  e.one=[bar(x) for x in range(t-3600,t,60) if x!=missing];e.health.add('DATA_DEGRADED');e.position=position();e.position.fill_time=t-3600;e.position.stop_since=t-3600
  for x in range(t,t+300,60):e.ingest(bar(x),x+60)
  self.assertFalse(contiguous(e.one));e.ingest(bar(t,300,v='5'),t+300);self.assertIn('DATA_DEGRADED',e.health);self.assertIn('ONE_MINUTE_GAP',e.data_issues);self.assertIn('DATA_CENSORED',e.position.health)
  # Explicit new required contiguous histories, all old crosschecks still in window.
  t=(t+3600+899)//900*900;e.one=[bar(x) for x in range(t-3600,t,60)];e.five=[bar(x,300,v='5') for x in range(t-450*300,t,300)];e.fifteen=aggregate(e.five,900)
  e.recover_data_health(t,True);self.assertNotIn('DATA_DEGRADED',e.health);self.assertIn('DATA_CENSORED',e.position.health)
  e.crosscheck_failures.add(t-600);e.recover_data_health(t,True);self.assertIn('DATA_DEGRADED',e.health)
 def test_R3_step_and_minimum_all_small_lots(self):
  for side in (1,-1):
   for units in range(1,10):
    costs=replace(C,minimum_notional=D(0));orig=position(side).plan;p=replace(orig,quantity=D(units)*costs.step,costs=costs);pos=Position.open(p,660)
    targetbar=bar(660,h='107',l='99',c='106') if side==1 else bar(660,h='101',l='93',c='94')
    pos.minute(targetbar,720);self.assertEqual(pos.remaining%costs.step,0)
    if units<4:self.assertFalse(pos.partial);self.assertEqual(pos.remaining,p.quantity);self.assertEqual(pos.events[-1]['kind'],'PARTIAL_UNEXECUTABLE')
    else:
     sold=D(pos.events[-1]['quantity']);self.assertEqual(sold%costs.step,0);self.assertEqual(sold+pos.remaining,p.quantity);self.assertEqual(D(pos.events[-1]['actual_fraction']),sold/p.quantity)
  pos=position();pos.minute(bar(660,h='107',l='99',c='106'),720);self.assertEqual(pos.events[-1]['quantity'],'0.15548');self.assertEqual(pos.remaining,D('.46647'))
  p=replace(pos.plan,quantity=D('.02'),costs=replace(C,minimum_notional=D(1)));small=Position.open(p,660);small.minute(bar(660,h='107',l='99',c='106'),720);self.assertFalse(small.partial);self.assertEqual(small.remaining,p.quantity)
 def test_R4_BE_covers_exit_slip_fees_and_remaining_funding(self):
  for side in (1,-1):
   for slip in (0,2,5):
    for funding_before,funding_after in [(0,0),('.0001','.0002'),('-.0001','-.0002')]:
     costs=replace(C,slip_bps=D(slip));p,_=plan(candidate(side),D(100),costs,D(1000));pos=Position.open(p,660)
     pos.funding_event(670,D(funding_before),D(100));total_before=pos.funding
     first=bar(660,h='107',l='99',c='106') if side==1 else bar(660,h='101',l='93',c='94')
     pos.minute(first,720);self.assertTrue(pos.partial)
     self.assertEqual(pos.remaining_funding,total_before*pos.remaining/p.quantity)
     pos.funding_event(730,D(funding_after),D(100));allocated=pos.remaining_funding
     follow=bar(900,300,o='100.8',h='101',l='100.5',c='100.8') if side==1 else bar(900,300,o='99.2',h='99.5',l='99',c='99.2')
     pos.five(follow,D(2),'TREND_UP' if side==1 else 'TREND_DOWN',1200);self.assertEqual(pos.state,'PAPER_PARTIAL')
     exit_fill=costs.fill(pos.active_stop,-side);net=side*(exit_fill-p.entry)-p.entry*costs.fee_entry-exit_fill*costs.fee_exit-allocated/pos.remaining
     self.assertGreaterEqual(net,0);self.assertEqual(pos.active_stop%costs.tick,0)
 def test_R5_latch_survives_recovery_restart_until_new_day(self):
  e=Engine(C);e.day=0;e.day_start=D(1000);e.equity=D(991);e.position=position()
  e.ingest(bar(660,o='100',h='100',l='97.9',c='98'),720);self.assertTrue(e.daily_halted);first=e.daily_halt_at
  e.ingest(bar(720,o='98',h='107',l='98',c='106'),780);e.ingest(bar(780,h='101',l='95',c='96'),840)
  self.assertGreater(e.nav(),D(990));e=restore(snapshot(e));self.assertTrue(e.daily_halted);self.assertEqual(e.daily_halt_at,first)
  for t in range(840,1200,60):e.ingest(bar(t),t+60)
  e.pending=[replace(candidate(),signal_time=1200,origin=900)]
  with tempfile.TemporaryDirectory() as tmp:
   store=Store(tmp+'/day.sqlite',e);store.ingest(bar(1200),1260);store.close()
   store=Store(tmp+'/day.sqlite',Engine(C));self.assertTrue(store.engine.daily_halted)
   store.ingest(bar(1260),1320);e=store.engine;store.close()
  self.assertEqual(e.counts.get('PAPER_OPEN',0),0);self.assertEqual(e.counts['RISK_HALT'],1)
  e.review=True;e.day_update(54000,D(100),54005);self.assertFalse(e.daily_halted);self.assertTrue(e.review)
  # With independent persistent DD review absent, next day regains entry eligibility.
  e.review=False;e.health.clear();e.one=[bar(t) for t in range(50400,54000,60)];e.pending=[replace(candidate(),signal_time=54000,origin=53700)]
  e.ingest(bar(54000),54060);e.ingest(bar(54060),54120);self.assertIsNotNone(e.position)
 def test_R6_midnight_stop_before_boundary_and_delayed_arrival(self):
  for delay in (0,180):
   e=Engine(C);e.day=0;e.day_start=D(1000);p=position();p.fill_time=53400;p.stop_since=53400;e.position=p
   e.ingest(bar(53940,o='100',h='105',l='95',c='104',received=54000+delay),54000+delay)
   self.assertEqual(e.nav(),D('997.500009780'));self.assertEqual(D(e.daily['0']['end']),e.nav());self.assertEqual(e.day_start,e.nav());self.assertEqual(e.daily['0']['market_cutoff'],54000);self.assertEqual(e.daily['0']['received_at'],54000+delay)
   self.assertEqual(e.closed[0].events[-1]['time'],54000+delay)
 def test_R6_partial_open_boundary_and_final_observed_day(self):
  for partial in (False,True):
   e=Engine(C);e.day=0;e.day_start=D(1000);p=position();p.fill_time=53400;p.stop_since=53400;e.position=p
   b=bar(53940,o='100',h='107' if partial else '105',l='99',c='106' if partial else '104')
   e.ingest(b,54000);self.assertEqual(D(e.daily['0']['end']),e.nav());self.assertEqual(e.day_start,e.nav());self.assertEqual(e.position.partial,partial)
   e.ingest(bar(54000,o=b.c,h=b.c+1,l=b.c-1,c=b.c),54060);end=e.daily_at_cutoff()['1'];self.assertTrue(end['partial_observation_day']);self.assertEqual(D(end['end']),e.nav());self.assertEqual(D(end['start']),D(e.daily['0']['end']))
if __name__=='__main__':unittest.main()
