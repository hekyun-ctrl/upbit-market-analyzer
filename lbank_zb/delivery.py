"""Disabled-by-default durable sender; provider ACK is not device delivery.

No network transport is created here. Caller must supply one explicitly.
Telegram does not offer client idempotency: unknown replies remain UNKNOWN
until an operator resolves them, avoiding blind duplicate entry messages.
"""
import json
from datetime import datetime, timezone, timedelta

LABELS = {'EXPIRED': '유효시간 만료', 'CANCELLED': '진입 취소',
          'HARD_STOP': '손절', 'STRUCTURE_INVALIDATED': '구조 무효화',
          'PARTIAL_TP1': '1차 목표 도달·부분 익절', 'TRAIL_UPDATED': '추적 손절 조정',
          'TRAIL_ALREADY_CROSSED': '추적 손절 통과', 'CAUTION': '주의',
          'DATA_DEGRADED': '자료 장애', 'TIME_REVIEW': '보유 상태 재검토',
          'EXIT_REQUIRED':'종료 판단·호가 체결 추정 대기'}


def message_for(event, key):
    if event['kind'] == 'READY':
        return event['message'] + '\n발송 사건 ' + key[:16]
    stamp = datetime.fromtimestamp(event['time'], timezone(timedelta(hours=9))).isoformat()
    detail = event.get('detail', {})
    return ('[모의 검증 | ZB-1.0 | ' + LABELS.get(event['kind'], event['kind']) + ']\n'
            + '최초 발생 ' + stamp + '\n' + '신호 ' + event.get('id', '자료 상태') + '\n'
            + '기록 ' + json.dumps(detail or {k: v for k, v in event.items() if k not in ('id', 'kind', 'time')},
                                   ensure_ascii=False, default=str) + '\n'
            + '실제 주문·사용자 체결 확인이 아닙니다.\n발송 사건 ' + key[:16])


class Dispatcher:
    def __init__(self, store, sender=None, enabled=False):
        self.store, self.sender, self.enabled = store, sender, enabled

    def drain(self, now, limit=10):
        # Never turn an old DISABLED backlog into a burst after activation.
        db = self.store.db
        with db:
            db.execute("UPDATE outbox SET status='EXPIRED' WHERE kind='READY' AND expires<? AND status IN ('PENDING','FAILED','DISABLED')", (now,))
        if not self.enabled or self.sender is None:
            return 0
        rows = db.execute("SELECT key,payload,attempts,status FROM outbox WHERE status IN ('PENDING','FAILED') OR (status='UNKNOWN' AND kind IN ('HARD_STOP','STRUCTURE_INVALIDATED','TRAIL_ALREADY_CROSSED','EXIT_REQUIRED')) ORDER BY rowid LIMIT ?", (limit,)).fetchall()
        count = 0
        for key, payload, attempts, previous_status in rows:
            event = json.loads(payload)
            if event['kind']=='READY' and now-event.get('quote_received', event['time'])>5:
                with db:db.execute("UPDATE outbox SET status='STALE_QUOTE' WHERE key=?",(key,))
                continue
            meta = db.execute('SELECT next_try FROM delivery_meta WHERE key=?', (key,)).fetchone()
            if meta and meta[0] > now:
                continue
            with db:
                # Crash/response loss after reservation is ambiguous, not success.
                db.execute("UPDATE outbox SET status='UNKNOWN',attempts=attempts+1 WHERE key=?", (key,))
            try:
                message=message_for(event, key)
                if previous_status=='UNKNOWN':message+='\n이전 응답 미확인으로 재전송합니다. 중복 수신 가능.'
                ack = self.sender(message)
            except Exception:
                ack = None
            status = 'ACK' if ack is True else 'FAILED' if ack is False else 'UNKNOWN'
            with db:
                db.execute('UPDATE outbox SET status=? WHERE key=?', (status, key))
                db.execute('INSERT OR REPLACE INTO delivery_meta VALUES(?,?,?)',
                           (key, int(now) + min(300, 5 * 2 ** min(attempts, 6)), int(event['time'])))
            count += 1
        return count

    def activate_new(self, since):
        if not self.enabled:
            return
        with self.store.db:
            self.store.db.execute("UPDATE outbox SET status='PENDING' WHERE status='DISABLED' AND json_extract(payload,'$.time')>=?", (since,))
