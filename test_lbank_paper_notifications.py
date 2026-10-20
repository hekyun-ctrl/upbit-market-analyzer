import ast
import json
import hashlib
import sqlite3
import tempfile
import unittest
from pathlib import Path
from lbank_paper_notifications import PaperNotifications
from lbank_performance import PerformanceBook


class PaperNotificationsTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name);self.now=1800000000.;self.messages=[]
        self.path=str(self.root/'study.db');self.book=PerformanceBook(self.path,1000)
        self.addCleanup(self.book.db.close)
        self.book.db.execute('CREATE TABLE technique_candidates(id TEXT PRIMARY KEY,first_at REAL,price REAL,status TEXT,detail TEXT)')
        self.book.db.commit()
        self.notifier=PaperNotifications(str(self.root/'notify.db'),{'techniques':self.path},lambda msg:self.messages.append(msg) or True,self.now)
        self.addCleanup(self.notifier.db.close)

    def add_candidate(self,at=None):
        with self.book.db:
            self.book.db.execute('INSERT INTO technique_candidates VALUES(?,?,?,?,?)',('event-1',at or self.now,100.,'watch',json.dumps({'event':{'strategy':'box_retest_5m','direction':1}})))

    def test_candidate_is_not_a_trade_and_deduplicates_across_restarts(self):
        self.add_candidate();self.book.sample(self.now,100)
        before=Path(self.path).read_bytes()
        self.notifier.scan(self.now);self.assertTrue(self.notifier.deliver(self.now))
        self.assertIn('아직 모의 진입 아님',self.messages[0]);self.assertIn('실전 진입 금지',self.messages[0])
        other=PaperNotifications(str(self.root/'notify.db'),{'techniques':self.path},self.messages.append,self.now+60)
        self.addCleanup(other.db.close);other.scan(self.now+60);other.deliver(self.now+60)
        self.assertEqual(len(self.messages),1)
        self.assertEqual(before,Path(self.path).read_bytes())

    def test_open_close_and_tp1_are_hypothetical(self):
        p=dict(price=100.,stop=98.,target1=103.,target2=105.,quantity_btc=1.,net_loss=2.2,direction=1,strategy='box_retest_5m')
        self.book.add('event1',p,self.now)
        self.notifier.scan(self.now);self.notifier.deliver(self.now)
        self.assertIn('모의 진입',self.messages[-1])
        self.book.observe(self.now+60,103.);self.notifier.scan(self.now+60);self.notifier.deliver(self.now+60)
        self.assertIn('최초 도달시각 미기록',self.messages[-1])
        self.book.observe(self.now+120,105.);self.notifier.scan(self.now+120);self.notifier.deliver(self.now+120)
        self.assertIn('모의 종료',self.messages[-1]);self.assertIn('비용 후',self.messages[-1])
        self.assertEqual(sum('모의 진입' in x for x in self.messages),1)

    def test_gap_is_excluded_not_profit(self):
        p=dict(price=100.,stop=98.,target1=103.,target2=105.,quantity_btc=1.,net_loss=2.2,direction=1,strategy='box_retest_5m')
        self.book.add('gap',p,self.now);self.book.observe(self.now+180,105.)
        self.notifier.scan(self.now+180);self.notifier.deliver(self.now+180)
        self.assertIn('성과 제외',self.messages[-1])

    def test_old_history_is_not_replayed_as_new(self):
        self.add_candidate(self.now-600)
        self.notifier.scan(self.now);self.notifier.deliver(self.now)
        self.assertNotIn('후보 ·',self.messages[-1])

    def test_failure_reserved_before_send_never_ambiguous_retry(self):
        self.add_candidate();self.notifier.sender=lambda _:False
        self.notifier.scan(self.now);self.assertFalse(self.notifier.deliver(self.now))
        self.assertTrue(self.notifier.db.execute("SELECT 1 FROM events WHERE delivery='failed_or_ambiguous'").fetchone())
        self.notifier.sender=lambda _:self.fail('duplicate retry')
        self.notifier.scan(self.now+60);self.assertFalse(self.notifier.deliver(self.now+60))

    def test_missing_source_progress_is_not_an_entry(self):
        self.notifier.sources={'techniques':str(self.root/'missing.db')}
        self.notifier.scan(self.now);self.notifier.deliver(self.now)
        self.assertIn('미준비',self.messages[-1]);self.assertFalse((self.root/'missing.db').exists())
        self.assertNotIn('모의 진입 ·',self.messages[-1])

    def test_live_gates_and_study_rules_unchanged(self):
        # Frozen from production 862339d; no Git executable/repository required.
        expected={'calculate_plan': 'bd2d5e5cdbc34717bab65c394bcbc1cd684300cd82e42c65ff54fb7571f4fc17', 'classify': 'd1af4071927aa226098bf82fc0ab6e8c2e60b068db67f35a4e76b5d605e98d16', 'assess_candidate': '02cd62e5a1cfbc751066671cc1da430ea9282d98bdb2df461caa36ba358977f2', 'confirm_watch': 'b11c415f319488fa79a8dbce8d0c144851fcb636f2653603a0737e33ecbab6e1', 'alert_gate': 'bb7b1f66fdb0017d21897fad9aa14e1a92e1aa45e458f194c733b7220ef53179', 'record_alert': '7d4e99c77c486c5b798fe17daa0961c47758ac0c9d5df487ca903020770f6bb0'}
        current={n.name:hashlib.sha256(ast.dump(n,include_attributes=False).encode()).hexdigest()
                 for n in ast.parse(Path('lbank_futures_monitor.py').read_text()).body
                 if isinstance(n,ast.FunctionDef) and n.name in expected}
        self.assertEqual(expected,current)
        files={'lbank_public_techniques.py': 'c9ff5a3f2bd6a72651b878b372385796c094300544b56ce4b3740bc108928b61', 'lbank_rsi_divergence.py': '602a4faae7cff60ecb33cc0fd21ae20953e69e4b89acc2bba62979c49737f269', 'lbank_performance.py': '39ce555def76ece101c28bb31ea22d70091b0fbf5febb65fc4de95ac86ace04c'}
        for f,sha in files.items():self.assertEqual(hashlib.sha256(Path(f).read_bytes()).hexdigest(),sha)



if __name__=='__main__':unittest.main()
