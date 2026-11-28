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
        # 2026-11-24: sealed 1m flat-context override and one post-stop breakout
        # retry are intentional capture-mode changes; covered by
        # test_lbank_sealed_breakout. Monetary sizing and research accounting
        # are unchanged. Fingerprints below pin this explicitly reviewed revision.
        # 2026-11-22 three-route revision: explicitly distinguish breakout,
        # continuation and pullback candidates; unchanged monetary/context,
        # RSI and two-stage confirmation functions remain fingerprint-guarded.
        # Explicit 2026-11-22 capture-mode extension: classify adds a
        # completed-1m fast-resumption candidate but still runs unchanged
        # assess_candidate, calculate_plan, confirm_watch, alert_gate and record_alert.
        # Frozen source guard: three intentionally changed entry functions now include
        # the 2026-10-24 opt-in comparison policy; legacy behavior is also covered
        # by entry/refinement and incident replay regressions. Notification gates,
        # research accounting and default activation remain unchanged.
        # confirm_watch deliberately carries frozen quote-crossing evidence;
        # its new behavior is covered by entry reassessment/freshness tests.
        expected={'calculate_plan': '4f5c259210303188377d6a30ffc8bc4caf0d701fb14cf120175c749e41520398', 'classify': 'e3b5adafce1c7683c26c975e31eb5c3cb6db1b50775f6dd8f71d02aac46de9a5', 'assess_candidate': '40d92add4e1b2681bf787b6ef4deedc79f7f56ea2d474385732b942c2a464ef6', 'alert_gate': '7acc5aea139cc7239616caf74e48fb7bdad06e79ec608afac8c0b64c3b9ed48b', 'record_alert': '7b0ddd57e17180fc5228cfc5aeb7dcb65777644aeadb919e514da800610cb300'}
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
