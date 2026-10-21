"""SQLite WAL transaction for input, snapshot, event ledger and inert outbox.
Single process writer; fcntl lifetime lock. No external send function is provided.
"""
import sqlite3,json,copy,fcntl,os
from pathlib import Path
from .core import *
from .engine import Engine

def bar_from(row):
    x=dict(row)
    for k in ('o','h','l','c','v'):x[k]=dec(x[k])
    return Bar(**x)
def costs_from(x):
    return Costs(**{k:(None if v is None else dec(v)) if k not in ('status','version') else v for k,v in x.items()})
def candidate_from(x):
    return Candidate(**{k:dec(v) if k in ('close','atr','F','S','I','box_target','box_far') and v is not None else v for k,v in x.items()})
def plan_from(x):
    return Plan(candidate_from(x['candidate']),**{k:costs_from(v) if k=='costs' else dec(v) for k,v in x.items() if k!='candidate'})
def position_from(x):
    x=dict(x);x['plan']=plan_from(x['plan'])
    for k in ('remaining','cash','active_stop','funding','last_price','high','low','last_trail_sent'):
        if x[k] is not None:x[k]=dec(x[k])
    x['health']=set(x['health']);x['review']=set(x['review']);return Position(**x)
def snapshot(e):
    x=vars(e).copy();x['strategies']=sorted(e.strategies);x['seen']=sorted(e.seen);x['health']=sorted(e.health)
    def pos(p):
        v=asdict(p);v['health']=sorted(p.health);v['review']=sorted(p.review);return v
    x['position']=pos(e.position) if e.position else None;x['closed']=[pos(p) for p in e.closed]
    return dumps(x)
def restore(text):
    x=json.loads(text);e=Engine(costs_from(x['costs']),x['latency'],x['strategies'])
    for k in ('one','five','fifteen'):x[k]=[bar_from(b) for b in x[k]]
    x['costs']=costs_from(x['costs']);x['pending']=[candidate_from(c) for c in x['pending']]
    x['position']=position_from(x['position']) if x['position'] else None;x['closed']=[position_from(p) for p in x['closed']]
    for k in ('equity','peak','day_start'):x[k]=dec(x[k])
    for k in ('strategies','seen','health'):x[k]=set(x[k])
    e.__dict__.update(x);return e

class Store:
    def __init__(self,path,engine):
        self.lock=None
        if path!=':memory:':
            Path(path).parent.mkdir(parents=True,exist_ok=True);self.lock=open(path+'.writer.lock','a')
            try:fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:self.lock.close();raise RuntimeError('SINGLE_WRITER_REQUIRED')
        self.db=sqlite3.connect(path);self.db.execute('PRAGMA journal_mode=WAL');self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''CREATE TABLE IF NOT EXISTS inputs(id TEXT PRIMARY KEY,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS corrections(id TEXT,payload TEXT,received INTEGER);
        CREATE TABLE IF NOT EXISTS events(seq INTEGER PRIMARY KEY,event_hash TEXT UNIQUE,payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS snapshots(id INTEGER PRIMARY KEY CHECK(id=1),payload TEXT NOT NULL);
        CREATE TABLE IF NOT EXISTS outbox(key TEXT PRIMARY KEY,kind TEXT,payload TEXT,expires INTEGER,status TEXT,attempts INTEGER DEFAULT 0,error TEXT);''')
        row=self.db.execute('SELECT payload FROM snapshots WHERE id=1').fetchone();self.engine=restore(row[0]) if row else engine
    def ingest(self,b,now):
        key=f'{b.period}:{b.start}';payload=dumps(b);old=self.db.execute('SELECT payload FROM inputs WHERE id=?',(key,)).fetchone()
        if old:
            if old[0]!=payload:
                # Preserve correction, never retroactively alter the already-published thesis.
                with self.db:self.db.execute('INSERT INTO corrections VALUES(?,?,?)',(key,payload,now))
                raise ValueError('REVISION_REQUIRES_REPLAY_VERSION')
            return False
        saved=snapshot(self.engine);n=len(self.engine.events)
        try:
            self.engine.ingest(b,now)
            with self.db:
                self.db.execute('INSERT INTO inputs VALUES(?,?)',(key,payload))
                for ev in self.engine.events[n:]:
                    seq=self.db.execute('SELECT count(*) FROM events').fetchone()[0]+1;h=digest({'seq':seq,'event':ev});body=dumps(ev)
                    self.db.execute('INSERT INTO events VALUES(?,?,?)',(seq,h,body))
                    if ev['kind'] in ('READY','EXPIRED','CANCELLED','HARD_STOP','STRUCTURE_INVALIDATED','PARTIAL_TP1','TRAIL_UPDATED','TRAIL_ALREADY_CROSSED','CAUTION','DATA_DEGRADED'):
                        # Disabled outbox. Sender activation is a separate deployment decision.
                        self.db.execute('INSERT INTO outbox(key,kind,payload,expires,status) VALUES(?,?,?,?,?)',(h,ev['kind'],body,ev.get('expires'),'DISABLED'))
                self.db.execute('INSERT OR REPLACE INTO snapshots VALUES(1,?)',(snapshot(self.engine),))
            return True
        except Exception:self.engine=restore(saved);raise
    def pending(self,now):
        with self.db:self.db.execute("UPDATE outbox SET status='EXPIRED' WHERE kind='READY' AND expires<? AND status IN ('PENDING','FAILED','UNKNOWN','DISABLED')",(now,))
        return self.db.execute("SELECT key,payload FROM outbox WHERE status IN ('PENDING','FAILED','UNKNOWN')").fetchall()
    def delivery_result(self,key,ack,error=None):
        # ACK is provider response, never proof of device receipt or user fill.
        with self.db:self.db.execute('UPDATE outbox SET status=?,attempts=attempts+1,error=? WHERE key=?',('ACK' if ack else 'UNKNOWN' if ack is None else 'FAILED',error,key))
    def close(self):
        self.db.close()
        if self.lock:fcntl.flock(self.lock,fcntl.LOCK_UN);self.lock.close()
