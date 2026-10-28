"""E1 research adapter: first-retest proposals only, capture gates unchanged.

The detector is reused byte-for-byte from PR21. E4 location/chase/barrier rules
and location confirmation progress are deliberately separate factors. These
hooks exist only in offline experiments; no production mode or sender is added.
"""
import copy
from unittest.mock import patch

from lbank_entry_location import first_retests
from research.compare_confirmation_experiment import E3Trace, E3P1Trace, evidence, p1, replay

POLICY = 'p2_e1_first_retest_detection_only_v1'


def merge_proposals(state, capture, diagnostics=None):
    """First-retest priority, preserving capture fallback and opposite sides.

    This mirrors classify's existing (strategy, direction) deduplication. It
    does not rewrite bars, current quote, entry rules, or plan calculations.
    """
    found = first_retests(state, diagnostics)
    seen, merged = set(), []
    for setup in found + capture:
        key = setup['strategy'], setup['direction']
        if key not in seen:
            seen.add(key)
            merged.append(setup)
    return merged


class E1Mixin:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        fast = self.policy.endswith('_e3')
        self.policy = (self.policy[:-3] if fast else self.policy) + '_e1' + ('_e3' if fast else '')
        self.e1_detection = None

    def classify(self, original, prices, *args, **kwargs):
        state = kwargs['native_state']
        detector = replay.monitor.detect_structures
        def injected(*a, **kw):
            capture = detector(*a, **kw)
            diagnostics = {}
            merged = merge_proposals(state, capture, diagnostics)
            if kw.get('diagnostics') is not None:
                kw['diagnostics']['e1_detection'] = diagnostics
            return merged
        with patch.object(replay.monitor, 'detect_structures', injected):
            return super().classify(original, prices, *args, **kwargs)

    def assess(self, original, prices, direction, setup, *args, **kwargs):
        self.e1_detection = copy.deepcopy(setup.get('entry_location'))
        result = super().assess(original, prices, direction, setup, *args, **kwargs)
        if result is not None and self.e1_detection is not None:
            result['_p2_e1_detection'] = copy.deepcopy(self.e1_detection)
        return result

    def confirm(self, original, prices, watch, age, *args, **kwargs):
        self.e1_detection = copy.deepcopy(watch.get('_p2_e1_detection'))
        result = super().confirm(original, prices, watch, age, *args, **kwargs)
        if result is not None and self.e1_detection is not None:
            result['_p2_e1_detection'] = copy.deepcopy(self.e1_detection)
        return result

    def record(self, *args, **kwargs):
        if kwargs.get('phase') in ('candidate', 'confirmation'):
            kwargs['e1_detection'] = copy.deepcopy(self.e1_detection)
        return super().record(*args, **kwargs)


class E1Trace(E1Mixin, evidence.Trace):
    pass


class E1P1Trace(E1Mixin, p1.P1Trace):
    pass


class E1E3Trace(E1Mixin, E3Trace):
    pass


class E1E3P1Trace(E1Mixin, E3P1Trace):
    pass
