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
        # Frozen source guard: three intentionally changed entry functions now include
        # the 2026-10-24 opt-in comparison policy; legacy behavior is also covered
        # by entry/refinement and incident replay regressions. Notification gates,
        # research accounting and default activation remain unchanged.
        expected={'calculate_plan': '7c8faa5cc5f16c158764553ce89906bf49b06347a48638cddcb9b162165ee976', 'classify': '3a4b9b128796029a0319f852f35d8c4d1e2a5fe485b1da562ede1555adc21a5b', 'assess_candidate': '7baf0c17c52483df450ff1731c95a2f7f996cd9f7bbf9ab525021ed128310d6c', 'confirm_watch': 'f72998d1a3cde2eba38cf4c3fbc86d0deb14417106c38550b8abba47a4c645a4', 'alert_gate': 'e047eeeb07dd40558cd745dea5e3731c2c177cde127a7137c37fd14a3213ffab', 'record_alert': '89147d99872cc78f1b8f8b4e6d86660806736030e335bf732f2975de24f3e04b'}
        src=Path('lbank_futures_monitor.py').read_text()
        current={n.name:hashlib.sha256(ast.get_source_segment(src,n).encode()).hexdigest()
                 for n in ast.parse(src).body
                 if isinstance(n,ast.FunctionDef) and n.name in expected}
        self.assertEqual(expected,current)
        # 2026-10-23: only PerformanceBook startup cleanup changed: close the
        # connection if schema initialization fails. Accounting rules unchanged.
        files={'lbank_public_techniques.py': 'c9ff5a3f2bd6a72651b878b372385796c094300544b56ce4b3740bc108928b61', 'lbank_rsi_divergence.py': '602a4faae7cff60ecb33cc0fd21ae20953e69e4b89acc2bba62979c49737f269', 'lbank_performance.py': '8386b38bda3d4bfd93a6efb6490efb482ef69077706e553ebff4e3ba687ff7be'}
        for f,sha in files.items():self.assertEqual(hashlib.sha256(Path(f).read_bytes()).hexdigest(),sha)



if __name__=='__main__':unittest.main()
