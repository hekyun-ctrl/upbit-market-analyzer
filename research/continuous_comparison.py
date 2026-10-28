"""Inert restartable orchestration of the frozen retained-replay schedule.

One source observation, independent policy watches/cooldowns/invalidation sets,
atomic cursor+decisions+plans. Every no-signal/unavailable interval is retained.
The original replay schedule is explicit; this is not a deployed live scheduler.
Unseen qualification has no boolean override and fails closed on missing receipts.
"""
import copy
from contextlib import ExitStack
import hashlib
import json
import math
from pathlib import Path
import threading
from types import SimpleNamespace

from lbank_signal_evidence import canonical, digest, pair_plans
from lbank_gate_trace import evaluate
from lbank_live_evidence import LiveEvidenceStore
from research.early_reversal_experiment import BASE_CLASSES, E5_CLASSES, replay

ROOT = Path(__file__).resolve().parents[1]
BLOCKERS = ['RAW5M_INDEPENDENCE_UNVERIFIED', 'CANDLE_VOLUME_UNIT_UNVERIFIED']
ADAPTER = SimpleNamespace(enabled=True)
LOCK = threading.Lock()  # Frozen research adapters patch globals, so never run concurrently.


def policy_spec(index, *, e5=False):
    if type(index) is not int or not 0 <= index < len(BASE_CLASSES) or type(e5) is not bool:
        raise ValueError('UNKNOWN_FROZEN_POLICY')
    cls = (E5_CLASSES if e5 else BASE_CLASSES)[index]
    # Resolve identity via the original mixins rather than invent a new strategy.
    return dict(index=index, e5=e5, class_name=cls.__name__,
                confirmation_on_discovery=any(c.__name__=='E3Mixin' for c in cls.__mro__))


def declaration(policies, costs, *, purpose='development', source_commit=None):
    if purpose not in ('development','unseen') or len(policies)<2:
        raise ValueError('COMPARISON_DECLARATION_REQUIRED')
    if len({(p['index'],p['e5']) for p in policies}) != len(policies):
        raise ValueError('DUPLICATE_POLICY')
    for p in policies:
        if p != policy_spec(p['index'],e5=p['e5']):
            raise ValueError('FROZEN_POLICY_SPEC_MISMATCH')
    code = {str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
            for p in sorted(ROOT.glob('lbank_*.py'))}
    code.update({str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted((ROOT/'research').glob('*.py'))})
    return dict(schema='continuous_comparison_v1', purpose=purpose,
        schedule='frozen_retained_replay_classify_then_confirm_then_arm_v1',
        policies=policies, costs=costs, nominal_delay_seconds=0,
        source_commit=source_commit, code_sha256=code, source_blockers=BLOCKERS,
        outbound=False, orders=False, production_scheduler_equivalence_claimed=False)


def initial_state():
    return dict(alert=dict(day=None,count=0,last_time=None,last_side=None), watch=None,
        invalidated_events=[], e5_invalidated=[], observations=0, valid=0, unavailable=0,
        watches=0, expired=0, plans=[])


class ContinuousComparison:
    def __init__(self, book, manifest, *, max_observations=100000, max_bytes=64*1024*1024):
        if type(max_observations) is not int or max_observations<1:raise ValueError("INVALID_COMPARISON_CAP")
        self.max_observations=max_observations
        if type(max_bytes) is not int or max_bytes<128*1024:raise ValueError("INVALID_COMPARISON_BYTE_CAP")
        self.book, self.db, self.manifest = book, book.db, copy.deepcopy(manifest)
        if manifest.get('schema') != 'continuous_comparison_v1' or manifest.get('outbound') is not False or manifest.get('orders') is not False:
            raise ValueError('INERT_COMPARISON_REQUIRED')
        expected = declaration(manifest['policies'],manifest['costs'],purpose=manifest['purpose'],source_commit=manifest.get('source_commit'))
        if manifest != expected:
            raise ValueError('FROZEN_MANIFEST_OR_CODE_CHANGED')
        self.identity = digest(manifest)
        path=Path(self.db.execute('PRAGMA database_list').fetchone()[2])
        page_size=self.db.execute('PRAGMA page_size').fetchone()[0]
        if self.db.execute('PRAGMA page_count').fetchone()[0]*page_size>max_bytes:
            raise ValueError('EXISTING_COMPARISON_EXCEEDS_BYTE_CAP')
        self.db.execute('PRAGMA max_page_count='+str(max_bytes//page_size))
        self.capacity_guard=SimpleNamespace(db=self.db,path=path,max_bytes=max_bytes)
        # Reuse the sidecar's nondeleting main/WAL guard. This is an offline
        # analysis writer; a pinned reader/cap pauses it rather than deleting.
        LiveEvidenceStore._check_wal_cap(self.capacity_guard)
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS comparison_runs(run_id TEXT PRIMARY KEY,manifest TEXT NOT NULL,cursor INTEGER NOT NULL);
          CREATE TABLE IF NOT EXISTS comparison_observations(run_id TEXT NOT NULL,seq INTEGER NOT NULL,observation_hash TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(run_id,seq));
          CREATE TABLE IF NOT EXISTS comparison_states(run_id TEXT NOT NULL,policy TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(run_id,policy));
          CREATE TABLE IF NOT EXISTS comparison_steps(run_id TEXT NOT NULL,seq INTEGER NOT NULL,policy TEXT NOT NULL,payload TEXT NOT NULL,PRIMARY KEY(run_id,seq,policy));
        ''')
        with book.transaction():
            row=self.db.execute('SELECT manifest FROM comparison_runs WHERE run_id=?',(self.identity,)).fetchone()
            if row and row[0]!=canonical(manifest):raise ValueError('COMPARISON_MANIFEST_REVISION')
            self.db.execute('INSERT OR IGNORE INTO comparison_runs VALUES(?,?,-1)',(self.identity,canonical(manifest)))
            for spec in manifest['policies']:
                name=self.name(spec)
                self.db.execute('INSERT OR IGNORE INTO comparison_states VALUES(?,?,?)',(self.identity,name,canonical(initial_state())))

    def name(self,spec):
        return spec['class_name']

    def state(self,name):
        return json.loads(self.db.execute('SELECT payload FROM comparison_states WHERE run_id=? AND policy=?',(self.identity,name)).fetchone()[0])

    def qualification(self, point):
        # Current source code has no official final decoder or independent5m
        # qualification. Even a synthetic fully-bound snapshot stays blocked.
        reasons=list(BLOCKERS)
        if point.get('received_snapshot_hash') is None:reasons.append('FINAL_RECEIPT_SNAPSHOT_REQUIRED')
        if point.get('quote_record_hash') is None:reasons.append('QUOTE_RECEIPT_REQUIRED')
        if self.manifest['purpose']=='development':reasons.append('REUSED_DEVELOPMENT_INPUT')
        return dict(qualified=False, reasons=reasons, unseen_comparison_executed=False)

    def step(self, point):
        if digest(self.manifest)!=self.identity:raise ValueError('FROZEN_RUN_DECLARATION_CHANGED')
        seq,at=point['seq'],point['observed_at']
        if type(seq) is not int or seq<0 or type(at) not in (int,float) or not math.isfinite(at):
            raise ValueError('INVALID_COMPARISON_OBSERVATION')
        if 'source_observation' not in point or 'quote' not in point or 'native_state' not in point:
            raise ValueError('SOURCE_OBSERVATION_REQUIRED')
        if type(point['quote']) not in (int,float) or not math.isfinite(point['quote']) or point['quote']<=0:
            raise ValueError('INVALID_COMPARISON_QUOTE')
        state=point['native_state']
        if state is not None:
            if state.get('quote')!=point['quote']:raise ValueError('COMPARISON_QUOTE_STATE_MISMATCH')
            for period,name in ((60,'minute_bars'),(300,'five_bars')):
                for b in state.get(name,[]):
                    if b['start']+period>at or b.get('received_at',0) is not None and b.get('received_at',0)>at:
                        raise ValueError('FUTURE_COMPARISON_SOURCE')
        if len(canonical(point).encode())>8*1024*1024:raise ValueError("COMPARISON_INPUT_CAP")
        key=digest(point)
        old=self.db.execute('SELECT observation_hash FROM comparison_observations WHERE run_id=? AND seq=?',(self.identity,seq)).fetchone()
        if old:
            if old[0]!=key:raise ValueError('COMPARISON_OBSERVATION_REVISION')
            return self.steps(seq)
        cursor=self.db.execute('SELECT cursor FROM comparison_runs WHERE run_id=?',(self.identity,)).fetchone()[0]
        if seq!=cursor+1:raise ValueError('COMPARISON_SEQUENCE_GAP')
        if seq>=self.max_observations:raise ValueError('COMPARISON_OBSERVATION_CAP_NONDELETING')
        if cursor>=0:
            prior=json.loads(self.db.execute('SELECT payload FROM comparison_observations WHERE run_id=? AND seq=?',(self.identity,cursor)).fetchone()[0])
            if at<prior['observed_at']:raise ValueError('COMPARISON_TIME_REGRESSION')
        if not LOCK.acquire(blocking=False):raise RuntimeError('COMPARISON_ALREADY_EVALUATING')
        try:
            LiveEvidenceStore._check_wal_cap(self.capacity_guard)
            with self.book.transaction():
                self.db.execute('INSERT INTO comparison_observations VALUES(?,?,?,?)',(self.identity,seq,key,canonical(point)))
                for spec in self.manifest['policies']:
                    name=self.name(spec); current=self.state(name)
                    result=self._step_policy(spec,current,point)
                    self.db.execute('UPDATE comparison_states SET payload=? WHERE run_id=? AND policy=?',(canonical(current),self.identity,name))
                    self.db.execute('INSERT INTO comparison_steps VALUES(?,?,?,?)',(self.identity,seq,name,canonical(result)))
                self.db.execute('UPDATE comparison_runs SET cursor=? WHERE run_id=?',(seq,self.identity))
            self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
            return self.steps(seq)
        finally:
            LOCK.release()

    def steps(self,seq):
        return [json.loads(r[0]) for r in self.db.execute('SELECT payload FROM comparison_steps WHERE run_id=? AND seq=? ORDER BY policy',(self.identity,seq))]

    def _step_policy(self,spec,current,point):
        name,at=self.name(spec),point['observed_at'];state=point['native_state']
        current['observations']+=1
        result=dict(policy=name,seq=point['seq'],at=at,source_observation_sha256=digest(point['source_observation']),
            qualification=self.qualification(point), phases=[], no_entry=True)
        if state is None or self.manifest['purpose']=='unseen':
            current['unavailable']+=1
            current['watch']=None
            result.update(status='input_unavailable',reason=point.get('unavailable_reason') or 'source_qualification_unavailable',
                          modeled_net_usdt=None, valid_interval=False)
            return result
        current['valid']+=1
        cls=(E5_CLASSES if spec['e5'] else BASE_CLASSES)[spec['index']]
        trace=cls(self.book,'capture',self.identity,0,self.manifest['costs'])
        trace.policy=name;trace.now=at;trace.state=state
        trace.quote_source=copy.deepcopy(point['source_observation'])
        trace.quote_source.setdefault('logged_at',replay.iso(at))
        for field in ('invalidated_events','e5_invalidated'):
            if hasattr(trace,field):setattr(trace,field,set(current[field]))
        context=dict(native_state=state,quote=point['quote'],evaluated_at=at)
        def invoke(function,*args,**kwargs):
            diag=kwargs.get('diagnostics',{})
            value=evaluate(ADAPTER,function,*args,trace_context=context,trace_diagnostics=diag,**kwargs)
            result['phases'].append(dict(function=function.__name__,diagnostics=diag))
            return value
        with ExitStack() as hooks:
            for key,attribute in replay.CONFIG_MAP.items():
                if key not in self.manifest['costs']:raise ValueError('FROZEN_COSTS_REQUIRED')
                hooks.enter_context(replay.patch.object(replay.monitor,attribute,self.manifest['costs'][key]))
            hooks.enter_context(replay.patch.object(replay.monitor,'ENTRY_REFINEMENT_MODE','capture'))
            trace.patches(hooks)
            proposal=invoke(replay.monitor.classify,state['prices'],native_state=state,diagnostics={})
            watch=current['watch']
            def confirm():
                diag={}
                plan=invoke(replay.monitor.confirm_watch,state['prices'],current['watch']['plan'],at-current['watch']['time'],native_state=state,diagnostics=diag)
                if plan:
                    gate=invoke(replay.monitor.alert_gate,at,plan['side'],current['alert'],plan=plan)
                    if gate=='ok':
                        replay.compact_plan(plan)  # Original evidence adapter freezes its exact plan.
                        card=copy.deepcopy(trace.cards[-1])
                        card['outcome']=dict(status='unresolved',net_pnl_model=None,reason='followup_not_yet_observed')
                        self.book.outcome(card['plan_id'],card['outcome'])
                        current['plans'].append(card)
                        replay.monitor.record_alert(at,plan['side'],current['alert'],plan=plan)
                        result.update(no_entry=False,plan_id=card['plan_id'])
                    current['watch']=None
                elif diag.get('reason')==replay.monitor.BREAKOUT_CONFIRMATION_REJECT or diag.get('cancel_watch') is True:
                    current['watch']=None
            if watch:
                age=at-watch['time']
                if age>replay.monitor.ENTRY_CONFIRM_MAX_AGE_SECONDS:
                    current['expired']+=1;current['watch']=None
                    result['phases'].append(dict(function='watch_expiry',reason='watch_expired'))
                elif age >= (0 if spec['confirmation_on_discovery'] else 30):confirm()
            if proposal is not None and hasattr(trace,'schedule'):proposal=trace.schedule(proposal)
            if current['watch'] is None and proposal:
                gate=invoke(replay.monitor.alert_gate,at,proposal['side'],current['alert'],plan=proposal)
                if gate=='ok':
                    current['watches']+=1;current['watch']=dict(plan=copy.deepcopy(proposal),time=at)
                    if spec['confirmation_on_discovery']:confirm()
            for field in ('invalidated_events','e5_invalidated'):
                if hasattr(trace,field):current[field]=sorted(getattr(trace,field))
        result.update(status='evaluated',valid_interval=True,
            modeled_net_usdt=0 if result['no_entry'] else None,
            no_signal_interval=result['no_entry'],
            no_qualified_candidate=all(p.get('diagnostics',{}).get('reason')!='qualified' for p in result['phases']))
        return result

    def followups(self,bars):
        """Original fixed six-hour benchmark; missing/ambiguous paths stay null."""
        if not LOCK.acquire(blocking=False):raise RuntimeError('COMPARISON_ALREADY_EVALUATING')
        try:
            LiveEvidenceStore._check_wal_cap(self.capacity_guard)
            with self.book.transaction(), ExitStack() as hooks:
                for key,attribute in replay.CONFIG_MAP.items():
                    hooks.enter_context(replay.patch.object(replay.monitor,attribute,self.manifest['costs'][key]))
                for spec in self.manifest['policies']:
                    name=self.name(spec);current=self.state(name)
                    for card in current['plans']:
                        if card['outcome']['status']!='unresolved':continue
                        observation=dict(card['plan'],at=replay.iso(card['provenance']['confirm_at']))
                        outcome=replay.outcome(observation,bars)
                        outcome['net_pnl_model']=outcome.get('net_usdt_low') if outcome['status']=='exact_in_model' else None
                        outcome['source_bars_sha256']=digest({str(k):v for k,v in bars.items()})
                        outcome['actual_fill_verified']=False
                        from research.compare_signal_decisions import first_contact
                        outcome['first_contact']=first_contact(card,bars)
                        self.book.outcome(card['plan_id'],outcome)
                        card['outcome']=outcome
                    self.db.execute('UPDATE comparison_states SET payload=? WHERE run_id=? AND policy=?',(canonical(current),self.identity,name))
            self.db.execute('PRAGMA wal_checkpoint(TRUNCATE)')
        finally:
            LOCK.release()

    def report(self):
        states={self.name(s):self.state(self.name(s)) for s in self.manifest['policies']}
        all_plans=[p for s in states.values() for p in s['plans']]
        pairs=[]
        names=list(states)
        decisions=[json.loads(r[0]) for r in self.db.execute('SELECT payload FROM decision_events')]
        decisions=[d for d in decisions if d.get('window')==self.identity]
        for new in names[1:]:
            local=[p for p in all_plans if p['policy'] in (names[0],new)]
            for group in pair_plans(local,decisions,old=names[0],new=new):
                group['comparison']=names[0]+'_vs_'+new
                group['gate_reasons']=[d for d in decisions if group.get('event_id') and d.get('event_id')==group['event_id'] and d['policy'] in (names[0],new)]
                old,new_card=group.get('old'),group.get('new')
                group['confirmation_lead_seconds']=(old['provenance']['confirm_at']-new_card['provenance']['confirm_at']) if old and new_card else None
                group['side_adjusted_entry_change']=(old['plan']['direction']*(new_card['plan']['price']-old['plan']['price'])) if old and new_card else None
                pairs.append(group)
        metrics={}
        for name,s in states.items():
            count=len(s['plans']);unresolved=sum(p['outcome']['status']=='unresolved' for p in s['plans'])
            valid=s['valid']; complete=s['unavailable']==0 and s['observations']>0
            exact=complete and all(p['outcome']['status']=='exact_in_model' for p in s['plans'])
            equity=peak=dd=0.
            if exact:
                for p in sorted(s['plans'],key=lambda p:p['outcome']['latest_exit_bar_end']):
                    equity+=p['outcome']['net_usdt_low'];peak=max(peak,equity);dd=max(dd,peak-equity)
            first=[p['outcome'].get('first_contact',{}).get('tp1_before_sl') for p in s['plans']]
            metrics[name]=dict(observations=s['observations'],valid_observations=valid,unavailable=s['unavailable'],
                trade_count=count,stop_first_count=sum(v is False for v in first) if complete and all(v is not None for v in first) else None,
                stop_first_unknown_count=sum(v is None for v in first),
                emergency_count=None,net_pnl_model=equity if exact else None,
                closed_drawdown_model=dd if exact else None,full_mtm_drawdown=None,
                unresolved_count=unresolved,unresolved_ratio=unresolved/count if count else None,
                comparison_economics_complete=exact,pending_watch=s['watch'] is not None)
        return dict(run_id=self.identity,manifest=self.manifest,metrics=metrics,pairing=pairs,
            source_blockers=BLOCKERS,qualified=False,profitability_validated=False,
            independent_policy_state=True,all_observations_retained=True,
            live_runtime_verified=False,outbound=False,orders=False)
