from unittest import TestCase

from lbank_zb.candle_evidence import REFERENCE_ROUTES
from research.zerobase.audit_native_holdout_inputs import audit, sealed_minutes


START = 1_795_549_500


def capture():
    return {
        'captured_at': START + 600,
        'samples': [],
        'bar_columns': ['period', 'start', 'open', 'high', 'low', 'close', 'volume', 'turnover', 'quality'],
        'native_rows': [[60, start, 100, 100, 100, 100, 1, 100, 'stream_closed']
                        for start in range(START, START + 300, 60)],
        'arrivals': [[60, start, start + 60, 'lbank-stream']
                     for start in range(START, START + 300, 60)],
    }


def comparison_row(**changes):
    routes = [route for route, _ in REFERENCE_ROUTES]
    return {'start': START, 'reference': {'start': START, 'source': routes[0],
            'corroborated_routes': routes, 'received_ms': (START + 300) * 1000,
            'open': '100', 'high': '100', 'low': '100', 'close': '100',
            'volume': '5', 'turnover': '500', **changes}}


class NativeHoldoutInputsTest(TestCase):
    def test_exact_independent_match_and_volume_disagreement(self):
        for row, expected in ((comparison_row(), 'matched'),
                              (comparison_row(volume='5.01'), 'volume_mismatch')):
            with self.subTest(expected=expected):
                result = audit([capture()], [row], [], START, START + 300)
                self.assertEqual(result['recomputed_close_checks'], {expected: 1})

    def test_minutes_do_not_substitute_for_native_five(self):
        result = audit([capture()], [], [], START, START + 300)
        self.assertEqual(result['sealed_minute_candles'], 5)
        self.assertEqual(result['recomputed_close_checks'], {'independent_reference_unavailable': 1})
        self.assertIsNone(result['comparison'])

    def test_conflicting_or_unreceived_minutes_are_not_used(self):
        first = capture()
        second = capture()
        second['native_rows'][0][5] = 101
        first['arrivals'][1][2] = START + 61
        second['arrivals'][1][2] = START + 61
        minutes, conflicts = sealed_minutes([first, second])
        self.assertEqual(conflicts, {START})
        self.assertNotIn(START + 60, minutes)
        result = audit([first, second], [comparison_row()], [], START, START + 300)
        self.assertEqual(result['recomputed_close_checks'], {'native_derived_missing': 1})
