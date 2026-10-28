"""Bounded observational Python execution coverage, never a gate implementation.

Trace original functions, not copies/replays of decisions. Source lines and
call/return occurrences are facts; line reachability is NOT boolean passage.
Uncalled checks stay not_evaluated. No source/receipt/profit qualification.
"""
import ast
from contextlib import contextmanager
import hashlib
from pathlib import Path
import sys

from lbank_signal_evidence import canonical, digest, gate_vector

ROOT = Path(__file__).resolve().parent
GATE_CALLS = dict(momentum=('trend_retest_momentum_gate',),
    context=('native_context_gate', 'sampled_context_gate'),
    rsi=('five_minute_rsi_entry_gate',), confirmation=('confirm_watch',),
    plan=('calculate_plan',), alert_limits=('alert_gate',))
SOURCES = {}
NO_LINE_FUNCTIONS = frozenset(('ema','rsi','atr_wilder','wilder_rsi_series'))
COMPREHENSIONS = frozenset(('<listcomp>','<genexpr>','<dictcomp>','<setcomp>'))
CONFIG_FIELDS = ('ENTRY_REFINEMENT_MODE','ENTRY_MAX_CHASE_PCT','ENTRY_CONFIRM_MAX_AGE_SECONDS',
    'MIN_SAMPLES','MIN_NET_RR','TAKER_FEE','MAKER_FEE','STOP_SLIPPAGE','FUNDING_BUFFER',
    'REF_BALANCE','RISK_FRACTION','MAX_MARGIN_FRACTION','LEVERAGE','COOLDOWN',
    'REVERSAL_COOLDOWN','MAX_DAILY_ALERTS','RUNNER_ENABLED')
LOCAL_FIELDS = ('direction','stop_distance','minimum_stop','unit_loss','t1','t2','observed_range',
    'barrier','raw_qty','qty','margin','net_loss','net_gain1','net_gain2',
    'relative','age_seconds','minimum_confirmation_seconds','momentum_ok','context_ok',
    'allowed','quality_ok','valid')


def source_info(filename):
    if filename not in SOURCES:
        path = Path(filename)
        source = path.read_text()
        tree = ast.parse(source)
        functions = {}
        parents = {id(child):parent for parent in ast.walk(tree) for child in ast.iter_child_nodes(parent)}
        def return_context(node):
            conditions=[];child=node;parent=parents.get(id(child))
            while parent is not None and not isinstance(parent,(ast.FunctionDef,ast.AsyncFunctionDef)):
                if isinstance(parent,ast.If):
                    conditions.append(dict(condition=ast.get_source_segment(source,parent.test),
                        selected_branch='body' if child in parent.body else 'else'))
                child,parent=parent,parents.get(id(parent))
            return dict(statement=ast.get_source_segment(source,node),enclosing_conditions=conditions)
        for fn in ast.walk(tree):
            if not isinstance(fn, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            statements = [n for n in ast.walk(fn) if isinstance(n, ast.stmt)]
            functions[fn.lineno] = dict(name=fn.name,
                statement_lines=sorted(set(n.lineno for n in statements)),
                returns={str(n.lineno): return_context(n) for n in statements if isinstance(n,ast.Return)})
        SOURCES[filename] = dict(path=path.name, sha256=hashlib.sha256(source.encode()).hexdigest(), functions=functions)
    return SOURCES[filename]


def result_summary(value):
    if value is None or isinstance(value, (str, bool, int, float)):
        return dict(value=value)
    if isinstance(value, tuple) and value and type(value[0]) is bool:
        return dict(passed=value[0], reason=value[1] if len(value)>1 and isinstance(value[1],str) else None)
    # Do not serialize arbitrary frame locals, account state or credentials.
    summary=dict(type=type(value).__name__)
    try:
        payload=canonical(value)
        if len(payload)<512*1024:summary['sha256']=hashlib.sha256(payload.encode()).hexdigest()
    except (TypeError,ValueError):pass
    return summary


class ExecutionTrace:
    def __init__(self, function, *, max_calls=512, max_lines=12000):
        self.function = function
        self.max_calls, self.max_lines = max_calls, max_lines
        self.calls, self.frames, self.files = [], {}, {}
        self.line_events = 0
        self.complete, self.failure = True, None

    def callback(self, frame, event, value):
        try:
            filename = frame.f_code.co_filename
            if filename == __file__:
                return None
            if Path(filename).parent != ROOT or not Path(filename).name.startswith('lbank_'):
                return None
            code = frame.f_code
            if code.co_name in COMPREHENSIONS:
                return None
            if event == 'call':
                if len(self.calls) >= self.max_calls:
                    self.complete, self.failure = False, 'trace_call_cap'
                    return None
                info = source_info(filename)
                self.files[info['path']] = info['sha256']
                row = dict(call_id=len(self.calls), name=code.co_name, file=info['path'],
                    first_line=code.co_firstlineno, parent_call_id=self.frames.get(id(frame.f_back)),
                    executed_lines=[], return_line=None, result=None, completed=False)
                if code.co_name in NO_LINE_FUNCTIONS:
                    frame.f_trace_lines = False
                    row['line_coverage_unavailable_reason'] = 'indicator_calculation; call_and_return_recorded'
                self.frames[id(frame)] = row['call_id']; self.calls.append(row)
            elif id(frame) in self.frames:
                row = self.calls[self.frames[id(frame)]]
                if event == 'line':
                    self.line_events += 1
                    if self.line_events > self.max_lines:
                        self.complete, self.failure = False, 'trace_line_cap'
                        return None
                    if frame.f_lineno not in row['executed_lines']:
                        row['executed_lines'].append(frame.f_lineno)
                elif event == 'return':
                    row.update(return_line=frame.f_lineno, result=result_summary(value), completed=True)
                    row['evaluated_values']={k:frame.f_locals[k] for k in LOCAL_FIELDS
                        if k in frame.f_locals and type(frame.f_locals[k]) in (int,float,str,bool,type(None))}
                    setup=frame.f_locals.get('setup') or frame.f_locals.get('watch')
                    if isinstance(setup,dict):row['strategy']=setup.get('strategy','breakout')
                    structural=frame.f_locals.get('structural')
                    if isinstance(structural,dict):row['structural_status']=structural.get('status')
                    diagnostic = frame.f_locals.get('diagnostics')
                    if isinstance(diagnostic,dict) and isinstance(diagnostic.get('reason'),str):
                        row['decision_reason'] = diagnostic['reason']
                    self.frames.pop(id(frame),None)
            return self.callback
        except Exception as exc:
            self.complete, self.failure = False, 'trace_callback_'+type(exc).__name__
            return None

    @contextmanager
    def recording(self):
        previous = sys.gettrace()
        if previous is not None:
            self.complete, self.failure = False, 'existing_thread_tracer'
            yield
            return
        sys.settrace(self.callback)
        try:
            yield
        finally:
            sys.settrace(previous)

    def report(self, context):
        if not self.calls and self.complete:
            self.complete,self.failure=False,'no_original_policy_frames_observed'
        gates = gate_vector()
        for gate, names in GATE_CALLS.items():
            calls = [c for c in self.calls if c['name'] in names]
            if not calls:
                gates[gate].update(reason='control_flow_did_not_reach_gate_call' if self.complete else 'trace_incomplete_not_observed',
                                   scope=self.function.__name__)
                continue
            results = []
            for call in calls:
                result = call['result'] or {}
                passed = result.get('passed')
                if call['name']=='alert_gate' and 'value' in result:
                    passed = result['value']=='ok'
                elif call['name'] in ('calculate_plan','confirm_watch') and call['completed']:
                    passed = result.get('type')=='dict'
                results.append(dict(call_id=call['call_id'], passed=passed,
                    reason=call.get('decision_reason', result.get('reason',result.get('value')))))
            gates[gate] = dict(status='executed', calls=results,
                passage_basis='returned_result_not_recomputed; overrides_and_selection_preserved')
        coverage = []
        for c in self.calls:
            info = source_info(str(ROOT/c['file']))['functions'].get(c['first_line'])
            if info and c['name'] not in NO_LINE_FUNCTIONS:
                returned=info['returns'].get(str(c['return_line']))
                c['return_source'] = returned['statement'] if returned else None
                c['return_context'] = returned
                coverage.append(dict(call_id=c['call_id'],
                    not_reached_statement_lines=sorted(set(info['statement_lines'])-set(c['executed_lines'])),
                    status='not_evaluated', reason='actual_control_flow; see return_site_and_decision_reason'))
        return dict(schema='actual_python_gate_execution_v1', complete=self.complete,
            unavailable_reason=self.failure, function=self.function.__name__,
            snapshot_sha256=digest(context), source_sha256=self.files,
            calls=self.calls, coverage=coverage, gates=gates,
            branch_boolean_values_available=False,
            excluded_line_scopes=sorted(NO_LINE_FUNCTIONS|COMPREHENSIONS),
            line_semantics='source_line_reached_only; never imply every short_circuit_operand_evaluated',
            quote_receipt_verified=False, full_input_receipt_coverage_verified=False)


@contextmanager
def record_call(adapter, function, *args, trace_context=None, trace_diagnostics=None, **kwargs):
    """Observe a direct original call; failed tracing never retries the policy."""
    if not getattr(adapter,'enabled',False):
        yield
        return
    diagnostics = trace_diagnostics if trace_diagnostics is not None else kwargs.get('diagnostics')
    recorder = ExecutionTrace(function)
    call_input=None
    try:
        call_input=dict(args_sha256=[digest(a) for a in args],
            kwargs_sha256={k:digest(v) for k,v in kwargs.items() if k!='diagnostics'})
        module=sys.modules.get('lbank_futures_monitor')
        call_input['configuration']={k:getattr(module,k) for k in CONFIG_FIELDS if hasattr(module,k)}
        call_input['configuration_sha256']=digest(call_input['configuration'])
    except Exception:
        recorder.complete,recorder.failure=False,'call_input_hash_unavailable'
    with recorder.recording():
        yield
    try:
        report = recorder.report(trace_context or {})
        report['call_input']=call_input
        if isinstance(diagnostics,dict):
            diagnostics['execution_trace'] = report
    except Exception:
        if isinstance(diagnostics,dict):
            diagnostics['execution_trace'] = dict(schema='actual_python_gate_execution_v1',
                complete=False, unavailable_reason='trace_report_failed')


def evaluate(adapter, function, *args, trace_context=None, trace_diagnostics=None, **kwargs):
    with record_call(adapter,function,*args,trace_context=trace_context,
                     trace_diagnostics=trace_diagnostics,**kwargs):
        return function(*args,**kwargs)


def validated_trace(trace, context):
    if not trace:
        return None
    if (trace.get('schema') != 'actual_python_gate_execution_v1'
            or not isinstance(trace.get('calls'),list)
            or trace.get('snapshot_sha256') != digest(context)):
        raise ValueError('EXECUTION_TRACE_SNAPSHOT_MISMATCH')
    return trace
