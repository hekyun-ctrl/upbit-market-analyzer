"""Pairing regressions: a rejected P1 trade must keep its observed cause."""
import unittest

from research.compare_structural_targets import paired_plan_evidence


class PairedTradeEvidenceTests(unittest.TestCase):
    def test_excluded_trade_retains_stage_time_and_structural_cause(self):
        card = dict(plan_id='plan-a', event_id='event-a', policy='capture')
        proof = dict(status='room_insufficient', maximum_permissible_entry=99.5)
        decisions = [dict(event_id='event-a', policy='structural_p1',
                          phase='candidate', evaluated_at=1000, first_reject='plan',
                          plan_reject_detail='room_insufficient', input_hash='input-a',
                          structural_target_evidence=proof),
                     dict(event_id='event-b', policy='structural_p1',
                          first_reject='rsi', phase='candidate')]
        group, = paired_plan_evidence([card], decisions)
        self.assertEqual(group['group'], 'excluded')
        reason, = group['gate_reasons']
        self.assertEqual(reason['detail'], 'room_insufficient')
        self.assertEqual(reason['phase'], 'candidate')
        self.assertEqual(reason['evaluated_at'], 1000)
        self.assertEqual(reason['input_hash'], 'input-a')
        self.assertEqual(reason['structural_target_evidence'], proof)
        reason['structural_target_evidence']['status'] = 'changed'
        self.assertEqual(proof['status'], 'room_insufficient')

    def test_repeated_evaluations_remain_one_trade_with_observed_reasons(self):
        card = dict(plan_id='plan-a', event_id='event-a', policy='capture')
        decisions = [dict(event_id='event-a', policy='structural_p1',
                          phase='candidate', evaluated_at=t, first_reject='plan',
                          plan_reject_detail='room_unknown') for t in (1000, 1005)]
        groups = paired_plan_evidence([card], decisions)
        self.assertEqual(len(groups), 1)
        self.assertEqual([d['evaluated_at'] for d in groups[0]['gate_reasons']], [1000, 1005])

    def test_missing_or_nonrejecting_observation_never_invents_reason(self):
        cards = [dict(plan_id='plan-a', event_id='event-a', policy='capture'),
                 dict(plan_id='plan-b', event_id=None, policy='capture')]
        decisions = [dict(event_id='event-a', policy='structural_p1', first_reject=None),
                     dict(event_id=None, policy='structural_p1', first_reject='rsi')]
        groups = paired_plan_evidence(cards, decisions)
        self.assertTrue(all(g['gate_reasons'] == [] for g in groups))


if __name__ == '__main__':
    unittest.main()
