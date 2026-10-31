import ast,hashlib,json,sqlite3,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import lbank_performance_audit as a
from lbank_performance import PerformanceBook,leg_pnl
from research.compare_entry_routes import compare
import lbank_futures_monitor as monitor


def plan(d=1):
 return dict(price=100.,stop=98. if d==1 else 102.,target1=103. if d==1 else 97.,target2=105. if d==1 else 95.,quantity_btc=1.,net_loss=2.2,direction=d,strategy='breakout')

def bar(start,o=100,h=101,l=99,c=100,period=60):
 return dict(start=start,open=o,high=h,low=l,close=c,volume=1.,quality='stream_closed')

class AuditTests(unittest.TestCase):
 def test_frozen_plan_quote_extrema_and_restart(self):
  with tempfile.TemporaryDirectory() as d:
   p=str(Path(d)/'x.db');b=PerformanceBook(p,1000);ident=b.add('x',plan(),1000,fee=0,slippage=0,funding=0)
   b.observe(1060,101);b.db.close();b=PerformanceBook(p,1000);b.add('x',dict(plan(),price=100.1),1000,fee=0,slippage=0,funding=0)
   row=b.db.execute('select * from trade_evidence where id=?',(ident,)).fetchone()
   self.assertEqual(json.loads(row['frozen_plan'])['price'],100);self.assertEqual(row['mfe'],1);self.assertEqual(row['quote_count'],1)
 def test_cost_leg_matches_existing_accounting(self):
  b=PerformanceBook(':memory:',1000);ident=b.add('x',plan(),1000);b.observe(1060,105)
  legs=b.db.execute("select * from model_legs where id=? and policy='levels' order by ts,kind",(ident,)).fetchall()
  self.assertEqual(len(legs),2);self.assertAlmostEqual(sum(json.loads(x['detail'])['modeled_net_usdt'] for x in legs),b.summary()['levels']['net_usdt_model'])
 def test_warning_applied_equal_time_and_backdated_ignored(self):
  b=PerformanceBook(':memory:',1000);ident=b.add('x',plan(),1000,fee=0,slippage=0,funding=0);b.observe(1060,99.5)
  b.early_warning('x',1000,1060,99.5);self.assertEqual(b.summary()['warning_exit']['net_usdt_model'],-.5)
  b.early_warning('x',1000,1050,99);results=[x['result'] for x in b.db.execute('select * from warning_applications where id=?',(ident,))]
  self.assertIn('applied',results)
 def test_same_bar_stop_target_is_censored(self):
  b=PerformanceBook(':memory:',1000);b.add('x',plan(),1000)
  state={'minute_bars':[bar(1020,100,104,97,101)]}
  out=b.observe(1080,101,state);self.assertTrue(out);self.assertEqual(out[0]['status'],'censored_intrabar_order');self.assertIsNone(out[0]['net_usdt_model'])
 def test_pre_entry_and_forming_bars_do_not_invent_touch(self):
  state={'minute_bars':[bar(900,100,110,90,100),dict(bar(1020,100,110,90,100),quality='forming')]}
  self.assertIsNone(a.intrabar_uncertainty(state,1,98,103,1000,1080))
 def test_pair_counts_exclude_censored(self):
  b=PerformanceBook(':memory:',1000);b.add('x',dict(plan(),runner_enabled=True),1000);b.observe(1180,101)
  r=a.paired_comparison(b.db);self.assertGreater(r['excluded'],0);self.assertEqual(r['difference_usdt'],0)
 def test_stage_events_same_timestamp_reconcile(self):
  b=PerformanceBook(':memory:',1000);b.record_evaluation(1000,'classification','market_no_setup',{});b.record_evaluation(1000,'dispatch_gate','cooldown',{})
  r=a.evaluation_summary(b.db,900,1100);self.assertEqual(r['checks'],2);self.assertTrue(r['reconciled'])
 def test_old_schema_read_does_not_add_tables_or_history(self):
  db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row;db.execute('create table signals(id,policy,opened,last_at,last_price,plan,stage,partial,status,closed,net)')
  db.execute('insert into signals values(?,?,?,?,?,?,?,?,?,?,?)',('x','levels',1,1,100,json.dumps(plan()),0,0,'open',None,None))
  before=list(db.execute("select name from sqlite_master where type='table'"));rows=a.trade_rows(db);after=list(db.execute("select name from sqlite_master where type='table'"))
  self.assertEqual(before,after);self.assertFalse(rows[0]['frozen_evidence_available']);self.assertIsNone(rows[0]['quote_count'])
 def test_closing_day_separates_carry_in(self):
  b=PerformanceBook(':memory:',1000);day='2026-10-30';start=1761750000.;b.add('x',plan(),start-100);b.observe(start+10,105)
  r=a.closing_day_summary(b.db,day);self.assertFalse(r['mark_to_market_account_day'])
 def test_split_report_preserves_every_character(self):
  msg=('가'*120+'\n')*100;chunks=a.split_report(msg,300);self.assertTrue(all(len(x)<=300 for x in chunks));self.assertEqual(''.join(chunks),msg)
 def test_independent_breakout_and_retest_routes(self):
  five=[bar(i*300,100,101,99,100,300) for i in range(20)]+[bar(6000,100,103,100,102,300)]
  rows=[{'ts':6300,'price':102,'five_bars':five,'minute_bars':[bar(6240,101,102,100,102)]},
        {'ts':6360,'price':101.5,'five_bars':five,'minute_bars':[bar(6300,101,102,101,101.5)]},
        {'ts':6420,'price':104,'five_bars':five,'minute_bars':[bar(6360,101,104,101,104)]}]
  def factory(r,d,level,frozen=None):return frozen or {'stop':99,'target1':103,'quantity_btc':1}
  def pnl(d,e,x,**kw):return d*(x-e)
  out=compare(rows,factory,pnl);long=[x for x in out if x['direction']==1][0]
  self.assertEqual(long['route_status'],'both');self.assertEqual(long['lead_seconds'],60);self.assertIsNotNone(long['net_difference_retest_minus_breakout'])
 def test_target_policy_not_activated_in_runtime(self):
  src=Path('lbank_futures_monitor.py').read_text();tree=ast.parse(src)
  calls=[n for n in ast.walk(tree) if isinstance(n,ast.Call) and getattr(n.func,'id',None)=='calculate_plan']
  runtime=[n for n in calls if any(k.arg=='target_policy' for k in n.keywords)]
  self.assertEqual(runtime,[])
 def test_offline_structural_policy_integrates_without_live_activation(self):
  class Policy:
   pnl=staticmethod(monitor.net_pnl_per_btc)
   def __call__(self,d,e,s,setup,state):
    return {'policy':'structural_target_inverse_entry_p1_v1','status':'qualified',
            'stop':s,'target1':e+d,'target2':e+d,'barrier':{'price':e+2*d}}
   def quantity(self,q,price):return q
  prices=[95+5*i/50 for i in range(51)]+[100.]*9;diag={}
  result=monitor.calculate_plan(prices,1,{'strategy':'breakout'},diagnostics=diag,target_policy=Policy())
  self.assertIsNotNone(result,diag);self.assertEqual(result['target1'],result['target2'])
  self.assertEqual(result['structural_target_plan']['policy'],'structural_target_inverse_entry_p1_v1')

if __name__=='__main__':unittest.main()
