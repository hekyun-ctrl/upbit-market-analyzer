"""Predeclared E5 research-only sweep/reclaim route, no live activation.

A ten-minute level is known before its sweep. The first completed recovery
fixes the small opposite trigger. Macro trend/RSI are observed explanations;
data, chase, structure, cost, target, risk and policy scheduling remain hard.
"""
import copy
import math
from contextlib import contextmanager
from unittest.mock import patch

from lbank_entry_location import valid_minutes
from lbank_signal_evidence import digest, canonical, EvidenceLedger
from research import compare_barrier_chase_experiment as baseline

evidence, replay = baseline.evidence, baseline.replay
POLICY = 'p2_e5_observed_sweep_first_reclaim_v1'
STRATEGY = 'early_reversal'


class CachedResearchLedger(EvidenceLedger):
    """Deduplicate immutable source imports, preserving receipt revision checks.

    This only skips repeated SQL writes of already identical source/input sets.
    All decisions still execute the original full future/receipt validation.
    No source/decision/delivery schema, ordering or transaction is changed.
    """
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs);self._sources={};self._input_sets={}

    def source(self,raw,*,event_time,received_at=None,quality=None):
        key=digest(raw);metadata=canonical(dict(event_time=event_time,received_at=received_at,quality=quality))
        if key in self._sources:
            if self._sources[key]!=metadata:raise ValueError('SOURCE_RECEIPT_REVISION_REQUIRES_VERSION')
            return key
        result=super().source(raw,event_time=event_time,received_at=received_at,quality=quality)
        self._sources[key]=metadata;return result

    def inputs(self,hashes):
        identity=tuple(sorted(set(hashes)))
        if identity not in self._input_sets:self._input_sets[identity]=super().inputs(hashes)
        return self._input_sets[identity]


def available_minutes(state, now, delay=0):
    try:
        valid = valid_minutes(state)
        if valid is None or not math.isfinite(now) or not math.isfinite(delay) or delay < 0:
            return None
        bars, atr = valid
        if any(b['start']+60+delay > now or
               (b.get('received_at') is not None and (not math.isfinite(b['received_at']) or b['received_at'] > now)) for b in bars):
            return None
        return bars, atr
    except (KeyError, TypeError, ValueError, OverflowError):
        return None


def detect_reversals(state, now, delay=0, diagnostics=None):
    """Strict completed recovery then actual quote break, both directions.

    No state/bar mutation. Repeat evaluation retains the same level+sweep
    identity and first-reclaim trigger, not a newly favorable recovery extreme.
    """
    valid = available_minutes(state, now, delay)
    if valid is None:
        if diagnostics is not None:diagnostics.update(policy=POLICY, reason='e5_invalid_or_unavailable_native_minutes')
        return []
    bars, atr = valid
    proposals, sides = [], {}
    for d in (1, -1):
        key = 'low' if d == 1 else 'high'
        opposite = 'high' if d == 1 else 'low'
        reason = 'e5_no_recent_sweep_reclaim'
        # Earliest qualifying sweep wins, with <=3 closed recovery bars.
        for i in range(len(bars)-3, len(bars)):
            history = bars[i-10:i]
            source = min(history, key=lambda b:(d*b[key], -b['start']))
            level = source[key]
            if d*(bars[i][key]-level) >= 0:continue
            reclaim = next((j for j in range(i,len(bars)) if
                d*(bars[j]['close']-level) > 0 and
                d*(bars[j]['close']-bars[j]['open']) > 0 and
                d*(bars[j]['close']-bars[j-1]['close']) > 0), None)
            if reclaim is None:
                reason = 'e5_recovery_not_completed';continue
            anchor = min(d*b[key] for b in bars[i:reclaim+1])*d
            trigger = bars[reclaim][opposite]
            later = bars[reclaim+1:]
            if (any(d*(b['close']-level) <= 0 or d*(b[key]-anchor) <= 0 for b in later)
                    or d*(state['quote']-anchor) <= 0):
                reason = 'e5_first_reclaim_invalidated';continue
            if d*(state['quote']-trigger) <= 0:
                reason = 'e5_small_opposite_trigger_not_crossed';continue
            expiry = bars[i]['start']+60+3*60
            if now > expiry:
                reason = 'e5_sweep_expired';continue
            proof = dict(policy=POLICY, direction=d, level=level, structure_anchor=anchor,
                level_source=copy.deepcopy(source), sweep_source=copy.deepcopy(bars[i]),
                recovery_source=copy.deepcopy(bars[reclaim]), trigger=trigger,
                expires_at=expiry, atr1_at_detection=atr, known_at=None, actual_received_at=None,
                nominal_known_at=bars[reclaim]['start']+60+delay,
                source='completed_native_1m_reclaim_actual_quote_break',
                timing_basis='retained_quote_proxy_nominal_bar_scenario')
            proposals.append(dict(strategy=STRATEGY,direction=d,level=level,
                structure_anchor=anchor,_p2_e5_detection=proof))
            reason='e5_reclaim_triggered';break
        sides[str(d)] = reason
    if diagnostics is not None:diagnostics.update(policy=POLICY,sides=sides,proposals=len(proposals))
    return proposals


def frozen_recovery(state, watch, now, delay=0):
    """Enforce frozen structure, including first post-watch bar and equality.

    Terminal failures never erase their first original stop touch. Trigger
    temporarily not crossed is nonterminal; expired or broken structure is not.
    """
    proof=watch.get('_p2_e5_detection')
    result=dict(policy=POLICY,frozen=copy.deepcopy(proof),actual_received_at=None)
    try:
        valid=available_minutes(state,now,delay)
        if valid is None or not proof or proof['policy']!=POLICY:
            return False,'e5_invalid_or_unavailable_native_minutes',result,True
        bars,_=valid; d=watch['direction'];q=state['quote']
        if d not in (-1,1) or d != proof['direction'] or not all(math.isfinite(x) and x>0 for x in
            (q,proof['level'],proof['trigger'],proof['structure_anchor'])):
            return False,'e5_invalid_evidence',result,True
        if now > proof['expires_at']:return False,'e5_sweep_expired',result,True
        if (proof['nominal_known_at']>now or not any(b==proof['recovery_source'] for b in bars)):
            return False,'e5_frozen_recovery_unavailable',result,True
        after=[b for b in bars if b['start']>proof['recovery_source']['start']]
        key='low' if d==1 else 'high'
        stop=watch.get('stop',proof['structure_anchor'])
        if not math.isfinite(stop) or stop<=0 or d*(q-stop)<=0 or d*(q-proof['structure_anchor'])<=0:
            return False,'e5_original_stop_or_extreme_crossed',result,True
        if any(d*(b['close']-proof['level'])<=0 or d*(b[key]-proof['structure_anchor'])<=0 or
               d*(b[key]-stop)<=0 for b in after):
            return False,'e5_first_reclaim_invalidated',result,True
        if d*(q-proof['trigger'])<=0:return False,'e5_small_opposite_trigger_not_crossed',result,False
        return True,'e5_frozen_recovery_intact',result,False
    except (KeyError,TypeError,ValueError,IndexError,OverflowError):
        return False,'e5_invalid_evidence',result,True


def canonical_anchor(proof, delay):
    b=proof['level_source'];s=proof['sweep_source'];d=proof['direction']
    return dict(bar_hash=digest(dict(level_source=b,sweep_source=s)),
        bar_hash_basis='composite_exact_level_and_sweep_sources',bar_start=b['start'],period=60,
        field='low' if d==1 else 'high',price=proof['level'],
        sweep_hash=digest(s),sweep_start=s['start'],known_at=None,
        nominal_available_at=s['start']+60+delay,
        timing_basis='nominal_scenario_not_observed_receipt')


class E5Mixin:
    def __init__(self,*args,**kwargs):
        super().__init__(*args,**kwargs)
        self.policy+='_e5';self.e5_scope=False;self.e5_detection=None
        self.e5_readiness=None;self.e5_invalidated=set()

    def classify(self,original,prices,*args,**kwargs):
        state=kwargs['native_state'];detector=replay.monitor.detect_structures
        def injected(*a,**kw):
            capture=list(detector(*a,**kw));diag={}
            # classify appends fresh breakout proposals after detect_structures.
            # Put byte-identical fresh proposals before E5 here; classify's
            # existing (strategy,direction) dedupe ignores its later duplicates.
            # This keeps every original capture proposal ahead of the new route.
            fresh=replay.monitor.breakout_evidence(prices,state,replay.monitor.ema,adaptive=True)
            for d,key,level in ((1,'breakout_long','prior_high'),(-1,'breakout_short','prior_low')):
                if fresh[key]:capture.append(dict(strategy='breakout',direction=d,level=fresh[level]))
            proposals=detect_reversals(state,self.now,self.delay,diag)
            if kw.get('diagnostics') is not None:kw['diagnostics']['e5_detection']=diag
            return capture+proposals
        with patch.object(replay.monitor,'detect_structures',injected):
            result=super().classify(original,prices,*args,**kwargs)
        scan=kwargs.get('diagnostics',{}).get('market',{}).get('e5_detection')
        self.record(None,'observed',phase='e5_structure_scan',
            e5_scan=copy.deepcopy(scan),scan_status='evaluated' if scan else 'not_evaluated_preempted_by_existing_input_or_chase_gate')
        return result

    @contextmanager
    def scope(self,setup):
        previous=(self.e5_scope,self.e5_detection)
        self.e5_scope=setup.get('strategy')==STRATEGY
        self.e5_detection=copy.deepcopy(setup.get('_p2_e5_detection')) if self.e5_scope else None
        try:yield
        finally:self.e5_scope,self.e5_detection=previous

    def gate(self,name,original,*args,**kwargs):
        if self.e5_scope and name in ('context','rsi'):
            result=original(*args,**kwargs)
            explanatory={'avoid_15m_strong_countertrend','5m_15m_opposed_to_entry',
                         '5m_rsi_direction_mismatch_or_flat'}
            if self.active is not None:
                self.active['gates'][name]=dict(status='passed' if result[0] else 'failed',
                    reason=result[1],enforced=(not result[0] and result[1] not in explanatory),
                    role='explanatory_E5_only' if result[0] or result[1] in explanatory else 'retained_hard_data_or_chase')
            if not result[0] and result[1] not in explanatory:return result
            return (True,'e5_explanatory_'+result[1],*result[2:])
        return super().gate(name,original,*args,**kwargs)

    def assess(self,original,prices,d,setup,*args,**kwargs):
        anchor=evidence.anchor_for
        def e5_anchor(candidate,state,delay):
            if candidate.get('strategy')==STRATEGY:
                return canonical_anchor(candidate['_p2_e5_detection'],delay),STRATEGY
            return anchor(candidate,state,delay)
        with self.scope(setup),patch.object(evidence,'anchor_for',e5_anchor):
            result=super().assess(original,prices,d,setup,*args,**kwargs)
            if result and self.e5_detection:result['_p2_e5_detection']=copy.deepcopy(self.e5_detection)
            return result

    def confirm(self,original,prices,watch,age,*args,**kwargs):
        self.e5_readiness=None
        if watch.get('strategy')!=STRATEGY:
            return super().confirm(original,prices,watch,age,*args,**kwargs)
        def checked(*a,**kw):
            ok,reason,proof,terminal=frozen_recovery(kw['native_state'],watch,self.now,self.delay)
            self.e5_readiness=proof
            if not ok:
                if terminal and watch.get('_evidence_event_id'):self.e5_invalidated.add(watch['_evidence_event_id'])
                kw['diagnostics'].update(reason=reason,cancel_watch=terminal);return None
            return original(*a,**kw)
        with self.scope(watch):
            result=super().confirm(checked,prices,watch,age,*args,**kwargs)
            if result:result['_p2_e5_detection']=copy.deepcopy(self.e5_detection)
            return result

    def schedule(self,proposal):
        if proposal.get('_evidence_event_id') in self.e5_invalidated:
            self.record(proposal['_evidence_event_id'],'rejected','e5_event_previously_invalidated',phase='watch_scheduling')
            return None
        parent=getattr(super(),'schedule',None)
        return parent(proposal) if parent else proposal

    def record(self,*args,**kwargs):
        if kwargs.get('phase') in ('candidate','confirmation'):
            kwargs['e5_detection']=copy.deepcopy(self.e5_detection)
            kwargs['e5_readiness']=copy.deepcopy(self.e5_readiness) if self.e5_scope and kwargs.get('phase')=='confirmation' else None
        return super().record(*args,**kwargs)

    def patches(self,stack):
        super().patches(stack)
        structure=replay.monitor.confirm_structure
        def confirm(prices,watch,*args,**kwargs):
            if watch.get('strategy')==STRATEGY:
                return True  # checked frozen_recovery already ran in confirm
            return structure(prices,watch,*args,**kwargs)
        stack.enter_context(patch.object(replay.monitor,'confirm_structure',confirm))


BASE_CLASSES=(evidence.Trace,baseline.E3Trace,baseline.p1.P1Trace,baseline.E3P1Trace,
    baseline.E1Trace,baseline.E1E3Trace,baseline.E1P1Trace,baseline.E1E3P1Trace,
    baseline.E4Trace,baseline.E4E3Trace,baseline.E4P1Trace,baseline.E4E3P1Trace,
    baseline.E4E1Trace,baseline.E4E1E3Trace,baseline.E4E1P1Trace,baseline.E4E1E3P1Trace)
E5_CLASSES=tuple(type('E5'+cls.__name__,(E5Mixin,cls),{}) for cls in BASE_CLASSES)
