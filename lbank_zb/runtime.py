"""Production connection supervisor for ZB-1.0, with entry publication fail-closed.
Reads existing native DB, emits durable transition-only operational notices.
No paper fill/READY may be published until the full integration is qualified.
"""
import os,sqlite3,time,logging,threading,json,hashlib
from pathlib import Path
LOG=logging.getLogger('lbank_zb_runtime')
POLICY='ZB-1.0_runtime_connection_v1'

def inspect_native(path,now):
    result={'connected':False,'latest_closed_1m':None,'latest_closed_5m':None,'consecutive_1m':0,'consecutive_5m':0}
    if not Path(path).is_file():return result
    db=sqlite3.connect('file:'+str(Path(path).resolve())+'?mode=ro',uri=True,timeout=1)
    try:
        for period,label,target in [(60,'1m',60),(300,'5m',450)]:
            rows=db.execute("SELECT start FROM native_bars WHERE period=? AND quality='stream_closed' ORDER BY start DESC LIMIT ?",(period,target)).fetchall()
            if rows:
                result['latest_closed_'+label]=rows[0][0]+period;count=1
                for a,b in zip(rows,rows[1:]):
                    if a[0]-b[0]!=period:break
                    count+=1
                result['consecutive_'+label]=count
        latest=result['latest_closed_1m'];result['connected']=latest is not None and 0<=now-latest<=75
        return result
    finally:db.close()

def status(snapshot):
    # Qualification dependencies are explicit; no env switch bypasses them.
    reasons=['PRODUCT_COST_FUNDING_UNVERIFIED','RAW5M_INDEPENDENCE_UNVERIFIED','FUNDING_LEDGER_INTEGRATION_PENDING','LIVE_QUOTE_LIFECYCLE_PENDING','ENGINEERING_READY_NOT_GRANTED']
    if not snapshot['connected']:reasons.insert(0,'NATIVE_FEED_NOT_CURRENT')
    if snapshot['consecutive_1m']<60 or snapshot['consecutive_5m']<450:reasons.append('WARMUP_INSUFFICIENT')
    return {'policy':POLICY,**snapshot,'entry_publication_enabled':False,'orders_enabled':False,'heartbeat_notifications':False,'blocked_reasons':reasons}

class RuntimeThread(threading.Thread):
    def __init__(self,sender=None,native_path=None,state_path=None):
        super().__init__(name='lbank-zb-runtime',daemon=True)
        self.sender=sender;self.native_path=native_path or os.getenv('LBANK_NATIVE_CANDLE_DB','/data/lbank_native_candles.sqlite3');self.state_path=state_path or '/data/lbank_zb_runtime.sqlite3'
    def poll(self,db,now):
        s=status(inspect_native(self.native_path,now));signature=hashlib.sha256(json.dumps({'policy':POLICY,'connected':s['connected'],'reasons':s['blocked_reasons']},sort_keys=True).encode()).hexdigest()
        # Reserve before network; ambiguous delivery is not automatically duplicated.
        with db:
            db.execute('INSERT INTO observations(time,payload) VALUES(?,?)',(int(now),json.dumps(s,sort_keys=True)))
            db.execute('DELETE FROM observations WHERE time<?',(int(now)-86400,))
            new=db.execute("INSERT OR IGNORE INTO notices(signature,status) VALUES(?,'RESERVED')",(signature,)).rowcount
        if new and self.sender:
            message=('[모의 검증 | ZB-1.0 운영 연결]\n'
                     +'원본 봉 연결: '+('확인' if s['connected'] else '대기')+'\n'
                     +'새 진입 알림은 보류 중입니다. 상품·비용·펀딩·호가 연결과 전체 기능 검증이 남아 있습니다.\n'
                     +'기존 참고 알람과 위험 경고는 유지합니다. 이 메시지는 진입 신호가 아닙니다.\n반복 상태 알림은 보내지 않습니다.')
            try:ack=bool(self.sender(message))
            except Exception:ack=False
            with db:db.execute('UPDATE notices SET status=? WHERE signature=?',('API_ACK' if ack else 'DELIVERY_UNKNOWN',signature))
            LOG.info('ZB_NOTIFICATION policy=%s kind=connection_status api_ack=%s entry_signal=false',POLICY,ack)
        LOG.info('ZB_RUNTIME_STATUS %s',json.dumps(s,sort_keys=True));return s
    def run(self):
        Path(self.state_path).parent.mkdir(parents=True,exist_ok=True)
        try:
            db=sqlite3.connect(self.state_path,timeout=2);db.execute('PRAGMA journal_mode=WAL')
            db.executescript('CREATE TABLE IF NOT EXISTS observations(time INTEGER,payload TEXT); CREATE TABLE IF NOT EXISTS notices(signature TEXT PRIMARY KEY,status TEXT);')
            LOG.info('ZB_RUNTIME_STARTED policy=%s source=%s old_reference_alerts=enabled orders=false',POLICY,os.getenv('RAILWAY_GIT_COMMIT_SHA','unknown'))
            while True:
                try:self.poll(db,time.time())
                except Exception as exc:LOG.warning('ZB_RUNTIME_DEGRADED exception=%s entries_blocked=true',type(exc).__name__)
                time.sleep(60)
        except Exception as exc:LOG.error('ZB_RUNTIME_STOPPED exception=%s',type(exc).__name__)
