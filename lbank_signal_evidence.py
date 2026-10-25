"""Versioned, inert trade-evidence ledger. No network, notifier or strategy.

Known receipt times and nominal availability scenarios are distinct. Immutable
anchors identify opportunities independently of policy, display IDs or prices
observed later. A send intent is recorded atomically but is always disabled.
"""
import hashlib
import json
import math
import sqlite3

SCHEMA = 'trade_evidence_v1'
PRODUCT = 'lbank:linear_usdt_perpetual:BTCUSDT:LAST'
GATES = ('momentum', 'context', 'rsi', 'confirmation', 'plan', 'alert_limits')


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'),
                      ensure_ascii=False, allow_nan=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def gate_vector():
    return {key:dict(status='not_evaluated') for key in GATES}


def event_id(direction, family, anchor, product=PRODUCT):
    if (type(direction) is not int or direction not in (-1, 1) or not isinstance(family, str) or not family
            or not isinstance(anchor, dict) or not anchor.get('bar_hash')
            or type(anchor.get('bar_start')) is not int or anchor['bar_start'] < 0
            or anchor.get('period') not in (60, 300)
            or anchor['bar_start'] % anchor['period']
            or anchor.get('field') not in ('high', 'low')
            or type(anchor.get('price')) not in (int,float)
            or not math.isfinite(anchor.get('price', float('nan')))
            or anchor['price'] <= 0):
        raise ValueError('ANCHOR_PROVENANCE_REQUIRED')
    # known_at can become known later without changing the structural identity.
    identity = {k:anchor[k] for k in ('bar_hash','bar_start','period','field','price')}
    return 'op-v1:' + digest(dict(product=product, direction=direction,
                                  family=family, anchor=identity))


class EvidenceLedger:
    def __init__(self, path):
        self.db = sqlite3.connect(str(path))
        self.db.execute('PRAGMA foreign_keys=ON')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS source_events(
            ingest_seq INTEGER PRIMARY KEY,raw_hash TEXT UNIQUE NOT NULL,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS input_sets(input_hash TEXT PRIMARY KEY,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS opportunity_events(
            event_id TEXT PRIMARY KEY,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS decision_events(
            seq INTEGER PRIMARY KEY,decision_hash TEXT UNIQUE NOT NULL,event_id TEXT,
            policy TEXT NOT NULL,evaluated_at REAL NOT NULL,state TEXT NOT NULL,
            first_reject TEXT,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS frozen_plans(
            plan_id TEXT PRIMARY KEY,event_id TEXT,policy TEXT NOT NULL,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS dispatch_events(
            dedupe_key TEXT PRIMARY KEY,plan_id TEXT NOT NULL REFERENCES frozen_plans,
            status TEXT NOT NULL,payload TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS outcome_events(
            outcome_hash TEXT PRIMARY KEY,plan_id TEXT NOT NULL REFERENCES frozen_plans,payload TEXT NOT NULL);
        ''')
        row=self.db.execute("SELECT payload FROM meta WHERE key='schema'").fetchone()
        if row and row[0] != SCHEMA:
            self.db.close();raise ValueError('EVIDENCE_SCHEMA_MISMATCH')
        with self.db:self.db.execute("INSERT OR IGNORE INTO meta VALUES('schema',?)",(SCHEMA,))

    def source(self, raw, *, event_time, received_at=None, quality=None):
        for time in (event_time,received_at):
            if time is not None and (not math.isfinite(time) or time < 0):
                raise ValueError('INVALID_SOURCE_TIME')
        key=digest(raw)
        payload=dict(product=PRODUCT,raw_hash=key,event_time=event_time,
                     received_at=received_at,quality=quality,raw=raw)
        old=self.db.execute('SELECT payload FROM source_events WHERE raw_hash=?',(key,)).fetchone()
        if old and old[0] != canonical(payload):raise ValueError('SOURCE_RECEIPT_REVISION_REQUIRES_VERSION')
        with self.db:self.db.execute('INSERT OR IGNORE INTO source_events(raw_hash,payload) VALUES(?,?)',
                                     (key,canonical(payload)))
        return key

    def inputs(self, hashes):
        hashes=sorted(set(hashes));key=digest(hashes)
        with self.db:self.db.execute('INSERT OR IGNORE INTO input_sets VALUES(?,?)',(key,canonical(hashes)))
        return key

    def opportunity(self, direction, family, anchor, *, first_observed_at):
        known=anchor.get('known_at')
        if known is not None and (not math.isfinite(known) or known>first_observed_at):
            raise ValueError('ANCHOR_NOT_AVAILABLE')
        key=event_id(direction,family,anchor)
        payload=dict(event_id=key,product=PRODUCT,direction=direction,family=family,
                     anchor=anchor,first_observed_at=first_observed_at)
        old=self.db.execute('SELECT payload FROM opportunity_events WHERE event_id=?',(key,)).fetchone()
        if old and json.loads(old[0])['first_observed_at'] < first_observed_at:
            return key
        with self.db:self.db.execute('INSERT OR REPLACE INTO opportunity_events VALUES(?,?)',(key,canonical(payload)))
        return key

    def decision(self, *, event, policy, evaluated_at, input_hash, state,
                 gates=None, first_reject=None, available_at=None, availability_basis=None, **detail):
        if (not math.isfinite(evaluated_at) or evaluated_at < 0
                or available_at is not None and (not math.isfinite(available_at) or available_at > evaluated_at)):
            raise ValueError('INPUT_NOT_AVAILABLE_AT_EVALUATION')
        if available_at is not None and availability_basis not in ('observed_receipt','nominal_scenario'):
            raise ValueError('AVAILABILITY_BASIS_REQUIRED')
        row=self.db.execute('SELECT payload FROM input_sets WHERE input_hash=?',(input_hash,)).fetchone()
        if row is None:raise ValueError('INPUT_SET_REQUIRED')
        for source in json.loads(row[0]):
            raw=self.db.execute('SELECT payload FROM source_events WHERE raw_hash=?',(source,)).fetchone()
            if raw is None:raise ValueError('SOURCE_EVENT_REQUIRED')
            source_value=json.loads(raw[0]);received=source_value['received_at']
            if (source_value['event_time'] is not None and source_value['event_time'] > evaluated_at
                    or received is not None and received > evaluated_at):
                raise ValueError('INPUT_NOT_AVAILABLE_AT_EVALUATION')
            if availability_basis=='observed_receipt' and received is None:
                raise ValueError('RECEIPT_PROVENANCE_REQUIRED')
        payload=dict(schema=SCHEMA,event_id=event,policy=policy,evaluated_at=evaluated_at,
                     input_hash=input_hash,state=state,gates=gates or gate_vector(),
                     first_reject=first_reject,available_at=available_at,
                     availability_basis=availability_basis,**detail)
        key=digest(payload)
        with self.db:self.db.execute('INSERT OR IGNORE INTO decision_events '
            '(decision_hash,event_id,policy,evaluated_at,state,first_reject,payload) VALUES(?,?,?,?,?,?,?)',
            (key,event,policy,evaluated_at,state,first_reject,canonical(payload)))
        return key

    def freeze(self, *, event, policy, plan, provenance, intent=False):
        payload=dict(event_id=event,policy=policy,plan=plan,provenance=provenance)
        key='plan-v1:'+digest(payload)
        with self.db:
            self.db.execute('INSERT OR IGNORE INTO frozen_plans VALUES(?,?,?,?)',
                            (key,event,policy,canonical(payload)))
            if intent:self.db.execute('INSERT OR IGNORE INTO dispatch_events VALUES(?,?,?,?)',
                (key,key,'DISABLED',canonical(dict(plan_id=key,orders_enabled=False))))
        return key

    def outcome(self, plan_id, value):
        key=digest(dict(plan_id=plan_id,value=value))
        with self.db:self.db.execute('INSERT OR IGNORE INTO outcome_events VALUES(?,?,?)',
                                     (key,plan_id,canonical(value)))

    def delivery(self, key, status, **receipt):
        # No sender here. Uncertain provider responses are never marked ACK.
        if status not in ('acknowledged','send_ack_unknown','send_failed'):
            raise ValueError('INVALID_DELIVERY_STATE')
        if status=='acknowledged' and not receipt.get('provider_message_id'):
            raise ValueError('PROVIDER_ACK_EVIDENCE_REQUIRED')
        old=self.db.execute('SELECT status,payload FROM dispatch_events WHERE dedupe_key=?',(key,)).fetchone()
        if old is None:raise ValueError('DISPATCH_INTENT_REQUIRED')
        value=canonical(receipt)
        if old[0]=='acknowledged' and old != (status,value):
            raise ValueError('ACK_IS_IMMUTABLE')
        with self.db:self.db.execute('UPDATE dispatch_events SET status=?,payload=? WHERE dedupe_key=?',
                                     (status,value,key))

    def close(self):
        self.db.close()


def pair_plans(plans, decisions, old='capture', new='location'):
    """Exact structural IDs only. No time/price-nearest or outcome-based match."""
    by_event={};groups=[];unique={}
    for p in plans:
        key=p['plan_id']
        if key in unique and canonical(unique[key])!=canonical(p):raise ValueError('PLAN_ID_REVISION')
        unique[key]=p
    for p in unique.values():
        event=p.get('event_id')
        if not event:
            groups.append(dict(group='unmatched',reason='anchor_provenance_missing',
                               old=p if p['policy']==old else None,new=p if p['policy']==new else None))
            continue
        by_event.setdefault(event,{old:[],new:[]})[p['policy']].append(p)
    seen={(d.get('event_id'),d['policy']) for d in decisions if d.get('event_id')}
    for event,policies in sorted(by_event.items()):
        a,b=policies[old],policies[new]
        if len(a)>1 or len(b)>1:
            for p in a+b:groups.append(dict(event_id=event,group='unmatched',reason='multiple_plans_same_anchor',
                                           old=p if p['policy']==old else None,new=p if p['policy']==new else None))
        elif a and b:groups.append(dict(event_id=event,group='matched',old=a[0],new=b[0]))
        elif a:groups.append(dict(event_id=event,group='excluded' if (event,new) in seen else 'unmatched',
                                  reason='comparator_evaluated_same_anchor' if (event,new) in seen else 'different_or_unobserved_structure',old=a[0],new=None))
        else:groups.append(dict(event_id=event,group='added' if (event,old) in seen else 'unmatched',
                                reason='comparator_evaluated_same_anchor' if (event,old) in seen else 'different_or_unobserved_structure',old=None,new=b[0]))
    return groups
