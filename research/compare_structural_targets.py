"""P1-only target experiment on frozen development data. Never sends/orders.

Capture detection, candidate filters, 30s confirmation, independent cooldowns,
sampling, stop calculation and budget caps stay fixed. T1/T2 become bounded
structural prices; the same benchmark closes both halves at T1 when no farther
target is independently established. This is not a new confirmation experiment.
"""
import copy
import csv
import gzip
import hashlib
import json
import logging
import sys
import tempfile
from collections import Counter
from contextlib import ExitStack
from pathlib import Path
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from research import compare_signal_decisions as evidence
from lbank_signal_evidence import EvidenceLedger, digest, pair_plans
from lbank_structural_target_plan import StructuralTargetPolicy, POLICY

replay=evidence.replay
OUT=ROOT/'research/zerobase/completion_20261028/p1'
CODE=['lbank_structural_target_plan.py','lbank_futures_monitor.py',
      'research/compare_structural_targets.py','research/compare_signal_decisions.py',
      'research/compare_entry_refinement.py']


class P1Trace(evidence.Trace):
    def __init__(self,*a,**kw):
        super().__init__(*a,**kw)
        self.policy='structural_p1';self.last_plan=None

    def assess(self,*a,**kw):
        self.last_plan=None
        return super().assess(*a,**kw)

    def confirm(self,*a,**kw):
        self.last_plan=None
        return super().confirm(*a,**kw)

    def record(self,*a,**kw):
        if kw.get('phase') in ('candidate','confirmation'):
            kw['structural_target_evidence']=copy.deepcopy(self.last_plan)
        return super().record(*a,**kw)

    def plan(self,original,*a,**kw):
        policy=StructuralTargetPolicy(now=self.now,pnl=replay.monitor.net_pnl_per_btc,
            ratio=replay.monitor.MIN_NET_RR,tick=.1,quantity_step=.0001,
            minimum_quantity=.0001,minimum_notional=1.,nominal_delay=self.delay,
            metadata_basis='2026-10-23 public instrument snapshot; not current reverified')
        def injected(*args,**kwargs):return original(*args,**kwargs,target_policy=policy)
        result=super().plan(injected,*a,**kw)
        self.last_plan=policy.last
        return result

    def compact(self,original,plan):
        # The parent creates/finalizes immutable evidence records. Override its
        # provenance before freeze rather than mutating a saved ledger payload.
        if not plan.get('_evidence_confirmed'):
            return super().compact(original,plan)
        result=dict(original(plan),entry_location=plan.get('entry_location'),
                    location_evidence=plan.get('location_evidence'))
        proof=plan['structural_target_plan']
        provenance=dict(input_hash=self.input_hash,anchor=plan.get('_evidence_anchor'),
            candidate_at=plan.get('_evidence_candidate_at'),confirm_at=self.now,
            candidate_at_source_text=plan.get('_evidence_candidate_source_time'),
            confirm_at_source_text=self.quote_source['logged_at'],known_at=None,
            send_requested_at=None,telegram_ack_at=None,receipt_status='not_available',
            timing_basis='retained_quote_proxy_and_nominal_bar_scenario',
            nearest_barrier=dict(proof['barrier'],status='observed_pivot_not_forecast'),
            target1_basis=proof['target1_basis'],costs=self.costs,commit=evidence.SOURCE_VERSION)
        frozen={k:v for k,v in plan.items() if not k.startswith('_evidence_')}
        identity=self.ledger.freeze(event=plan.get('_evidence_event_id'),policy=self.policy,
                                   plan=frozen,provenance=provenance,intent=False)
        self.cards.append(dict(plan_id=identity,event_id=plan.get('_evidence_event_id'),
            policy=self.policy,window=self.window,plan=frozen,provenance=provenance))
        return dict(result,evidence_plan_id=identity,evidence_event_id=plan.get('_evidence_event_id'))


def manual_sensitivity(card,points,outcomes):
    """Same trade scenarios, never new independent trade counts or fake ticks."""
    result={};at=card['provenance']['confirm_at'];p=card['plan']
    for delay in (30,60):
        eligible=[q for q in points if at+delay<=replay.timestamp(q['logged_at'])<=at+delay+90]
        if not eligible:
            result[str(delay)]=dict(status='unresolved',net_pnl_model=None,
                                   reason='no_actual_retained_quote_within_90s')
            continue
        q=eligible[0];entry=q['price'];d=p['direction']
        loss=-replay.monitor.net_pnl_per_btc(d,entry,p['stop'])
        gain=replay.monitor.net_pnl_per_btc(d,entry,p['target1'],exit_kind='take_profit')
        if d*(entry-p['stop'])<=0 or d*(p['target1']-entry)<=0 or loss<=0 or gain<replay.monitor.MIN_NET_RR*loss-1e-8:
            result[str(delay)]=dict(status='no_entry',net_pnl_model=0,reason='fixed_plan_no_longer_permissible',
                                   observed_at=q['logged_at'],entry=entry)
            continue
        qty=min(p['quantity_btc'],replay.monitor.REF_BALANCE*replay.monitor.RISK_FRACTION/loss,
                replay.monitor.REF_BALANCE*replay.monitor.MAX_MARGIN_FRACTION*replay.monitor.LEVERAGE/entry)
        trade=dict(at=q['logged_at'],direction=d,price=entry,stop=p['stop'],
                   target1=p['target1'],target2=p['target2'],quantity_btc=qty)
        o=replay.outcome(trade,outcomes)
        result[str(delay)]=dict(status=o['status'],outcome=o,
            net_pnl_model=o.get('net_usdt_low') if o['status']=='exact_in_model' else None,
            observed_at=q['logged_at'],entry=entry,quantity_btc=qty,
            actual_observation_delay_seconds=replay.timestamp(q['logged_at'])-at,
            assumptions='fixed original S/T; cap Q at original and current risk/margin; modeled quote entry, no verified fill')
    return result


def write_json(path,value):path.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False)+'\n')
def write_csv(path,rows):
    if not rows:path.write_text('');return
    keys=list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=keys);w.writeheader()
        w.writerows({k:json.dumps(v,sort_keys=True) if isinstance(v,(dict,list)) else v for k,v in row.items()} for row in rows)


def main():
    OUT.mkdir(parents=True,exist_ok=True);logging.disable(logging.CRITICAL)
    bundles,bars=replay.load_archives(replay.DATA/'archive_chunks.json')
    points=json.loads((replay.DATA/'recorded_quotes.json').read_text())
    outcomes=replay.load_outcome_bars(replay.DATA/'outcome_candle_logs.jsonl')
    saved=json.loads(evidence.SAVED.read_text());costs=bundles[0]['model_config']
    code_hash={p:hashlib.sha256((ROOT/p).read_bytes()).hexdigest() for p in CODE}
    evidence.SOURCE_VERSION=digest(dict(base='d098f5657d823769f468846df1fac5ec04986d0e',code=code_hash))
    result=dict(schema='p1_structural_target_comparison_v1',policy=POLICY,
        diagnostic_only=True,profitability_validated=False,development_data=True,unseen_data=False,
        independent_market_validation=False,actual_fills=False,sends=0,orders=0,
        baseline_capture_exactly_reproduced=True,experiments={},
        assumptions=dict(receipt_times='unknown; nominal closed+0/15s scenarios only',
            fee='taker both legs; retained config or explicit adverse estimate',
            slippage='existing model adverse stop exit only; target fill slippage not verified',
            funding='hypothetical reserve per entry, actual history days=0',
            quantity='P1 floors to public snapshot .0001 BTC step and checks .0001 minimum / 1 USDT notional',
            target2='P1 equals structural T1; both benchmark halves there, no invented extension',
            emergency='not modeled; count unknown, not zero',
            delays='scenarios of same events; never sum as independent trades',
            cooldown='per policy/window independent, initially empty; unobserved prior alerts unknown'))
    all_cards=[];all_rows=[];all_decisions=[];opportunities=[]
    scenarios=[('base_delay0',0,costs),('base_delay15',15,costs),
               ('adverse_delay0',0,dict(costs,fee=.0008,slippage=.0005,funding_buffer=.0003))]
    with tempfile.TemporaryDirectory(prefix='p1-target-') as directory:
        feed=replay.make_feed(directory,bars)
        for scenario,delay,model in scenarios:
            path=Path(directory)/(scenario+'.sqlite3');ledger=EvidenceLedger(path)
            traces=[];policies={}
            attrs={replay.CONFIG_MAP[k]:v for k,v in model.items()}
            attrs.update(COOLDOWN=2700,REVERSAL_COOLDOWN=5400,MAX_DAILY_ALERTS=8,is_quiet_new_entry_time=lambda _:False)
            with patch.multiple(replay.monitor,**attrs),patch.object(replay.monitor,'ENTRY_REFINEMENT_MODE','capture'),patch.object(
                    replay.monitor,'telegram',side_effect=AssertionError('P1_SEND_FORBIDDEN')):
                for cls in (evidence.Trace,P1Trace):
                    windows={};policy='capture' if cls is evidence.Trace else 'structural_p1'
                    for window in sorted({p['window'] for p in points}):
                        selected=sorted((p for p in points if p['window']==window),key=lambda p:p['logged_at'])
                        trace=cls(ledger,'capture',window,delay,model);source_points=iter(selected)
                        original=replay.available_state
                        def available(*a,**kw):
                            trace.quote_source=copy.deepcopy(next(source_points));trace.now=a[1]
                            assert replay.timestamp(trace.quote_source['logged_at'])==a[1]
                            state,reason=original(*a,**kw)
                            trace.coverage.append(dict(valid=state is not None,reason=reason))
                            return state,reason
                        with ExitStack() as hooks:
                            hooks.enter_context(patch.object(replay,'available_state',available));trace.patches(hooks)
                            row=replay.replay_window(selected,feed,delay)
                            for signal in row['signals']:signal['benchmark']=replay.outcome(signal,outcomes)
                        assert len(trace.cards)==len(row['signals'])
                        for card,signal in zip(trace.cards,row['signals']):
                            card.update(outcome=signal['benchmark'],first_contact=evidence.first_contact(card,outcomes),
                                horizons=evidence.horizon_results(card,selected),manual_delay_scenarios=manual_sensitivity(card,selected,outcomes),scenario=scenario)
                        windows[window]=row;traces.append(trace)
                    aggregate=replay.aggregate(windows)
                    cards=[c for t in traces if t.policy==policy for c in t.cards]
                    aggregate.update(trade_count=len(cards),
                        stop_first_count=sum(c['first_contact'].get('tp1_before_sl') is False for c in cards),
                        stop_first_unknown_count=sum(c['first_contact'].get('tp1_before_sl') is None for c in cards),
                        emergency_count=None,unresolved_ratio=sum(c['outcome']['status']!='exact_in_model' for c in cards)/len(cards) if cards else None)
                    if policy=='capture' and scenario.startswith('base'):
                        frozen=copy.deepcopy(windows)
                        for v in frozen.values():
                            for s in v['signals']:s.pop('evidence_plan_id',None);s.pop('evidence_event_id',None)
                        assert frozen==saved['experiments'][f'capture_delay{delay}']['windows'],'BASELINE_CHANGED'
                    decisions=[d for t in traces if t.policy==policy for d in t.decisions]
                    policies[policy]=dict(aggregate=aggregate,windows=windows,
                        first_reject=dict(Counter(d['first_reject'] for d in decisions if d['first_reject'])),
                        phase_rejections={phase:dict(Counter((d.get('plan_reject_detail') or d['first_reject'])
                            for d in decisions if d.get('phase')==phase and d['first_reject']))
                            for phase in ('candidate','confirmation','alert_eligibility')},
                        input_states=dict(Counter('valid' if c['valid'] else c['reason'] for t in traces if t.policy==policy for c in t.coverage)))
                cards=[c for t in traces for c in t.cards];decisions=[d for t in traces for d in t.decisions]
                groups=pair_plans(cards,decisions,new='structural_p1')
                rows=evidence.csv_rows(groups)
                for r in rows:r['scenario']=scenario
                all_rows+=rows;all_cards+=cards
                for d in decisions:d['scenario']=scenario
                all_decisions+=decisions
                events={d['event_id'] for d in decisions if d.get('event_id')}
                for event in sorted(events):
                    times={p:min((d['evaluated_at'] for d in decisions if d.get('event_id')==event
                        and d['policy']==p and d.get('phase')=='candidate'),default=None) for p in ('capture','structural_p1')}
                    opportunities.append(dict(event_id=event,scenario=scenario,**times,
                        candidate_lead_seconds=times['capture']-times['structural_p1'] if all(v is not None for v in times.values()) else None,
                        receipt_known_at=None))
                result['experiments'][scenario]=dict(costs=model,nominal_delay_seconds=delay,policies=policies,
                    pairing=dict(Counter(g['group'] for g in groups)),
                    first_candidate_lead_seconds=[r['candidate_lead_seconds'] for r in opportunities if r['scenario']==scenario and r['candidate_lead_seconds'] is not None])
                assert ledger.db.execute('SELECT count(*) FROM dispatch_events').fetchone()[0]==0
            ledger.close()
    write_json(OUT/'comparison.json',result);write_json(OUT/'trade_cards.json',all_cards)
    write_csv(OUT/'trades.csv',all_rows);write_csv(OUT/'gate_decisions.csv',all_decisions);write_csv(OUT/'opportunities.csv',opportunities)
    # Preserve the complete decision table remotely without duplicating its
    # repetitive CSV. The uncompressed table is a reproducible intermediate.
    (OUT/'gate_decisions.csv.gz').write_bytes(gzip.compress((OUT/'gate_decisions.csv').read_bytes(),mtime=0))
    (OUT/'.gitignore').write_text('gate_decisions.csv\n')
    inputs=[replay.DATA/'archive_chunks.json',replay.DATA/'recorded_quotes.json',replay.DATA/'outcome_candle_logs.jsonl',evidence.SAVED]
    manifest=dict(policy=POLICY,code_sha256=code_hash,
        input_sha256={str(p.relative_to(ROOT)):hashlib.sha256(p.read_bytes()).hexdigest() for p in inputs},
        output_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in OUT.iterdir()
                       if p.name not in ('manifest.json','gate_decisions.csv','.gitignore')},
        uncompressed_decision_sha256=hashlib.sha256((OUT/'gate_decisions.csv').read_bytes()).hexdigest(),
        command='python research/compare_structural_targets.py',sample_class='real retained public observations, development only; costs/receipts/fills modeled')
    write_json(OUT/'manifest.json',manifest)
    print(json.dumps({k:{p:v['aggregate'] for p,v in e['policies'].items()} for k,e in result['experiments'].items()},indent=2))


if __name__=='__main__':main()
