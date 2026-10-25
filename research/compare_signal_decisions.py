"""P0 evidence adapter: replay frozen policies, add no strategy and send nothing.

Retained windows are development data. Receipt times absent from an archive stay
NULL. Policy clocks/cooldowns are independently replayed by the existing runner.
"""
import copy
import csv
import gzip
import hashlib
import json
import logging
import math
import re
import sys
import tempfile
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from research import compare_entry_refinement as replay
from lbank_signal_evidence import EvidenceLedger, canonical, digest, gate_vector, pair_plans
from lbank_entry_quality import confirmed_pivot_barriers

OUT=ROOT/'research/zerobase/trade_evidence_p0_20261025'
SAVED=ROOT/'research/zerobase/entry_location_20261025/comparison.json'
SOURCE_VERSION='ce33a017ff6aca0729175a8f88c3ec978f4a84a2'


def anchor_for(setup,state,delay):
    d=setup['direction'];family=setup.get('strategy','breakout')
    minutes=state['minute_bars'];proof=setup.get('entry_location')
    if proof:
        key='high' if d==1 else 'low';level=proof['trigger'];family='breakout_level'
        rows=[b for b in minutes if b['start']<proof['pattern_start']][-10:]
    elif family=='breakout':
        key='high' if d==1 else 'low';level=setup.get('level');family='breakout_level'
        rows=minutes[-16:-1]
    else:
        key='low' if d==1 else 'high';level=setup.get('structure_anchor')
        rows=minutes[-6:-1]
    candidates=[b for b in rows if level is not None and abs(b[key]-level)<1e-7]
    if not candidates:return None,family
    # Exact source occurrence, not a price/time-nearest match across policies.
    bar=max(candidates,key=lambda b:b['start'])
    return dict(bar_hash=digest(bar),bar_start=bar['start'],period=60,
                field=key,price=level,known_at=None,
                nominal_available_at=bar['start']+60+delay,
                timing_basis='nominal_scenario_not_observed_receipt'),family


def barrier_at(state,direction,price,delay):
    pivots=confirmed_pivot_barriers(state['five_bars'],direction,price)
    if not pivots:return dict(status='unknown',price=None,known_at=None)
    nearest=min(pivots,key=lambda p:direction*p['price'])
    i=next(i for i,b in enumerate(state['five_bars']) if b['start']==nearest['start'])
    right=state['five_bars'][i+2]
    return dict(status='observed_pivot_not_reachability_forecast',price=nearest['price'],
                pivot_start=nearest['start'],right_confirmation_start=right['start'],
                known_at=None,nominal_known_at=right['start']+300+delay,
                right_bar_hash=digest(right),timing_basis='nominal_scenario')


class TraceLog:
    def __init__(self,trace):self.trace=trace
    def info(self,fmt,*args,**kwargs):
        if self.trace.active is None:return
        text=fmt%args if args else fmt
        match=re.search(r'PLAN REJECT reason=(\w+)',text)
        if match:self.trace.active['plan_reject_detail']=match.group(1)
    warning=error=exception=debug=info


class Trace:
    def __init__(self,ledger,mode,window,delay,costs):
        self.ledger,self.mode,self.window,self.delay,self.costs=ledger,mode,window,delay,costs
        self.policy=mode;self.active=None;self.state=None;self.now=None;self.input_hash=None
        self.decisions=[];self.observations=[];self.cards=[];self.first_candidates={};self.coverage=[]
        self.quote_source=None

    def inputs(self,state,now):
        hashes=[]
        for period,name in ((60,'minute_bars'),(300,'five_bars')):
            for b in state[name]:
                if b['start']+period+self.delay>now:raise ValueError('FUTURE_BAR_IN_POLICY_STATE')
                if b.get('received_at') is not None and b['received_at']>now:
                    raise ValueError('FUTURE_SOURCE_RECEIPT')
                hashes.append(self.ledger.source(b,event_time=b['start']+period,
                    received_at=b.get('received_at'),
                    quality='stream_inferred_closed; receipt_and_volume_unit_unverified'))
        if self.quote_source is None:raise ValueError('QUOTE_SOURCE_REQUIRED')
        hashes.append(self.ledger.source(self.quote_source,event_time=now,
                                         quality='retained_quote_log_observation_proxy'))
        return self.ledger.inputs(hashes)

    def record(self,event,state,reason=None,gates=None,**extra):
        row=dict(event_id=event,policy=self.policy,evaluated_at=self.now,state=state,
                 input_hash=self.input_hash,first_reject=reason,gates=copy.deepcopy(gates or gate_vector()),**extra)
        self.ledger.decision(event=event,policy=self.policy,evaluated_at=self.now,
            input_hash=self.input_hash,state=state,first_reject=reason,gates=row['gates'],
            available_at=self.now,availability_basis='nominal_scenario',window=self.window,
            nominal_delay_seconds=self.delay,policy_definition_commit=SOURCE_VERSION,
            code_config_hash=digest(dict(version=SOURCE_VERSION,mode=self.mode,costs=self.costs)),
            evaluated_at_source_text=self.quote_source['logged_at'] if self.quote_source else None,
            **extra)
        self.decisions.append(row)

    def classify(self,original,prices,*args,**kwargs):
        state=kwargs['native_state'];now=self.now
        self.state=state;self.input_hash=self.inputs(state,now)
        result=original(prices,*args,**kwargs)
        diag=kwargs.get('diagnostics',{})
        self.observations.append(dict(policy=self.policy,window=self.window,time=now,
                                      reason=diag.get('reason'),input_hash=self.input_hash))
        if not diag.get('candidates'):
            self.record(None,'observed',diag.get('reason','no_structure'),
                        no_structure=diag.get('reason')=='market_no_setup')
        return result

    def assess(self,original,prices,d,setup,*args,**kwargs):
        state=kwargs['native_state'];anchor,family=anchor_for(setup,state,self.delay)
        event=(self.ledger.opportunity(d,family,anchor,first_observed_at=self.now) if anchor else None)
        active=dict(event_id=event,gates=gate_vector());before=self.active;self.active=active
        try:result=original(prices,d,setup,*args,**kwargs)
        finally:self.active=before
        diag=kwargs.get('diagnostics',{});reason=diag.get('reason','unknown')
        if active['gates']['momentum']['status']=='failed' and reason!='retest_momentum':
            active['gates']['momentum']['override_used']=True
        self.record(event,'qualified' if result else 'rejected',None if result else reason,
                    gates=active['gates'],phase='candidate',strategy=setup.get('strategy'),
                    direction=d,quote=state['quote'],anchor=anchor,plan_reject_detail=active.get('plan_reject_detail'))
        if event:self.first_candidates.setdefault(event,self.now)
        if result:
            result.update(_evidence_event_id=event,_evidence_anchor=anchor,
                          _evidence_candidate_at=self.now,
                          _evidence_candidate_source_time=self.quote_source['logged_at'])
        return result

    def gate(self,name,original,*args,**kwargs):
        result=original(*args,**kwargs)
        if self.active is not None:
            self.active['gates'][name]=dict(status='passed' if result[0] else 'failed',reason=result[1])
        return result

    def plan(self,original,*args,**kwargs):
        result=original(*args,**kwargs)
        if self.active is not None:
            self.active['gates']['plan']=dict(status='passed' if result else 'failed',
                reason=self.active.get('plan_reject_detail') if not result else None,
                subchecks=('risk_margin_net_rr_passed' if result else 'unlogged_subchecks_not_evaluated_or_unknown'))
        return result

    def confirm(self,original,prices,watch,age,*args,**kwargs):
        state=kwargs['native_state'];self.state=state;self.input_hash=self.inputs(state,self.now)
        before=self.active;self.active=dict(event_id=watch.get('_evidence_event_id'),gates=gate_vector())
        active=self.active
        try:result=original(prices,watch,age,*args,**kwargs)
        finally:self.active=before
        reason=kwargs.get('diagnostics',{}).get('reason','unknown')
        if active['gates']['momentum']['status']=='failed' and reason!='retest_momentum':
            active['gates']['momentum']['override_used']=True
        active['gates']['confirmation']=dict(status='passed' if result else 'failed',reason=reason)
        self.record(watch.get('_evidence_event_id'),'qualified' if result else 'rejected',
                    None if result else reason,gates=active['gates'],phase='confirmation',
                    age_seconds=age,plan_reject_detail=active.get('plan_reject_detail'))
        if result:
            for key in ('_evidence_event_id','_evidence_anchor','_evidence_candidate_at','_evidence_candidate_source_time'):
                result[key]=watch.get(key)
            result['_evidence_confirmed']=True
        return result

    def alert_gate(self,original,now,side,state,plan=None):
        result=original(now,side,state,plan=plan)
        gates=gate_vector();gates['alert_limits']=dict(status='passed' if result=='ok' else 'failed',reason=result)
        self.record(plan.get('_evidence_event_id'),'qualified' if result=='ok' else 'rejected',
            None if result=='ok' else result,gates=gates,phase='alert_eligibility',
            modeled_confirmation=bool(plan.get('_evidence_confirmed')))
        return result

    def compact(self,original,plan):
        result=original(plan)
        # Preserve PR #21's existing explanatory output fields as well as
        # its economic values when checking the frozen per-window result.
        result.update(entry_location=plan.get('entry_location'),
                      location_evidence=plan.get('location_evidence'))
        if not plan.get('_evidence_confirmed'):return result
        frozen={k:v for k,v in plan.items() if not k.startswith('_evidence_')}
        barrier=barrier_at(self.state,plan['direction'],plan['price'],self.delay)
        provenance=dict(input_hash=self.input_hash,anchor=plan.get('_evidence_anchor'),
            candidate_at=plan.get('_evidence_candidate_at'),confirm_at=self.now,
            candidate_at_source_text=plan.get('_evidence_candidate_source_time'),
            confirm_at_source_text=self.quote_source['logged_at'],
            known_at=None,send_requested_at=None,telegram_ack_at=None,
            receipt_status='not_available',timing_basis='retained_quote_log_proxy_and_nominal_bar_scenario',
            nearest_barrier=barrier,target1_basis='cost_solved_required_price_not_structural_target',
            costs=self.costs,commit=SOURCE_VERSION)
        identity=self.ledger.freeze(event=plan.get('_evidence_event_id'),policy=self.policy,
                                    plan=frozen,provenance=provenance,intent=False)
        card=dict(plan_id=identity,event_id=plan.get('_evidence_event_id'),policy=self.policy,
                  window=self.window,plan=frozen,provenance=provenance)
        self.cards.append(card)
        return dict(result,evidence_plan_id=identity,evidence_event_id=card['event_id'])

    def patches(self,stack):
        m=replay.monitor
        for name,method in (('classify',self.classify),('assess_candidate',self.assess),
                            ('confirm_watch',self.confirm),('calculate_plan',self.plan),
                            ('alert_gate',self.alert_gate)):
            original=getattr(m,name)
            stack.enter_context(patch.object(m,name,lambda *a,_fn=original,_method=method,**kw:_method(_fn,*a,**kw)))
        for name,gate in (('trend_retest_momentum_gate','momentum'),('native_context_gate','context'),
                          ('five_minute_rsi_entry_gate','rsi')):
            original=getattr(m,name)
            stack.enter_context(patch.object(m,name,lambda *a,_fn=original,_gate=gate,**kw:self.gate(_gate,_fn,*a,**kw)))
        compact=replay.compact_plan
        stack.enter_context(patch.object(replay,'compact_plan',lambda plan:self.compact(compact,plan)))
        stack.enter_context(patch.object(m,'LOG',TraceLog(self)))


def horizon_results(plan,points):
    at=plan['provenance']['confirm_at'];p=plan['plan'];result={}
    for minutes in (5,15,30,60):
        eligible=sorted((x for x in points if at+60*minutes<=replay.timestamp(x['logged_at'])<=at+60*minutes+90),key=lambda x:x['logged_at'])
        if not eligible:result[str(minutes)]=dict(status='unknown',reason='no_observation_within_90s')
        else:
            q=eligible[0]
            result[str(minutes)]=dict(status='unmanaged_mark_to_horizon_model',observed_at=q['logged_at'],price=q['price'],
                net_pnl_model=p['quantity_btc']*replay.monitor.net_pnl_per_btc(p['direction'],p['price'],q['price']))
    return result


def first_contact(card,bars):
    p=card['plan'];at=card['provenance']['confirm_at'];d=p['direction']
    first=int(at//300)*300
    for start in range(first,int(at+21600)//300*300,300):
        b=bars.get(start)
        if (b is None or b.get('quality')!='stream_closed'
                or any(not isinstance(b.get(k),(int,float)) or not math.isfinite(b[k]) for k in ('open','high','low','close'))
                or min(b[k] for k in ('open','high','low','close'))<=0
                or b['low']>min(b['open'],b['close']) or b['high']<max(b['open'],b['close'])):
            return dict(status='unknown',reason='missing_outcome_bar',at=replay.iso(start),tp1_before_sl=None)
        stop=b['low']<=p['stop'] if d==1 else b['high']>=p['stop']
        target=b['high']>=p['target1'] if d==1 else b['low']<=p['target1']
        if start==first and at>first and (stop or target):
            return dict(status='unknown',reason='entry_bar_before_after_unknown',tp1_before_sl=None)
        if stop or target:
            opens_stop=d*(b['open']-p['stop'])<=0
            opens_target=d*(b['open']-p['target1'])>=0
            value=False if opens_stop else True if opens_target else None if stop and target else target
            return dict(status='intrabar_ambiguous' if value is None else 'first_touch_in_5m_model',
                        at=replay.iso(start+300),tp1_before_sl=value)
    return dict(status='neither_before_6h',tp1_before_sl=None)


def csv_rows(groups):
    result=[]
    for group in groups:
        a,b=group['old'],group['new'];representative=a or b;p=representative['plan']
        prov=representative['provenance'];event=group.get('event_id') or representative.get('event_id')
        oa=a['outcome'] if a else None;nb=b['outcome'] if b else None
        net=lambda o: o.get('net_usdt_low') if o and o.get('status')=='exact_in_model' else None
        row=dict(event_id=event,group=group['group'],pairing_reason=group.get('reason','same_structural_anchor'),
            direction=p['direction'],anchor_known_at=None,
            old_candidate_at=(a['provenance'].get('candidate_at_source_text') or replay.iso(a['provenance']['candidate_at'])) if a else None,
            new_candidate_at=(b['provenance'].get('candidate_at_source_text') or replay.iso(b['provenance']['candidate_at'])) if b else None,
            old_confirm_at=(a['provenance'].get('confirm_at_source_text') or replay.iso(a['provenance']['confirm_at'])) if a else None,
            new_confirm_at=(b['provenance'].get('confirm_at_source_text') or replay.iso(b['provenance']['confirm_at'])) if b else None,
            old_entry=a['plan']['price'] if a else None,new_entry=b['plan']['price'] if b else None,
            stop=p['stop'],target1=p['target1'],target1_basis=prov['target1_basis'],
            nearest_barrier=prov['nearest_barrier']['price'],gate_reasons=canonical(group.get('gate_reasons',[])),
            tp1_before_sl=canonical(dict(old=a.get('first_contact') if a else None,new=b.get('first_contact') if b else None)),mfe_r=None,mae_r=None,
            outcome_status=canonical(dict(old=oa,new=nb)),net_pnl_model=None,
            old_net_pnl_model=net(oa) if a else 0 if group['group']=='added' else None,
            new_net_pnl_model=net(nb) if b else 0 if group['group']=='excluded' else None,
            confirmation_lead_seconds=(a['provenance']['confirm_at']-b['provenance']['confirm_at']) if a and b else None,
            signed_entry_worsening=p['direction']*(b['plan']['price']-a['plan']['price']) if a and b else None,
            data_limitations='receipt/dispatch/fills/funding unverified; retained signal-adjacent windows; MFE/MAE unavailable; ambiguous TP/SL stays unknown')
        for name,c in (('old',a),('new',b)):
            row[name+'_plan_id']=c['plan_id'] if c else None
            for field in ('stop','target1','target2'):
                row[name+'_'+field]=c['plan'].get(field) if c else None
        row['net_pnl_model']=canonical(dict(old=row['old_net_pnl_model'],new=row['new_net_pnl_model']))
        result.append(row)
    return result


def main():
    OUT.mkdir(parents=True,exist_ok=True)
    bundles,bars=replay.load_archives(replay.DATA/'archive_chunks.json')
    points=json.loads((replay.DATA/'recorded_quotes.json').read_text())
    costs=bundles[0]['model_config'];saved=json.loads(SAVED.read_text())
    outcomes=replay.load_outcome_bars(replay.DATA/'outcome_candle_logs.jsonl')
    attributes={replay.CONFIG_MAP[k]:v for k,v in costs.items()}
    attributes.update(COOLDOWN=2700,REVERSAL_COOLDOWN=5400,MAX_DAILY_ALERTS=8,is_quiet_new_entry_time=lambda _:False)
    summary=dict(schema='trade_evidence_v1',diagnostic_only=True,profitability_validated=False,
                 orders=0,sends=0,experiments={},source_commit=SOURCE_VERSION,
                 input_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
                  [SAVED,replay.DATA/'archive_chunks.json',replay.DATA/'recorded_quotes.json',replay.DATA/'outcome_candle_logs.jsonl',
                   ROOT/'docs/zerobase/TRADE_EVIDENCE_ALERT_DESIGN_20261025.md']})
    all_rows=[];all_cards=[];opportunity_rows=[];gate_rows=[]
    with tempfile.TemporaryDirectory(prefix='p0-evidence-') as directory, ExitStack() as stack:
        feed=replay.make_feed(directory,bars)
        stack.enter_context(patch.multiple(replay.monitor,**attributes))
        stack.enter_context(patch.object(replay.monitor,'telegram',side_effect=AssertionError('P0_SEND_FORBIDDEN')))
        logging.disable(logging.CRITICAL)
        for delay in (0,15):
            path=Path(directory)/f'evidence_delay{delay}.sqlite3';ledger=EvidenceLedger(path)
            traces=[];experiments={}
            try:
                for mode in ('capture','location'):
                    windows={}
                    for window in sorted({p['window'] for p in points}):
                        selected=sorted((p for p in points if p['window']==window),key=lambda p:p['logged_at'])
                        trace=Trace(ledger,mode,window,delay,costs)
                        # Existing replay asks for state at each retained quote. This
                        # adapter records the exact evaluation time without adding ticks.
                        original_available=replay.available_state
                        source_points=iter(selected)
                        def available(*args,**kwargs):
                            raw=next(source_points)
                            if replay.timestamp(raw['logged_at'])!=args[1] or raw['price']!=args[2]:
                                raise AssertionError('QUOTE_SOURCE_REPLAY_ORDER_MISMATCH')
                            trace.quote_source=copy.deepcopy(raw)
                            state,reason=original_available(*args,**kwargs)
                            trace.now=args[1]
                            trace.coverage.append(dict(at=args[1],reason=reason,valid=state is not None))
                            if state is None:
                                source=ledger.source(raw,event_time=args[1],quality='retained_quote_log_observation_proxy')
                                trace.input_hash=ledger.inputs([source])
                                trace.record(None,'observed',reason,gates=gate_vector(),phase='input_unavailable')
                            return state,reason
                        with ExitStack() as hooks:
                            hooks.enter_context(patch.object(replay.monitor,'ENTRY_REFINEMENT_MODE',mode))
                            hooks.enter_context(patch.object(replay,'available_state',available))
                            trace.patches(hooks)
                            row=replay.replay_window(selected,feed,delay)
                            for signal in row['signals']:
                                signal['benchmark']=replay.outcome(signal,outcomes)
                        if len(trace.cards)!=len(row['signals']):raise AssertionError('TRADE_CARD_LOSS')
                        for card,signal in zip(trace.cards,row['signals']):
                            card['outcome']=signal['benchmark']
                            card['horizons']=horizon_results(card,selected)
                            card['first_contact']=first_contact(card,outcomes)
                            ledger.outcome(card['plan_id'],dict(benchmark=card['outcome'],horizons=card['horizons'],first_contact=card['first_contact']))
                        windows[window]=row;traces.append(trace)
                    aggregate=replay.aggregate(windows)
                    if aggregate != saved['experiments'][f'{mode}_delay{delay}']['aggregate']:
                        raise AssertionError('P0_CHANGED_FROZEN_POLICY_OUTPUT')
                    # Check every retained observation summary, opportunity and
                    # trade, not merely counts or total PnL. Added evidence IDs
                    # are annotations rather than policy outputs.
                    frozen_windows=copy.deepcopy(windows)
                    for value in frozen_windows.values():
                        for signal in value['signals']:
                            signal.pop('evidence_plan_id',None)
                            signal.pop('evidence_event_id',None)
                    if frozen_windows != saved['experiments'][f'{mode}_delay{delay}']['windows']:
                        raise AssertionError('P0_CHANGED_FROZEN_WINDOW_OR_TRADE')
                    experiments[mode]=dict(aggregate=aggregate,
                        frozen_window_output_sha256=digest(frozen_windows),
                        observations=sum(len(t.observations) for t in traces if t.mode==mode),
                        input_states=Counter('valid' if c['valid'] else c['reason'] for t in traces if t.mode==mode for c in t.coverage),
                        first_reject=Counter(d['first_reject'] for t in traces if t.mode==mode for d in t.decisions if d['first_reject']),
                        unique_candidate_events=len({d['event_id'] for t in traces if t.mode==mode for d in t.decisions if d.get('event_id')}),
                        unique_flow={stage:len({d['event_id'] for t in traces if t.mode==mode for d in t.decisions
                            if d.get('event_id') and (stage=='detected' or stage=='candidate_qualified' and d.get('phase')=='candidate' and d['state']=='qualified'
                            or stage=='confirmed' and d.get('phase')=='confirmation' and d['state']=='qualified'
                            or stage=='alert_eligible' and d.get('phase')=='alert_eligibility' and d.get('modeled_confirmation') and d['state']=='qualified')})
                            for stage in ('detected','candidate_qualified','confirmed','alert_eligible')})
                    for phase in ('input_unavailable','candidate','confirmation','alert_eligibility'):
                        selected=[d for t in traces if t.mode==mode for d in t.decisions if d.get('phase')==phase and d['first_reject']]
                        experiments[mode].setdefault('phase_rejections',{})[phase]={reason:dict(
                            evaluations=sum(d['first_reject']==reason for d in selected),
                            unique_events=len({d['event_id'] for d in selected if d.get('event_id') and d['first_reject']==reason}))
                            for reason in sorted({d['first_reject'] for d in selected})}
                cards=[c for t in traces for c in t.cards];decisions=[d for t in traces for d in t.decisions]
                for d in decisions:
                    if d.get('event_id'):
                        gate_rows.append(dict(nominal_delay_seconds=delay,event_id=d['event_id'],policy=d['policy'],
                            evaluated_at=replay.iso(d['evaluated_at']),phase=d.get('phase'),state=d['state'],
                            first_reject=d['first_reject'],plan_reject_detail=d.get('plan_reject_detail'),
                            input_hash=d['input_hash'],gates=canonical(d['gates']),
                            limitations='nominal availability; unrun checks stay not_evaluated; not an ablation'))
                groups=pair_plans(cards,decisions)
                for g in groups:g['gate_reasons']=[dict(policy=d['policy'],phase=d.get('phase'),first_reject=d['first_reject'],detail=d.get('plan_reject_detail'))
                    for d in decisions if d.get('event_id')==g.get('event_id') and d['first_reject']]
                rows=csv_rows(groups)
                for row in rows:row['nominal_delay_seconds']=delay
                all_rows+=rows
                for raw, in ledger.db.execute('SELECT payload FROM opportunity_events ORDER BY event_id'):
                    op=json.loads(raw);event=op['event_id'];times={}
                    for mode in ('capture','location'):
                        values=[d['evaluated_at'] for d in decisions if d.get('event_id')==event and d['policy']==mode and d.get('phase')=='candidate']
                        times[mode]=min(values) if values else None
                    opportunity_rows.append(dict(event_id=event,direction=op['direction'],family=op['family'],anchor_start=op['anchor']['bar_start'],
                        anchor_known_at=None,nominal_available_at=op['anchor']['nominal_available_at'],nominal_delay_seconds=delay,
                        old_candidate_at=replay.iso(times['capture']) if times['capture'] is not None else None,
                        new_candidate_at=replay.iso(times['location']) if times['location'] is not None else None,
                        observed_candidate_lead_seconds=times['capture']-times['location'] if all(t is not None for t in times.values()) else None,
                        limitation='first in retained observations; true receipt/known_at absent; not final alarm lead'))
                for c in cards:c['nominal_delay_seconds']=delay
                all_cards+=cards
                summary['experiments'][str(delay)]=dict(policies=experiments,pairing=Counter(g['group'] for g in groups),
                    first_rejection_details=Counter(d.get('plan_reject_detail') for d in decisions if d.get('plan_reject_detail')),
                    unmatched_reasons=Counter(g.get('reason') for g in groups if g['group']=='unmatched'),
                    sources=ledger.db.execute('SELECT count(*) FROM source_events').fetchone()[0],
                    decisions=ledger.db.execute('SELECT count(*) FROM decision_events').fetchone()[0],
                    dispatches=ledger.db.execute('SELECT count(*) FROM dispatch_events').fetchone()[0])
            finally:ledger.close()
            with path.open('rb') as raw,(OUT/f'evidence_delay{delay}.sqlite3.gz').open('wb') as target:
                with gzip.GzipFile(filename='',mode='wb',fileobj=target,mtime=0) as archive:archive.write(raw.read())
    (OUT/'summary.json').write_text(json.dumps(summary,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    (OUT/'trade_cards.json').write_text(json.dumps(all_cards,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
    with (OUT/'trades.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(all_rows[0]));writer.writeheader();writer.writerows(all_rows)
    with (OUT/'opportunities.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(opportunity_rows[0]));writer.writeheader();writer.writerows(opportunity_rows)
    with (OUT/'gate_decisions.csv').open('w',newline='') as f:
        writer=csv.DictWriter(f,fieldnames=list(gate_rows[0]));writer.writeheader();writer.writerows(gate_rows)
    files=['summary.json','trade_cards.json','trades.csv','opportunities.csv','gate_decisions.csv']
    manifest=dict(schema='trade_evidence_v1',policy_outputs_exactly_reproduced=True,diagnostic_only=True,
        ledger_rebuild_command='python research/compare_signal_decisions.py',
        sqlite_archives='generated intermediate files; rebuild from pinned code and inputs',
        ingest_sequence_basis='offline_import_order_not_historical_receive_order',
        source_code_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in
            [Path(__file__),ROOT/'lbank_signal_evidence.py']},
        output_sha256={name:hashlib.sha256((OUT/name).read_bytes()).hexdigest() for name in files},
        inputs=summary['input_sha256'])
    (OUT/'manifest.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps(summary,ensure_ascii=False,indent=2))


if __name__=='__main__':main()
