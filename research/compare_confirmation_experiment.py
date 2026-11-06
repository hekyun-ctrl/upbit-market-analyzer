"""Predeclared E3-only offline ablation, with separate frozen target panels."""
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
from research.evidence_confirmation import freeze_trigger, readiness, POLICY
from lbank_signal_evidence import EvidenceLedger, digest, pair_plans

evidence, replay = p1.evidence, p1.replay
OUT = ROOT/'research/zerobase/completion_20261028/p2'
DECLARATION = OUT/'experiment_manifest.json'
CODE = list(dict.fromkeys(p1.CODE + ['research/evidence_confirmation.py',
    'research/compare_confirmation_experiment.py', 'lbank_signal_evidence.py']))


class E3Mixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.policy += '_e3'
        self.confirmation_trigger = None
        self.confirmation_readiness = None
        self.invalidated_events = set()

    def schedule(self, proposal):
        event = proposal.get('_evidence_event_id')
        if event is not None and event in self.invalidated_events:
            self.record(event, 'rejected', 'e3_event_previously_invalidated',
                        phase='watch_scheduling')
            return None
        return proposal

    def assess(self, original, prices, direction, setup, *args, **kwargs):
        self.confirmation_trigger = freeze_trigger(setup, kwargs['native_state'])
        result = super().assess(original, prices, direction, setup, *args, **kwargs)
        if result:
            result['_p2_confirmation_trigger'] = copy.deepcopy(self.confirmation_trigger)
        return result

    def record(self, *args, **kwargs):
        if kwargs.get('phase') == 'candidate':
            kwargs['e3_frozen_trigger'] = copy.deepcopy(self.confirmation_trigger)
        elif kwargs.get('phase') == 'confirmation':
            kwargs['e3_readiness'] = copy.deepcopy(self.confirmation_readiness)
        return super().record(*args, **kwargs)

    def confirm(self, original, prices, watch, age, *args, **kwargs):
        def checked(*a, **kw):
            ok, reason, proof, terminal = readiness(kw['native_state'], watch, self.now, self.delay)
            self.confirmation_readiness = proof
            if not ok:
                if reason == 'e3_original_stop_crossed' and watch.get('_evidence_event_id') is not None:
                    self.invalidated_events.add(watch['_evidence_event_id'])
                kw['diagnostics'].update(reason=reason, cancel_watch=terminal)
                return None
            # Actual elapsed age is retained. This explicit argument has no env
            # activation path and no production caller supplies it.
            return original(*a, **kw, minimum_confirmation_seconds=0)
        return super().confirm(checked, prices, watch, age, *args, **kwargs)


class E3Trace(E3Mixin, evidence.Trace):
    pass


class E3P1Trace(E3Mixin, p1.P1Trace):
    pass


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
    frozen = json.loads((p1.OUT/'comparison.json').read_text())
    bundles, bars = replay.load_archives(replay.DATA/'archive_chunks.json')
    points = json.loads((replay.DATA/'recorded_quotes.json').read_text())
    outcomes = replay.load_outcome_bars(replay.DATA/'outcome_candle_logs.jsonl')
    costs = bundles[0]['model_config']
    code_hash = {path:hashlib.sha256((ROOT/path).read_bytes()).hexdigest() for path in CODE}
    evidence.SOURCE_VERSION = digest(dict(declaration=digest(declaration), code=code_hash))
    summary = dict(schema='zb_p2_e3_diagnostic_v1', policy=POLICY,
        diagnostic_only=True, development_data=True, unseen_data=False,
        profitability_validated=False, sends=0, orders=0, baseline_exactly_reproduced=True,
        declaration_sha256=hashlib.sha256(DECLARATION.read_bytes()).hexdigest(),
        e1_e4_e5_implemented=False, live_ledger_integrated=False,
        source_blockers=declaration['frozen']['source_blockers'],
        assumptions=frozen['assumptions'], experiments={})
    all_cards, all_decisions, all_rows, opportunities = [], [], [], []
    scenarios = [('base_delay0',0,costs), ('base_delay15',15,costs),
        ('adverse_delay0',0,dict(costs,fee=.0008,slippage=.0005,funding_buffer=.0003))]
    with tempfile.TemporaryDirectory(prefix='p2-e3-') as directory:
        feed = replay.make_feed(directory, bars)
        for scenario, delay, model in scenarios:
            ledger = EvidenceLedger(Path(directory)/(scenario+'.sqlite3'))
            traces, arms = [], {}
            attrs = {replay.CONFIG_MAP[k]:v for k,v in model.items()}
            attrs.update(COOLDOWN=2700, REVERSAL_COOLDOWN=5400, MAX_DAILY_ALERTS=8,
                         is_quiet_new_entry_time=lambda _:False)
            with patch.multiple(replay.monitor, **attrs), patch.object(replay.monitor, 'ENTRY_REFINEMENT_MODE', 'capture'), patch.object(replay.monitor, 'telegram', side_effect=AssertionError('P2_SEND_FORBIDDEN')):
                for cls in (evidence.Trace, E3Trace, p1.P1Trace, E3P1Trace):
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
                                proposal_filter=trace.schedule if fast else None)
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
                    if not fast:
                        expected=frozen['experiments'][scenario]['policies'][policy]['windows']
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
    p1.write_json(OUT/'manifest.json',dict(schema='zb_p2_e3_artifacts_v1',
        declaration_sha256=summary['declaration_sha256'], code_sha256=code_hash,
        input_sha256=declaration['input_sha256'],
        output_sha256={p:hashlib.sha256((OUT/p).read_bytes()).hexdigest() for p in outputs},
        uncompressed_decision_sha256=hashlib.sha256(raw).hexdigest(),
        command='python research/compare_confirmation_experiment.py',sample_class=declaration['sample_class']))
    print(json.dumps({k:{p:a['aggregate'] for p,a in v['arms'].items()} for k,v in summary['experiments'].items()},indent=2))


if __name__=='__main__':
    main()
