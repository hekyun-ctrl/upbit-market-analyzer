import unittest
from decimal import Decimal
from audit_rlxbt_combo_contract import condition,cash,truth_table


RULES=dict(entry_rules=[
    dict(condition='vol_regime_persistence_24 < -0.0988 && weekend_vol_shock <= 0',direction=1,signal='flagship_long'),
    dict(condition='is_monday == 1',direction=1,signal='monday_long'),
    dict(condition='vol_regime_persistence_24 > 0.3410',direction=-1,signal='flagship_short'),
    dict(condition='is_thursday == 1',direction=-1,signal='thursday_short')],
    exit_rules=[dict(condition='vol_regime_persistence_24 > 0.0412 && vol_regime_persistence_24 < 0.3410',reason='flagship_exit')])


class ContractTests(unittest.TestCase):
    def test_original_strict_boundaries(self):
        for value,expected in [('-0.0988001',True),('-0.0988',False),('0',False)]:
            self.assertEqual(condition(RULES['entry_rules'][0]['condition'],
                dict(vol_regime_persistence_24=value,weekend_vol_shock=0)),expected)

    def test_missing_features_never_become_zero(self):
        with self.assertRaises(ValueError):condition('weekend_vol_shock <= 0',{})

    def test_calls_and_attribute_access_never_execute(self):
        for value in ['__import__("os")','is_monday.real','[is_monday][0]']:
            with self.assertRaises(ValueError):condition(value,dict(is_monday=1))

    def test_opposed_calendar_and_volatility_original_rules(self):
        values=dict(vol_regime_persistence_24='.5',weekend_vol_shock=0,is_monday=1,is_thursday=0)
        active=[r['direction'] for r in RULES['entry_rules'] if condition(r['condition'],values)]
        self.assertEqual(active,[1,-1])

    def test_full_synthetic_partition_counts_not_market_rates(self):
        t=truth_table(RULES)
        self.assertEqual((t['cases'],t['opposite_entry_cases'],t['simultaneous_entry_exit_cases']),(147,5,6))
        self.assertEqual(t['same_direction_multiple_signal_cases'],5)
        self.assertFalse(t['market_frequency_estimated']);self.assertFalse(t['engine_priority_reproduced'])

    def test_long_cash_independent_decimal_fixture(self):
        self.assertEqual(Decimal(cash(1,'.02')['net']),Decimal('1.82839824'))
        self.assertEqual(Decimal(cash(1,.02)['net']),Decimal('1.82839824'))
        self.assertEqual(Decimal(cash(1,'-.015')['net']),Decimal('-1.66880218'))

    def test_short_cash_independent_decimal_fixture(self):
        self.assertEqual(Decimal(cash(-1,'.02')['net']),Decimal('1.83160224'))
        self.assertEqual(Decimal(cash(-1,'-.015')['net']),Decimal('-1.67119818'))

    def test_double_costs_hold_reference_moves_fixed(self):
        for d in (1,-1):
            for move in ('.02','-.015'):
                self.assertLess(Decimal(cash(d,move,2)['net']),Decimal(cash(d,move,1)['net']))


if __name__=='__main__':unittest.main()
