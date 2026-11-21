import unittest,tempfile,json,os
from dataclasses import replace
from lbank_zb.core import *
from lbank_zb.engine import Engine
from lbank_zb.store import Store,snapshot,restore

C=Costs(D('.0001'),D('.0001'),D(0),D('.01'),D('.00001'),D(1),D(0),'ASSUMED','fixture')
def bar(t=0,p=60,o='100',h='101',l='99',c='100',v='1',received=None):return Bar(t,p,D(o),D(h),D(l),D(c),D(v),received if received is not None else t+p,source='synthetic_fixture')
def candidate(side=1):return Candidate('TREND_PULLBACK',side,600,300,D(100),D(2),D(97) if side==1 else D(103),D(96) if side==1 else D(104),D(97) if side==1 else D(103))
def position(side=1):
 p,why=plan(candidate(side),D(100),C,D(1000));assert not why,why
 return Position.open(p,660)

class Contract(unittest.TestCase):
 def test_product_and_closed(self):
  for k,v in [('exchange','binance'),('market_type','spot'),('settlement_asset','BTC'),('price_basis','MARK')]:
   with self.assertRaises(ValueError):replace(bar(),**{k:v}).validate(60)
  with self.assertRaises(ValueError):replace(bar(),closed=False).validate(60)
  with self.assertRaises(ValueError):bar(received=61).validate(60)
 def test_missing_not_filled(self):
  with self.assertRaises(ValueError):aggregate([bar(0),bar(120)],300)
 def test_ema_rsi_and_pivot_known(self):
  rows=[bar(i*300,300) for i in range(150)]
  f=features(rows);self.assertEqual(f['ema20'][-1],D(100));self.assertEqual(f['rsi'][-1],D(50));self.assertEqual(f['er'][-1],0)
  rows[145]=bar(145*300,300,h='105')
  self.assertFalse(any(p['index']==145 for p in pivots(rows,146*300)))
  self.assertTrue(any(p['index']==145 for p in pivots(rows,148*300)))
 def test_freshness_arrival_deadline(self):
  rows=[bar(0,300)]
  self.assertTrue(bar_fresh(rows,300,540));self.assertTrue(bar_fresh(rows,300,615));self.assertFalse(bar_fresh(rows,300,616))
 def test_unknown_costs(self):
  p,reasons=plan(candidate(),D(100),replace(C,fee_entry=None),D(1000));self.assertIsNone(p);self.assertIn('COST_OR_PRODUCT_UNKNOWN',reasons)
 def test_quantity_rounding_risk(self):
  for d in (1,-1):
   p,why=plan(candidate(d),D(100),C,D(1000));self.assertFalse(why);self.assertLessEqual(p.risk,D('2.5'));self.assertEqual(p.quantity%C.step,0)
 def test_funding_known_at(self):
  hist=[{'time':50,'known_at':200,'rate':'.5'},{'time':30,'known_at':40,'rate':'.001'}]
  self.assertEqual(planning_funding(100,[200],D('.0001'),hist,D(100)),D('.100'))
  self.assertIsNone(planning_funding(100,None,D(0),[],D(100)))
 def test_conflict_and_overlap(self):
  long,_=plan(candidate(),D(100),C,D(1000));short,_=plan(candidate(-1),D(100),C,D(1000))
  winner,reasons=select([long,short]);self.assertIsNone(winner);self.assertEqual(set(reasons.values()),{'CONFLICT'})
  p=replace(long,candidate=replace(long.candidate,strategy='FAILED_BREAK_RECLAIM'))
  winner,reasons=select([p,long]);self.assertEqual(winner.id,long.id);self.assertEqual(len(reasons),1)
 def test_stop_wins_and_stays(self):
  p=position();p.minute(bar(660,h='110',l='90'),720)
  self.assertEqual(p.state,'PAPER_CLOSED');self.assertIn('AMBIGUOUS_INTRABAR',p.health);loss=p.cash
  p.minute(bar(720,h='120',l='100'),780);self.assertEqual(loss,p.cash)
 def test_gap_stop_price(self):
  p=position();p.minute(bar(660,o='90',h='92',l='89',c='91'),720)
  self.assertEqual(p.events[-1]['price'],'90');self.assertLess(p.cash,-p.plan.risk)
 def test_structure_not_short_reversal(self):
  p=position();p.five(bar(900,300,o='99',h='100',l='98',c='99'),D(2),'TRANSITION',1200)
  self.assertEqual(p.state,'PAPER_OPEN');self.assertTrue(p.warned)
  p.five(bar(1200,300,o='98',h='99',l='96',c='96.9'),D(2),'RANGE',1500);self.assertEqual(p.state,'PAPER_CLOSED')
 def test_partial_only_and_new_stop_not_retroactive(self):
  p=position();p.minute(bar(660,h='107',l='99',c='106'),720);self.assertEqual(p.remaining,p.plan.quantity-rounding(p.plan.quantity*D('.25'),p.plan.costs.step));self.assertEqual(p.active_stop,p.plan.stop)
  p.five(bar(900,300,o='106',h='109',l='105',c='108'),D(2),'TREND_UP',1200)
  self.assertEqual(p.stop_since,1200);self.assertEqual(p.state,'PAPER_PARTIAL')
  p.minute(bar(1140,o='108',h='109',l='100',c='108'),1201);self.assertEqual(p.state,'PAPER_PARTIAL');self.assertIn('AMBIGUOUS_INTRABAR',p.health)
 def test_funding_partial_sign_independent(self):
  for d in (1,-1):
   p=position(d);q=p.remaining;p.funding_event(700,D('.001'),D(100));self.assertEqual(p.funding,D(d)*q*D('.1'))
   p.exit(D(100),q/4,710,'PARTIAL_TP1');p.funding_event(720,D('-.002'),D(100));self.assertEqual(p.funding,D(d)*q*(D('.1')-D('.15')))
 def test_time_review_no_forced_exit(self):
  p=position();p.minute(bar(660+6*3600),660+6*3600+60);self.assertEqual(p.state,'PAPER_OPEN');self.assertTrue(any(x['kind']=='TIME_REVIEW' for x in p.events))
 def test_intraminute_and_gap_censor(self):
  p=position();p.fill_time=670;p.minute(bar(660,h='110',l='90'),720);self.assertEqual(p.state,'PAPER_OPEN');self.assertIn('AMBIGUOUS_INTRABAR',p.health)
  p=position();p.minute(bar(660),720);p.minute(bar(780),840);self.assertIn('DATA_CENSORED',p.health)
 def test_no_preentry_extremes_for_runner(self):
  p=position();p.partial=True;p.five(bar(600,300,h='120',l='90'),D(2),'TREND_UP',900);self.assertIsNone(p.high)
 def test_restart_open_position_and_event_hash(self):
  e=Engine(C);e.position=position();e.pending=[candidate()];s=snapshot(e);r=restore(s);self.assertEqual(s,snapshot(r));self.assertEqual(r.position.plan.entry,D(100))
 def test_store_idempotence_revision_restart(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=tmp+'/state.sqlite';s=Store(path,Engine(C));b=bar();self.assertTrue(s.ingest(b,60));before=snapshot(s.engine);self.assertFalse(s.ingest(b,60));self.assertEqual(before,snapshot(s.engine));s.close()
   s=Store(path,Engine(C));self.assertEqual(before,snapshot(s.engine))
   with self.assertRaises(ValueError):s.ingest(replace(b,c=D('100.5')),60)
   self.assertEqual(s.db.execute('SELECT count(*) FROM corrections').fetchone()[0],1);s.close()
 def test_transaction_rolls_back(self):
  s=Store(':memory:',Engine(C));s.db.execute("CREATE TRIGGER fail BEFORE INSERT ON inputs BEGIN SELECT RAISE(ABORT,'fixture_failure'); END")
  before=snapshot(s.engine)
  with self.assertRaises(Exception):s.ingest(bar(),60)
  self.assertEqual(before,snapshot(s.engine));self.assertEqual(s.db.execute('SELECT count(*) FROM inputs').fetchone()[0],0);s.close()
 def test_expired_outbox_does_not_revive_emergency(self):
  s=Store(':memory:',Engine(C))
  with s.db:
   s.db.execute("INSERT INTO outbox(key,kind,payload,expires,status) VALUES('a','READY','{}',120,'FAILED')")
   s.db.execute("INSERT INTO outbox(key,kind,payload,expires,status) VALUES('b','HARD_STOP','{}',NULL,'FAILED')")
  self.assertEqual([x[0] for x in s.pending(121)],['b']);s.delivery_result('b',None,'response_lost');self.assertEqual([x[0] for x in s.pending(122)],['b']);s.close()
 def test_live_quote_fail_closed(self):
  e=Engine(C);self.assertFalse(e.quote_ready(None,600,candidate()));self.assertFalse(e.quote_ready({'product':'lbank:linear_usdt_perpetual:BTCUSDT:LAST','quantity_btc':'1','depth_verified':True,'received':590,'fill_price':'100'},600,candidate()))
 def test_no_net_pass_from_unknown_cost(self):
  e=Engine(C);self.assertIsNone(e.report()['verified_net']);self.assertIsNone(e.report()['PF_model']);self.assertEqual(e.report()['classification'],'EVIDENCE_INSUFFICIENT')
 def test_mirror_strategies_and_prefix(self):
  # Explicit lower-band reclaim, symmetry verified without changing thresholds.
  rows=[bar(i*300,300,o='100',h='102',l='98',c='100') for i in range(150)]
  rows[-1]=bar(149*300,300,o='98',h='101',l='97',c='100.8')
  a,_=detect(rows,'RANGE',{});long=[x for x in a if x.side==1 and x.strategy=='FAILED_BREAK_RECLAIM'];self.assertEqual(len(long),1)
  reflected=[Bar(b.start,b.period,200-b.o,200-b.l,200-b.h,200-b.c,b.v,b.received) for b in rows]
  b,_=detect(reflected,'RANGE',{});short=[x for x in b if x.side==-1 and x.strategy=='FAILED_BREAK_RECLAIM'];self.assertEqual(len(short),1);self.assertEqual(long[0].S,200-short[0].S)
  future=rows+[bar(150*300,300,h='500',c='499')];self.assertEqual(dumps(detect(rows,'RANGE',{})[0]),dumps(detect(future[:150],'RANGE',{})[0]))
if __name__=='__main__':unittest.main()

class StrategiesAndReplay(unittest.TestCase):
 def test_A_signal_and_original_ATR(self):
  rows=[bar(i*300,300,o='100',h='102',l='98',c='100') for i in range(150)]
  rows[-3]=bar(147*300,300,o='100',h='104',l='100',c='103')
  rows[-2]=bar(148*300,300,o='101',h='102',l='99',c='100')
  rows[-1]=bar(149*300,300,o='100',h='102',l='99.8',c='101.8')
  found,_=detect(rows,'TREND_UP',{});a=[c for c in found if c.strategy=='TREND_PULLBACK' and c.side==1];self.assertEqual(len(a),1);self.assertEqual(a[0].atr,features(rows)['atr'][-2]);self.assertEqual(a[0].F,D('99'))
 def test_B_fixed_breakout_range_and_three_bar_expiry(self):
  rows=[bar(i*300,300,o='100',h='102',l='98',c='100') for i in range(150)]
  rows[-1]=bar(149*300,300,o='102',h='105',l='101',c='104.5');w={};detect(rows,'TRANSITION',w);self.assertEqual(dec(w['1']['U']),D(102))
  follow=rows+[bar(150*300,300,o='103',h='104',l='102',c='103.8')];out,_=detect(follow,'TRANSITION',w);b=[x for x in out if x.strategy=='BREAKOUT_RETEST' and x.side==1];self.assertEqual(len(b),1);self.assertEqual(b[0].F,D(102));self.assertEqual(b[0].atr,features(rows)['atr'][-2])
  w={'1':{'U':'102','D':'98','A':'4','start':149*300,'side':1}};later=rows+[bar(i*300,300,o='104',h='105',l='104',c='104') for i in range(150,154)];_,ev=detect(later,'TRANSITION',w);self.assertTrue(any(x['type']=='EXPIRED' for x in ev))
 def test_replay_restart_equivalence_and_unknown_cost(self):
  from lbank_zb.replay import replay
  with tempfile.TemporaryDirectory() as tmp:
   path=tmp+'/bars.jsonl';rows=[bar(i*60) for i in range(10)];Path(path).write_text('\n'.join(dumps(x) for x in rows))
   a=replay(path,C);b=replay(path,C,db=tmp+'/state.sqlite');c=replay(path,C,db=tmp+'/state.sqlite');self.assertEqual(a['event_hash'],b['event_hash']);self.assertEqual(b['event_hash'],c['event_hash']);self.assertIsNone(c['verified_net']);self.assertEqual(a['closed'],0)
 def test_risk_halt_does_not_stop_existing_stop(self):
  e=Engine(C);e.position=position();e.review=True;e.day_start=D(2000)
  e.ingest(bar(660,l='90',h='101'),720);self.assertEqual(len(e.closed),1);self.assertEqual(e.closed[0].events[-1]['kind'],'HARD_STOP')

class IndependentCash(unittest.TestCase):
 def test_long_and_short_partial_cash_exact(self):
  costs=replace(C,fee_entry=D('.001'),fee_exit=D('.001'))
  for side,target,stop,expected in [(1,'115','110','10.88875'),(-1,'85','90','11.21125')]:
   c=candidate(side);p=Plan(c,D(100),D(90) if side==1 else D(110),D(target),D(target),D(1),D(10),D('1.2'),D(0),costs,D(1000));pos=Position.open(p,660)
   pos.exit(D(target),D('.25'),700,'PARTIAL_TP1');pos.funding_event(710,D('.002'),D(100));pos.exit(D(stop),D('.75'),720,'HARD_STOP')
   self.assertEqual(pos.cash,D(expected));self.assertEqual(pos.remaining,0)
 def test_quantity_depth_and_unconfirmed_units(self):
  from lbank_zb.adapter import depth_fill
  b={'asks':[{'price':'101','volume':'1'},{'price':'102','volume':'1'}],'bids':[{'price':'100','volume':'1'}]}
  self.assertEqual(depth_fill(b,1,D('1.5'),D(1),'BTC'),D('152')/D('1.5'))
  with self.assertRaises(ValueError):depth_fill(b,1,D(1),None,'contracts')
 def test_paired_blocks_no_trade_days(self):
  from lbank_zb.evaluation import block_interval,paired_daily_difference
  a=block_interval({1:'0',2:'.01',3:'-.01',4:'0'});self.assertEqual(a,block_interval({1:'0',2:'.01',3:'-.01',4:'0'}));self.assertEqual(a['iterations'],2000)
  with self.assertRaises(ValueError):paired_daily_difference({1:'0'},{2:'0'})

class PreEntry(unittest.TestCase):
 def test_stop_before_scheduled_fill_cannot_revive(self):
  e=Engine(C,120);e.pending=[candidate()];e.ingest(bar(600,l='95',c='100'),660);self.assertEqual(e.pending,[])
  e.ingest(bar(720),780);self.assertIsNone(e.position);self.assertEqual(e.counts['CANCELLED'],1)
