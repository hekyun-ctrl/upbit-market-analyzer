"""Inert original entry scheduler, executed from the unchanged monitor AST.

This adapts the entry slice of run(), not its network/risk-notice/startup loop.
Every actual quote-poll input (including polls with no entry evaluation) must
be supplied by the caller. Virtual delivery is a declared model boundary; it
is never a Telegram acknowledgement. No historical poll/receipt is invented.
"""
import ast
import copy
from collections import Counter, deque
from contextlib import ExitStack
import hashlib
from pathlib import Path
from types import SimpleNamespace

from lbank_signal_evidence import canonical, digest
from research.continuous_comparison import ContinuousComparison, ADAPTER, replay
from research.early_reversal_experiment import BASE_CLASSES

SOURCE = Path(replay.monitor.__file__)
SCHEDULE = 'original_live_entry_ast_v1'
PERSIST = ('last_sample', 'last_setup_key', 'observed_ready_key',
           'first_ready_observed_at', 'watch', 'alert_state', 'observations',
           'last_entry_diagnostics')


def source_slice():
    """Select exact statements; fail on missing/ambiguous source boundaries."""
    tree = ast.parse(SOURCE.read_text())
    run = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'run')
    loop = next(n for n in run.body if isinstance(n, ast.While))
    body = loop.body[0].body
    def unique(test):
        found = [n for n in body if test(n)]
        if len(found) != 1:
            raise ValueError('ORIGINAL_SCHEDULE_SOURCE_BOUNDARY_CHANGED')
        return copy.deepcopy(found[0])
    assignments = {}
    for name in ('native_key', 'sample_due', 'confirm_due', 'new_setup_bar',
                 'quote_setup_due', 'setup_due'):
        assignments[name] = unique(lambda n: isinstance(n, ast.Assign) and
            len(n.targets) == 1 and isinstance(n.targets[0], ast.Name) and n.targets[0].id == name)
    ready = unique(lambda n: isinstance(n, ast.If) and ast.unparse(n.test) ==
                   'native_key is not None and native_key != observed_ready_key')
    sample = unique(lambda n: isinstance(n, ast.If) and ast.unparse(n.test) == 'sample_due')
    # Only the original sample cursor/cache writes affect the entry scheduler.
    # Neutral RSI/manual-position shadow work remains outside this adapter.
    if not (len(sample.body) >= 3 and ast.unparse(sample.body[0]) == 'last_sample = now'
            and ast.unparse(sample.body[1]) == 'observations.append((now, price))'
            and isinstance(sample.body[2], ast.Expr)
            and ast.unparse(sample.body[2].value.func) == 'save_snapshots'):
        raise ValueError('ORIGINAL_SAMPLE_CURSOR_SOURCE_CHANGED')
    sample.body = sample.body[:3]
    entry = unique(lambda n: isinstance(n, ast.If) and
                   ast.unparse(n.test) == 'sample_due or confirm_due or setup_due')
    nodes = [assignments['native_key'], ready] + [assignments[n] for n in
        ('sample_due', 'confirm_due', 'new_setup_bar', 'quote_setup_due', 'setup_due')] + [sample, entry]
    return ast.Module(body=nodes, type_ignores=[])


def schedule_definition(config):
    if not isinstance(config, dict) or set(config) != {'poll_seconds', 'delivery_model',
            'initial_last_sample', 'entry_mode', 'source_ast_sha256'}:
        raise ValueError('EXPLICIT_ORIGINAL_SCHEDULE_CONFIG_REQUIRED')
    value = copy.deepcopy(config)
    if (type(value['poll_seconds']) is not int or value['poll_seconds'] < 30
            or value['delivery_model'] not in ('modeled_success', 'modeled_failure')
            or type(value['initial_last_sample']) not in (int, float)
            or value['initial_last_sample'] < 0 or value['entry_mode'] != 'capture'):
        raise ValueError('INVALID_ORIGINAL_SCHEDULE_CONFIG')
    actual = hashlib.sha256(ast.dump(source_slice(), include_attributes=False).encode()).hexdigest()
    if value['source_ast_sha256'] != actual:
        raise ValueError('ORIGINAL_SCHEDULE_SOURCE_CHANGED')
    return value


def config(*, poll_seconds, initial_last_sample=0, delivery_model='modeled_success'):
    return schedule_definition(dict(poll_seconds=poll_seconds, initial_last_sample=initial_last_sample,
        delivery_model=delivery_model, entry_mode='capture', source_ast_sha256=hashlib.sha256(
            ast.dump(source_slice(), include_attributes=False).encode()).hexdigest()))


def runtime_state(settings):
    return dict(last_sample=settings['initial_last_sample'],last_setup_key=None,
        observed_ready_key=None,first_ready_observed_at=None,watch=None,
        alert_state=dict(day=None,count=0,last_time=None,last_side=None),
        observations=[],last_entry_diagnostics={})


class OriginalLiveComparison(ContinuousComparison):
    def __init__(self, book, manifest, **kwargs):
        if manifest.get('schedule') != SCHEDULE:
            raise ValueError('ORIGINAL_LIVE_SCHEDULE_DECLARATION_REQUIRED')
        # E3's intentional scheduling change needs its own explicit adapter.
        # This original-scheduler comparison cannot silently pretend it ran it.
        if any(p['confirmation_on_discovery'] or p['e5'] for p in manifest['policies']):
            raise ValueError('POLICY_REQUIRES_EXPLICIT_EXPERIMENTAL_SCHEDULE')
        self.code = compile(source_slice(), str(SOURCE), 'exec')
        self.settings = schedule_definition(manifest['schedule_config'])
        super().__init__(book, manifest, **kwargs)

    def _step_policy(self, spec, current, point):
        name, at, state = self.name(spec), point['observed_at'], point['native_state']
        current['observations'] += 1
        result = dict(policy=name,seq=point['seq'],at=at,
            source_observation_sha256=digest(point['source_observation']),
            qualification=self.qualification(point),phases=[],no_entry=True)
        if self.manifest['purpose'] == 'unseen':
            current['unavailable'] += 1
            result.update(status='input_unavailable',reason='source_qualification_unavailable',
                          modeled_net_usdt=None,valid_interval=False)
            return result
        cls = BASE_CLASSES[spec['index']]
        trace = cls(self.book,'capture',self.identity,0,self.manifest['costs'])
        trace.policy,trace.now,trace.state = name,at,state
        trace.quote_source = copy.deepcopy(point['source_observation'])
        trace.quote_source.setdefault('logged_at',replay.iso(at))
        for field in ('invalidated_events','e5_invalidated'):
            if hasattr(trace,field):setattr(trace,field,set(current[field]))
        saved = copy.deepcopy(current.get('original_live_runtime',runtime_state(self.settings)))
        saved['observations'] = deque(saved['observations'],maxlen=360)
        for field in ('last_setup_key','observed_ready_key'):
            if saved[field] is not None:saved[field] = tuple(saved[field])
        events, calls, frozen, counters = [], [], [], Counter()
        def virtual(kind, **data):
            events.append(dict(kind=kind,**copy.deepcopy(data)))
        def send(message):
            virtual('modeled_delivery',message_sha256=hashlib.sha256(message.encode()).hexdigest(),
                    actual_send=False,provider_acknowledged=False,
                    modeled_success=self.settings['delivery_model']=='modeled_success')
            return self.settings['delivery_model']=='modeled_success'
        def observe(adapter,kind,**data):
            # Keep exact call boundaries and data hashes; the real live sidecar
            # envelope code was independently tested in the preceding unit.
            key=digest(dict(kind=kind,data=data));virtual(kind,record_hash=key)
            if kind=='frozen_plan':
                replay.compact_plan(data['plan'])
                card=copy.deepcopy(trace.cards[-1])
                card['outcome']=dict(status='unresolved',net_pnl_model=None,
                                     reason='followup_not_yet_observed')
                self.book.outcome(card['plan_id'],card['outcome'])
                frozen.append(card)
            return key
        def journal_sample(*args,**kwargs):
            if not point.get('journal_available',True):raise RuntimeError('DECLARED_JOURNAL_FAILURE')
            virtual('journal_sample',args_sha256=digest(args),decision_sha256=digest(kwargs))
        def added(plan,now):
            virtual('modeled_tracking_registration',plan_sha256=digest(plan))
            return SimpleNamespace(number=len(current['plans'])+len(frozen),sent_at=now)
        with ExitStack() as hooks:
            for key,attribute in replay.CONFIG_MAP.items():
                hooks.enter_context(replay.patch.object(replay.monitor,attribute,self.manifest['costs'][key]))
            hooks.enter_context(replay.patch.object(replay.monitor,'ENTRY_REFINEMENT_MODE','capture'))
            trace.patches(hooks)
            # The selected original statements execute without text rewriting.
            # All external writes/deliveries are explicit local model ports.
            namespace = dict(vars(replay.monitor))
            namespace.update(saved)
            namespace.update(now=at,price=point['quote'],native_state=state,
                native_diag=copy.deepcopy(point.get('native_diagnostics',{})),POLL=self.settings['poll_seconds'],
                performance=SimpleNamespace(sample=journal_sample,add=lambda *a,**kw:virtual('journal_add')),
                audit_ready=point.get('journal_available',True),live_evidence=ADAPTER,
                quote_evidence=dict(record_hash=point.get('quote_record_hash')),
                market_context=copy.deepcopy(point.get('market_context',{})),
                pending_journal=[],count_event=lambda key:counters.update([key]),
                telegram=send,TELEGRAM_DELIVERY=SimpleNamespace(last_class='offline_model_no_transport'),
                time=SimpleNamespace(time=lambda:at),observe_evidence=observe,
                save_snapshots=lambda *a,**kw:virtual('sample_cache_write'),
                risk_tracker=SimpleNamespace(next_number=len(current['plans'])+1,add_sent_signal=added))
            # Record actual top-level call order in addition to unchanged
            # per-helper execution traces and P0 decision records.
            for fn in ('classify','confirm_watch','alert_gate'):
                original=namespace[fn]
                def called(*args,_fn=original,_name=fn,**kwargs):
                    calls.append(_name)
                    return _fn(*args,**kwargs)
                namespace[fn]=called
            exec(self.code,namespace,namespace)
        for field in ('invalidated_events','e5_invalidated'):
            if hasattr(trace,field):current[field]=sorted(getattr(trace,field))
        runtime={k:copy.deepcopy(namespace[k]) for k in PERSIST}
        runtime['observations']=list(runtime['observations'])
        current['original_live_runtime']=runtime
        current['watch']=copy.deepcopy(runtime['watch']);current['alert']=copy.deepcopy(runtime['alert_state'])
        current['watches']+=counters['watched'];current['expired']+=counters['expired']
        current['plans'].extend(frozen)
        evaluated=namespace['sample_due'] or namespace['confirm_due'] or namespace['setup_due']
        valid=state is not None and namespace['audit_ready']
        if valid:current['valid']+=1
        else:current['unavailable']+=1
        result.update(status='evaluated' if evaluated else 'no_entry_evaluation_due',valid_interval=valid,
            modeled_net_usdt=0 if valid and not frozen else None,no_entry=not frozen,
            plan_ids=[c['plan_id'] for c in frozen],
            schedule=dict((k,namespace[k]) for k in ('sample_due','confirm_due','new_setup_bar','quote_setup_due','setup_due')),
            actual_policy_call_order=calls,events=events,counters=dict(counters),
            no_signal_interval=not frozen,phases=copy.deepcopy(trace.decisions),
            declared_delivery_model=self.settings['delivery_model'],
            full_live_network_or_followup_loop_executed=False,actual_outbound=0)
        return result
