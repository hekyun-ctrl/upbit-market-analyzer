"""Read-only study notifications; never creates entries or changes live gates."""
import hashlib
import json
import logging
import os
import sqlite3
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

from lbank_public_techniques import LABELS
from lbank_telegram_transport import TelegramTransport

POLICY = 'native_paper_notifications_v1'
LOG = logging.getLogger('lbank_paper_notifications')
KST = timezone(timedelta(hours=9))


def local_time(ts):
    return datetime.fromtimestamp(ts, KST).strftime('%m-%d %H:%M:%S')


class PaperNotifications:
    """Each thread owns its SQLite connections; persistent delivery de-duplication.

    A crash/ambiguous Telegram response can lose a notification: reserve BEFORE
    sending, never auto-replay it. Study ledgers retain the measured event.
    One aggregate notification/minute; progress every 15min, never a fake entry.
    """
    def __init__(self, path, sources, sender, now):
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path, timeout=.2)
        self.db.row_factory = sqlite3.Row
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value REAL);
          CREATE TABLE IF NOT EXISTS events(id TEXT PRIMARY KEY,observed REAL,
            event_at REAL,kind TEXT,detail TEXT,delivery TEXT);
        ''')
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO meta VALUES('start',?)", (now,))
        self.start = self.db.execute("SELECT value FROM meta WHERE key='start'").fetchone()[0]
        self.sources, self.sender = dict(sources), sender

    def _store(self, key, at, kind, text, now):
        identity = hashlib.sha256(key.encode()).hexdigest()
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO events VALUES(?,?,?,?,?,?)',
                (identity,now,at,kind,text,'pending' if 0 <= now-at <= 300 else 'stale_not_sent'))

    def scan(self, now):
        available = []
        for name, path in self.sources.items():
            try:
                db = sqlite3.connect(Path(path).resolve().as_uri()+'?mode=ro', uri=True, timeout=.1)
                db.row_factory = sqlite3.Row
                try:
                    table = 'technique_candidates' if name == 'techniques' else 'rsi_candidates'
                    candidates = db.execute(f'SELECT * FROM {table} WHERE first_at>=? ORDER BY first_at LIMIT 500',
                                            (max(self.start,now-86400),)).fetchall()
                    for row in candidates:
                        detail=json.loads(row['detail']);event=detail.get('event',detail)
                        strategy=event.get('strategy','rsi_divergence');direction=event.get('direction')
                        side='롱 관찰' if direction==1 else '숏 관찰' if direction==-1 else '방향 미확정'
                        self._store(name+':watch:'+row['id'],row['first_at'],'candidate',
                            f"후보 · {LABELS.get(strategy,strategy)} · {side}\n"
                            f"{local_time(row['first_at'])} 관측 {row['price']:,.1f} · 아직 모의 진입 아님",now)
                    rows = db.execute("SELECT * FROM signals WHERE policy='levels' AND opened>=? ORDER BY opened LIMIT 500",
                                      (max(self.start,now-86400),)).fetchall()
                    for row in rows:
                        p=json.loads(row['plan']);strategy=LABELS.get(p['strategy'],p['strategy'])
                        side='롱' if p['direction']==1 else '숏'
                        prefix=name+':'+row['id']
                        self._store(prefix+':opened',row['opened'],'opened',
                            f"모의 진입 · {strategy} · {side}\n{local_time(row['opened'])} 기준 {p['price']:,.1f}\n"
                            f"모의 손절 {p['stop']:,.1f} / 1차 {p['target1']:,.1f} / 2차 {p['target2']:,.1f}",now)
                        if row['stage']:
                            # TP1 timestamp is not separately stored by this older study.
                            # Report observation time explicitly, never reconstruct a touch time.
                            self._store(prefix+':tp1',now,'tp1',f"1차 목표 상태 확인 · {strategy} · {side}\n"
                                f"확인 {local_time(now)} · 모형 50% 익절 상태 (최초 도달시각 미기록)",now)
                        if row['status']!='open':
                            net='자료 공백/검열: 성과 제외' if row['net'] is None else f"모형 비용 후 {row['net']:+.3f} USDT"
                            self._store(prefix+':closed',row['closed'],'closed',
                                f"모의 종료 · {strategy} · {side}\n{local_time(row['closed'])} · {row['status']} · {net}",now)
                    sample=db.execute('SELECT MAX(ts) FROM samples').fetchone()[0]
                    available.append(f"{name}: {'가격 기록 갱신' if sample is not None and now-sample<=150 else '가격 기록 지연/미준비'}")
                finally:
                    db.close()
            except (OSError, sqlite3.Error, ValueError, KeyError, TypeError):
                available.append(name+': 연구 기록 미준비')
                LOG.warning('PAPER_SOURCE_UNAVAILABLE source=%s',name)
        # Progress messages explicitly do not count as signals or trades.
        slot=int(now//900)
        self._store('progress:'+str(slot),now,'progress',
            '검증 관측 상태 · '+local_time(now)+'\n'+' / '.join(available)+
            '\n후보·종료는 별도 기록하며 조건이 없으면 모의 진입을 만들지 않습니다.',now)
        with self.db:
            self.db.execute("UPDATE events SET delivery='stale_not_sent' WHERE delivery='pending' AND event_at<?",(now-300,))
            self.db.execute('DELETE FROM events WHERE observed<?',(now-7*86400,))
        return available

    def deliver(self, now):
        last=self.db.execute("SELECT value FROM meta WHERE key='last_send'").fetchone()
        if last is not None and now-last[0]<60:return False
        rows=self.db.execute("SELECT * FROM events WHERE delivery='pending' ORDER BY CASE WHEN kind='progress' THEN 1 ELSE 0 END,event_at LIMIT 4").fetchall()
        if not rows:return False
        message='[LBank BTCUSDT | 검증용 모의 알람 · 실전 진입 금지]\n'+ '\n\n'.join(r['detail'] for r in rows)
        message+='\n\n실제 주문 없음 · 미검증 실험 · 가상 체결/비용 모형이며 계좌 수익이 아닙니다.'
        # Persist reservation before network; no ambiguous delivery retries.
        with self.db:
            self.db.executemany("UPDATE events SET delivery='attempted_unconfirmed' WHERE id=?",[(r['id'],) for r in rows])
            self.db.execute("INSERT OR REPLACE INTO meta VALUES('last_send',?)",(now,))
        try:ok=bool(self.sender(message))
        except Exception:ok=False
        with self.db:
            self.db.executemany('UPDATE events SET delivery=? WHERE id=?',
                [('delivered' if ok else 'failed_or_ambiguous',r['id']) for r in rows])
        LOG.info('PAPER_NOTIFICATION policy=%s delivered=%s events=%d kinds=%s orders=false live_gate=false',
            POLICY,ok,len(rows),','.join(r['kind'] for r in rows))
        return ok


class PaperNotificationThread(threading.Thread):
    def __init__(self):
        super().__init__(name='lbank-paper-notifications',daemon=True)

    def run(self):
        try:
            notifier=PaperNotifications(os.getenv('LBANK_PAPER_NOTIFICATIONS_DB','/data/lbank_paper_notifications.sqlite3'),
                dict(techniques=os.getenv('LBANK_PUBLIC_TECHNIQUE_STUDY_DB','/data/lbank_public_technique_study.sqlite3'),
                     divergence=os.getenv('LBANK_RSI_DIVERGENCE_STUDY_DB','/data/lbank_rsi_divergence_study.sqlite3')),
                TelegramTransport().send,time.time())
            LOG.info('PAPER_NOTIFICATIONS_STARTED policy=%s source=existing_native_study_ledgers poll=15 event_batch_min_seconds=60 progress_seconds=900 trade_signal=false',POLICY)
            while True:
                try:
                    now=time.time();notifier.scan(now);notifier.deliver(now)
                except Exception as exc:
                    LOG.error('PAPER_NOTIFICATION_ERROR type=%s live_risk_unchanged=true',type(exc).__name__)
                time.sleep(15)
        except Exception as exc:
            LOG.error('PAPER_NOTIFICATION_INIT_FAILED type=%s live_risk_unchanged=true',type(exc).__name__)
