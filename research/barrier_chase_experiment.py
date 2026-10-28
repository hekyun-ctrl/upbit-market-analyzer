"""E4 research-only barrier/chase ablation; no location mode or sender.

Reuse PR21's location bound and disable ONLY the single-pivot target exception
inside calculate_plan. All caller gates and frozen E1/E3/P1 factors stay intact.
"""
import copy
from unittest.mock import patch

from lbank_entry_location import location_check
from research.first_retest_experiment import (E1Trace, E1P1Trace, E1E3Trace,
    E1E3P1Trace, E3Trace, E3P1Trace, evidence, p1, replay)

POLICY = 'p2_e4_barrier_chase_only_v1'


class E4Mixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.policy += '_e4'
        self.e4_evidence = None

    def assess(self, *args, **kwargs):
        self.e4_evidence = None
        return super().assess(*args, **kwargs)

    def confirm(self, *args, **kwargs):
        self.e4_evidence = None
        return super().confirm(*args, **kwargs)

    def record(self, *args, **kwargs):
        if kwargs.get('phase') in ('candidate', 'confirmation'):
            kwargs['e4_evidence'] = copy.deepcopy(self.e4_evidence)
        return super().record(*args, **kwargs)

    def plan(self, original, prices, direction, setup=None, native_state=None, **kwargs):
        def checked(*a, **kw):
            # E1's detector proof is separately retained without enabling the
            # bundled location mode; use it only for the same PR21 location test.
            proof_setup = copy.deepcopy(kw.get('setup') or {})
            if not proof_setup.get('entry_location') and proof_setup.get('_p2_e1_detection'):
                proof_setup['entry_location'] = copy.deepcopy(proof_setup['_p2_e1_detection'])
            ok, reason, proof = location_check(kw.get('native_state'), direction, proof_setup)
            self.e4_evidence = dict(policy=POLICY, location=dict(passed=ok, reason=reason, proof=proof),
                ranked_plan_evaluations=[], scope='calculate_plan_only')
            if not ok:
                replay.monitor.LOG.info('PLAN REJECT reason=%s strategy=%s direction=%s',
                    reason, proof_setup.get('strategy'), direction)
                return None
            ranked = replay.monitor.ranked_entry_evidence
            def no_pivot_exception(*args, **kwds):
                result = copy.deepcopy(ranked(*args, **kwds))
                self.e4_evidence['ranked_plan_evaluations'].append(dict(
                    original_may_override_single_pivots=result['may_override_single_pivots'],
                    effective_may_override_single_pivots=False,
                    target1=args[3] if len(args)>3 else kwds.get('target1')))
                result['may_override_single_pivots'] = False
                return result
            with patch.object(replay.monitor, 'ranked_entry_evidence', no_pivot_exception):
                return original(*a, **kw)
        return super().plan(checked, prices, direction, setup=setup,
                            native_state=native_state, **kwargs)


class E4Trace(E4Mixin, evidence.Trace): pass
class E4E3Trace(E4Mixin, E3Trace): pass
class E4P1Trace(E4Mixin, p1.P1Trace): pass
class E4E3P1Trace(E4Mixin, E3P1Trace): pass
class E4E1Trace(E4Mixin, E1Trace): pass
class E4E1E3Trace(E4Mixin, E1E3Trace): pass
class E4E1P1Trace(E4Mixin, E1P1Trace): pass
class E4E1E3P1Trace(E4Mixin, E1E3P1Trace): pass
