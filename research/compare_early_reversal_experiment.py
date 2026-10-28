"""Predeclared E5 × E4 × E1 × E3 early reversal ablation, with separate frozen target panels."""
import copy
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

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from research import compare_structural_targets as p1
from research.evidence_confirmation import POLICY as E3_POLICY
from research.compare_confirmation_experiment import E3Mixin, E3Trace, E3P1Trace
from research.first_retest_experiment import E1Mixin, E1Trace, E1P1Trace, E1E3Trace, E1E3P1Trace, POLICY
from research.barrier_chase_experiment import (E4Mixin, E4Trace, E4E3Trace, E4P1Trace, E4E3P1Trace, E4E1Trace, E4E1E3Trace, E4E1P1Trace, E4E1E3P1Trace, POLICY)
from lbank_signal_evidence import EvidenceLedger, digest, pair_plans

evidence, replay = p1.evidence, p1.replay
from research.early_reversal_experiment import E5Mixin, BASE_CLASSES, E5_CLASSES, CachedResearchLedger, POLICY
OUT = ROOT/'research/zerobase/completion_20261028/p2_e5'
DECLARATION = OUT/'experiment_manifest.json'
CODE = list(dict.fromkeys(p1.CODE + ['research/evidence_confirmation.py',
    'research/compare_confirmation_experiment.py', 'lbank_signal_evidence.py',
    'research/first_retest_experiment.py', 'research/compare_first_retest_experiment.py',
    'lbank_entry_location.py', 'lbank_structure_patterns.py',
    'research/barrier_chase_experiment.py', 'research/compare_barrier_chase_experiment.py',
    'research/early_reversal_experiment.py','research/compare_early_reversal_experiment.py']))


def economic_windows(windows):
    """Strip provenance IDs only, retaining every actual strategy output."""
    windows = copy.deepcopy(windows)
    for window in windows.values():
        for signal in window['signals']:
            signal.pop('evidence_plan_id', None)
            signal.pop('evidence_event_id', None)
    return windows


def pair_evidence(cards, decisions, old, new):
    cards = [c for c in cards if c['policy'] in (old, new)]
    decisions = [d for d in decisions if d['policy'] in (old, new)]
    groups = pair_plans(cards, decisions, old=old, new=new)
    for group in groups:
        group['gate_reasons'] = [copy.deepcopy(d) for d in decisions
            if group.get('event_id') and d.get('event_id') == group['event_id'] and d.get('first_reject')]
    return groups


def main():
    logging.disable(logging.CRITICAL)
    declaration = json.loads(DECLARATION.read_text())
    for path, expected in declaration['input_sha256'].items():
        assert hashlib.sha256((ROOT/path).read_bytes()).hexdigest() == expected, ('DECLARED_INPUT_CHANGED', path)
    frozen = json.loads((ROOT/'research/zerobase/completion_20261028/p2_e4/comparison.json').read_text())
    bundles, bars = replay.load_archives(replay.DATA/'archive_chunks.json')
    points = json.loads((replay.DATA/'recorded_quotes.json').read_text())
    outcomes = replay.load_outcome_bars(replay.DATA/'outcome_candle_logs.jsonl')
    costs = bundles[0]['model_config']
    code_hash = {path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in CODE}
    evidence.SOURCE_VERSION = digest(dict(declaration=digest(declaration), code=code_hash))
    summary = dict(schema='zb_p2_e5_diagnostic_v1', policy=POLICY,
        diagnostic_only=True, development_data=True, unseen_data=False,
        profitability_validated=False, sends=0, orders=0, baseline_exactly_reproduced=True,
        declaration_sha256=hashlib.sha256(DECLARATION.read_bytes()).hexdigest(),
        e1_detection_only_implemented=True, e4_implemented=True, e5_implemented=True, live_ledger_integrated=False,
        source_blockers=declaration['frozen']['source_blockers'],
        assumptions=frozen['assumptions'], experiments={})
    all_cards, all_decisions, all_rows, opportunities = [], [], [], []
    scenarios = [('base_delay0',0,costs), ('base_delay15',15,costs),
        ('adverse_delay0',0,dict(costs,fee=.0008,slippage=.0005,funding_buffer=.0003))]
    with tempfile.TemporaryDirectory(prefix='p2-e5-') as directory:
        feed = replay.make_feed(directory, bars)
        for scenario, delay, model in scenarios:
            ledger = CachedResearchLedger(Path(directory)/(scenario+'.sqlite3'))
            traces, arms = [], {}
            attrs = {replay.CONFIG_MAP[k]:v for k,v in model.items()}
            attrs.update(COOLDOWN=2700, REVERSAL_COOLDOWN=5400, MAX_DAILY_ALERTS=8,
                         is_quiet_new_entry_time=lambda _:False)
            with patch.multiple(replay.monitor, **attrs), patch.object(replay.monitor, 'ENTRY_REFINEMENT_MODE', 'capture'), patch.object(replay.monitor, 'telegram', side_effect=AssertionError('P2_SEND_FORBIDDEN')):
                for cls in BASE_CLASSES + E5_CLASSES:
                    windows, local_traces = {}, []
                    fast = issubclass(cls, E3Mixin)
                    for window in sorted({p['window'] for p in points}):
                        selected = sorted((p for p in points if p['window']==window), key=lambda p:p['logged_at'])
                        trace = cls(ledger, 'capture', window, delay, model)
                        original, iterator = replay.available_state, iter(selected)
                        def available(*args, **kwargs):
                            raw = next(iterator)
                            assert replay.timestamp(raw['logged_at'])==args[1] and raw['price']==args[2]
                            trace.quote_source, trace.now = copy.deepcopy(raw), args[1]
                            state, reason = original(*args, **kwargs)
                            trace.coverage.append(dict(at=args[1], valid=state is not None, reason=reason))
                            if state is None:
                                src=ledger.source(raw,event_time=args[1],quality='retained_quote_log_observation_proxy')
                                trace.input_hash=ledger.inputs([src])
                                trace.record(None,'observed',reason,phase='input_unavailable')
                            return state, reason
                        with ExitStack() as hooks:
                            hooks.enter_context(patch.object(replay, 'available_state', available))
                            trace.patches(hooks)
                            def watch_busy(proposal, watch):
                                trace.record(proposal.get('_evidence_event_id'), 'observed',
                                    'existing_watch_pending', phase='watch_scheduling',
                                    active_watch_event_id=watch['plan'].get('_evidence_event_id'),
                                    active_watch_at=watch['time'])
                            row = replay.replay_window(selected, feed, delay,
                                confirmation_delay_seconds=0 if fast else 30,
                                confirm_on_discovery=fast, watch_observer=watch_busy,
                                proposal_filter=trace.schedule if fast or isinstance(trace,E5Mixin) else None)
                            for signal in row['signals']:
                                signal['benchmark'] = replay.outcome(signal, outcomes)
                        assert len(trace.cards)==len(row['signals']), 'TRADE_CARD_LOSS'
                        for card, signal in zip(trace.cards, row['signals']):
                            card.update(scenario=scenario, outcome=signal['benchmark'],
                                first_contact=evidence.first_contact(card, outcomes),
                                horizons=evidence.horizon_results(card, selected),
                                manual_delay_scenarios=p1.manual_sensitivity(card, selected, outcomes))
                        windows[window]=row
                        traces.append(trace); local_traces.append(trace)
                    policy=trace.policy
                    if not issubclass(cls, E5Mixin):
                        expected=frozen['experiments'][scenario]['arms'][policy]['windows']
                        assert economic_windows(windows)==economic_windows(expected), ('FROZEN_BASELINE_CHANGED',scenario,policy)
                    cards=[c for t in local_traces for c in t.cards]
                    decisions=[d for t in local_traces for d in t.decisions]
                    aggregate=replay.aggregate(windows)
                    aggregate.update(trade_count=len(cards), stop_first_count=sum(c['first_contact'].get('tp1_before_sl') is False for c in cards),
                        stop_first_unknown_count=sum(c['first_contact'].get('tp1_before_sl') is None for c in cards),
                        emergency_count=None, full_mtm_drawdown=None,
                        unresolved_ratio=sum(c['outcome']['status']!='exact_in_model' for c in cards)/len(cards) if cards else None,
                        immediate_confirmations=sum(c['provenance']['confirm_at']==c['provenance']['candidate_at'] for c in cards))
                    arms[policy]=dict(aggregate=aggregate, windows=windows,
                        input_states=dict(Counter('valid' if c['valid'] else c['reason'] for t in local_traces for c in t.coverage)),
                        first_reject=dict(Counter(d['first_reject'] for d in decisions if d['first_reject'])),
                        confirmation_attempts=sum(d.get('phase')=='confirmation' for d in decisions),
                        phase_rejections={phase:dict(Counter(d.get('plan_reject_detail') or d['first_reject'] for d in decisions if d.get('phase')==phase and d['first_reject'])) for phase in ('candidate','confirmation','alert_eligibility')})
                cards=[c for t in traces for c in t.cards]
                decisions=[d for t in traces for d in t.decisions]
                pairs={}
                for old,new in declaration['comparison_pairs']:
                    name=old+'_vs_'+new
                    groups=pair_evidence(cards,decisions,old,new)
                    rows=evidence.csv_rows(groups)
                    for row in rows:row.update(scenario=scenario,comparison=name)
                    all_rows+=rows
                    pairs[name]=dict(pairing=dict(Counter(g['group'] for g in groups)),
                        confirmation_lead_seconds=[r['confirmation_lead_seconds'] for r in rows if r['confirmation_lead_seconds'] is not None])
                    events=sorted({d['event_id'] for d in decisions if d.get('event_id') and d['policy'] in (old,new)})
                    for event in events:
                        times={p:min((d['evaluated_at'] for d in decisions if d.get('event_id')==event and d['policy']==p and d.get('phase')=='candidate'),default=None) for p in (old,new)}
                        opportunities.append(dict(event_id=event,scenario=scenario,comparison=name,
                            old_candidate_at=times[old],new_candidate_at=times[new],candidate_lead_seconds=times[old]-times[new] if all(t is not None for t in times.values()) else None,
                            known_at=None,actual_received_at=None))
                for decision in decisions:decision['scenario']=scenario
                all_cards+=cards;all_decisions+=decisions
                summary['experiments'][scenario]=dict(costs=model,nominal_delay_seconds=delay,arms=arms,comparisons=pairs)
                assert ledger.db.execute('SELECT count(*) FROM dispatch_events').fetchone()[0]==0
            ledger.close()
    p1.write_json(OUT/'comparison.json',summary)
    p1.write_json(OUT/'trade_cards.json',all_cards)
    p1.write_csv(OUT/'trades.csv',all_rows)
    p1.write_csv(OUT/'opportunities.csv',opportunities)
    p1.write_csv(OUT/'gate_decisions.csv',all_decisions)
    raw=(OUT/'gate_decisions.csv').read_bytes()
    (OUT/'gate_decisions.csv.gz').write_bytes(gzip.compress(raw,mtime=0))
    (OUT/'.gitignore').write_text('gate_decisions.csv\n')
    outputs=['comparison.json','trade_cards.json','trades.csv','opportunities.csv','gate_decisions.csv.gz']
    p1.write_json(OUT/'manifest.json',dict(schema='zb_p2_e5_artifacts_v1',
        declaration_sha256=summary['declaration_sha256'], code_sha256=code_hash,
        input_sha256=declaration['input_sha256'],
        output_sha256={p:hashlib.sha256((OUT/p).read_bytes()).hexdigest() for p in outputs},
        uncompressed_decision_sha256=hashlib.sha256(raw).hexdigest(),
        command='python research/compare_early_reversal_experiment.py',sample_class=declaration['sample_class']))
    print(json.dumps({k:{p:dict(alerts=a['aggregate']['trade_count'],stops=a['aggregate']['stop_first_count'],net=a['aggregate']['total_net_usdt'],unresolved=a['aggregate']['unresolved_ratio']) for p,a in v['arms'].items() if p.endswith('_e5')} for k,v in summary['experiments'].items()},indent=2))


if __name__=='__main__':
    main()
