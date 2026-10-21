import unittest,tempfile,sqlite3
from pathlib import Path
from lbank_zb.runtime import inspect_native,status,RuntimeThread
class RuntimeTests(unittest.TestCase):
 def test_missing_and_stale_feed_never_ready(self):
  s=status(inspect_native('/nonexistent/zbruntime',100));self.assertFalse(s['entry_publication_enabled']);self.assertIn('NATIVE_FEED_NOT_CURRENT',s['blocked_reasons'])
 def test_native_readonly_and_no_qualification_bypass(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=tmp+'/native';db=sqlite3.connect(p);db.execute('CREATE TABLE native_bars(period,start,quality)')
   db.executemany('INSERT INTO native_bars VALUES(?,?,?)',[(60,x,'stream_closed') for x in range(0,3600,60)]);db.commit();db.close()
   a=inspect_native(p,3605);self.assertTrue(a['connected']);self.assertEqual(a['consecutive_1m'],60);self.assertFalse(status(a)['entry_publication_enabled']);self.assertFalse(inspect_native(p,4000)['connected'])
 def test_notice_once_and_ack_not_trade(self):
  db=sqlite3.connect(':memory:');db.executescript('CREATE TABLE observations(time,payload); CREATE TABLE notices(signature TEXT PRIMARY KEY,status);');sent=[];w=RuntimeThread(sender=lambda m:sent.append(m) or True,native_path='/nonexistent/zbruntime')
  w.poll(db,100);w.poll(db,160);self.assertEqual(len(sent),1);self.assertIn('진입 신호가 아닙니다',sent[0]);self.assertEqual(db.execute('SELECT status FROM notices').fetchone()[0],'API_ACK');db.close()
 def test_failure_is_preserved_without_duplicate_spam(self):
  db=sqlite3.connect(':memory:');db.executescript('CREATE TABLE observations(time,payload); CREATE TABLE notices(signature TEXT PRIMARY KEY,status);');sent=[];w=RuntimeThread(sender=lambda m:sent.append(m) or False,native_path='/nonexistent/zbruntime')
  w.poll(db,100);w.poll(db,160);self.assertEqual(len(sent),1);self.assertEqual(db.execute('SELECT status FROM notices').fetchone()[0],'DELIVERY_UNKNOWN');db.close()
