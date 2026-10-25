"""Evidence identity, causality, atomicity and honest pairing; no sends."""
import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from lbank_signal_evidence import EvidenceLedger,event_id,gate_vector,pair_plans,digest
from research.compare_signal_decisions import Trace,anchor_for,barrier_at,first_contact,csv_rows


def anchor(**extra):
    return dict(bar_hash='a'*64,bar_start=60,period=60,field='low',price=100.,known_at=None,**extra)


def card(policy,event='event-1',identity=None,outcome=None):
    return dict(plan_id=identity or policy,event_id=event,policy=policy,
                plan=dict(direction=1,price=100.,stop=99.,target1=102.),
                provenance=dict(candidate_at=120.,confirm_at=150.,target1_basis='cost_solved',
                                nearest_barrier=dict(price=103.)),
                outcome=outcome or dict(status='unresolved',reason='gap'))


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'evidence.db'
        self.book=EvidenceLedger(self.path);self.addCleanup(lambda:self.book.close())

    def inputs(self,event_time=100.,received_at=None):
        key=self.book.source(dict(price=100.,unique=event_time),event_time=event_time,received_at=received_at)
        return self.book.inputs([key])

    def test_event_identity_ignores_policy_display_number_and_receipt_annotation(self):
        a=anchor();b=dict(a,known_at=130.,display_number=77,policy='different')
        self.assertEqual(event_id(1,'pullback',a),event_id(1,'pullback',b))
        for changed in (dict(a,bar_start=120),dict(a,bar_hash='b'*64),dict(a,price=101.)):
            self.assertNotEqual(event_id(1,'pullback',a),event_id(1,'pullback',changed))
        self.assertNotEqual(event_id(1,'pullback',a),event_id(-1,'pullback',a))

    def test_anchor_provenance_and_availability_are_required(self):
        for a in ({},dict(anchor(),bar_hash=''),dict(anchor(),bar_start=61),dict(anchor(),price=float('nan'))):
            with self.assertRaises(ValueError):event_id(1,'pullback',a)
        with self.assertRaisesRegex(ValueError,'ANCHOR_NOT_AVAILABLE'):
            self.book.opportunity(1,'pullback',dict(anchor(),known_at=200.),first_observed_at=150.)

    def test_restart_dedup_and_earlier_comparator_observation_keep_one_event(self):
        identity=self.book.opportunity(1,'pullback',anchor(),first_observed_at=160.)
        self.book.close();self.book=EvidenceLedger(self.path)
        self.assertEqual(identity,self.book.opportunity(1,'pullback',anchor(),first_observed_at=150.))
        self.book.opportunity(1,'pullback',anchor(),first_observed_at=170.)
        rows=self.book.db.execute('SELECT payload FROM opportunity_events').fetchall()
        self.assertEqual(len(rows),1)
        self.assertEqual(json.loads(rows[0][0])['first_observed_at'],150.)

    def test_future_bar_or_late_right_confirmation_receipt_cannot_be_used_early(self):
        for event,received in ((180.,None),(100.,180.)):
            inputs=self.inputs(event,received)
            with self.assertRaisesRegex(ValueError,'INPUT_NOT_AVAILABLE'):
                self.book.decision(event=None,policy='capture',evaluated_at=150.,input_hash=inputs,
                    state='observed',available_at=140.,availability_basis='nominal_scenario')

    def test_unknown_receipt_stays_unknown_and_cannot_be_promoted_to_actual(self):
        inputs=self.inputs()
        with self.assertRaisesRegex(ValueError,'RECEIPT_PROVENANCE_REQUIRED'):
            self.book.decision(event=None,policy='capture',evaluated_at=150.,input_hash=inputs,
                state='observed',available_at=150.,availability_basis='observed_receipt')
        self.book.decision(event=None,policy='capture',evaluated_at=150.,input_hash=inputs,
            state='observed',available_at=150.,availability_basis='nominal_scenario')
        raw=json.loads(self.book.db.execute('SELECT payload FROM source_events').fetchone()[0])
        self.assertIsNone(raw['received_at'])

    def test_same_timestamp_inputs_have_distinct_sequences_and_duplicate_hash_dedups(self):
        first=self.book.source(dict(price=100),event_time=100.)
        second=self.book.source(dict(price=101),event_time=100.)
        self.assertEqual(first,self.book.source(dict(price=100),event_time=100.))
        rows=self.book.db.execute('SELECT ingest_seq FROM source_events').fetchall()
        self.assertNotEqual(first,second);self.assertEqual(len(rows),2);self.assertNotEqual(rows[0],rows[1])

    def test_quote_source_retains_original_timestamp_precision_without_fabricating_receipt(self):
        trace=Trace(self.book,'capture','synthetic',0,{})
        trace.quote_source=dict(logged_at='1970-01-03T03:05:00.000000123+00:00',price=100.)
        trace.inputs(dict(minute_bars=[],five_bars=[],quote=100.),183900.000000123)
        raw=json.loads(self.book.db.execute('SELECT payload FROM source_events').fetchone()[0])
        self.assertEqual(raw['raw']['logged_at'],'1970-01-03T03:05:00.000000123+00:00')
        self.assertIsNone(raw['received_at'])

    def test_first_reject_does_not_mark_unrun_gates_passed_and_decision_deduplicates(self):
        values=gate_vector();values['context']=dict(status='failed',reason='countertrend')
        args=dict(event=None,policy='capture',evaluated_at=150.,input_hash=self.inputs(),state='rejected',
                  gates=values,first_reject='context',available_at=150.,availability_basis='nominal_scenario')
        self.assertEqual(self.book.decision(**args),self.book.decision(**args))
        row=json.loads(self.book.db.execute('SELECT payload FROM decision_events').fetchone()[0])
        self.assertEqual(self.book.db.execute('SELECT count(*) FROM decision_events').fetchone()[0],1)
        self.assertEqual(row['gates']['rsi']['status'],'not_evaluated')

    def test_frozen_plan_and_disabled_intent_are_atomic_and_no_unknown_ack_is_fabricated(self):
        key=self.book.freeze(event='event',policy='capture',plan=dict(price=100),provenance={},intent=True)
        self.assertEqual(key,self.book.freeze(event='event',policy='capture',plan=dict(price=100),provenance={},intent=True))
        self.book.delivery(key,'send_ack_unknown',requested_at=150.)
        self.assertEqual(self.book.db.execute('SELECT status FROM dispatch_events').fetchone()[0],'send_ack_unknown')
        with self.assertRaisesRegex(ValueError,'PROVIDER_ACK'):
            self.book.delivery(key,'acknowledged',at=151.)
        self.book.delivery(key,'acknowledged',provider_message_id='fixture-1')
        with self.assertRaisesRegex(ValueError,'IMMUTABLE'):self.book.delivery(key,'send_failed')

    def test_failed_intent_insert_rolls_back_the_plan(self):
        self.book.db.executescript("CREATE TRIGGER abort_intent BEFORE INSERT ON dispatch_events BEGIN SELECT RAISE(ABORT,'fixture'); END;")
        with self.assertRaises(sqlite3.IntegrityError):
            self.book.freeze(event='event',policy='capture',plan={},provenance={},intent=True)
        self.assertEqual(self.book.db.execute('SELECT count(*) FROM frozen_plans').fetchone()[0],0)

    def test_matching_uses_exact_anchors_without_time_window_or_outcome_selection(self):
        a,b=card('capture'),card('location');a['outcome']=dict(status='exact_in_model',net_usdt_low=-1.)
        b['provenance']['confirm_at']=5000.
        self.assertEqual(pair_plans([a,b,a],[]) [0]['group'],'matched')
        b['event_id']='other'
        self.assertTrue(all(g['group']=='unmatched' for g in pair_plans([a,b],[])))
        a2=copy.deepcopy(a);a2['plan_id']='capture-second'
        self.assertTrue(all(g['group']=='unmatched' for g in pair_plans([a,a2,b],[])))

    def test_nonentry_zero_is_distinct_from_entered_unknown(self):
        a=card('capture')
        group=pair_plans([a],[dict(event_id='event-1',policy='location')])
        row=csv_rows(group)[0]
        self.assertEqual(row['group'],'excluded')
        self.assertIsNone(row['old_net_pnl_model'])
        self.assertEqual(row['new_net_pnl_model'],0)

    def test_pivot_confirmation_uses_right_closed_bar_not_pivot_start(self):
        bars=[dict(start=i*300,high=101.,low=99.) for i in range(7)]
        bars[3]['high']=105.
        value=barrier_at(dict(five_bars=bars),1,100.,15)
        self.assertEqual(value['nominal_known_at'],5*300+300+15)
        self.assertIsNone(value['known_at'])
        self.assertEqual(value['right_bar_hash'],digest(bars[5]))

    def test_adapter_rejects_a_right_bar_received_after_evaluation(self):
        from test_lbank_entry_location import retest_state
        s=retest_state();now=s['minute_bars'][-1]['start']+60
        s['five_bars'][-1]['received_at']=now+15
        trace=Trace(self.book,'capture','synthetic',0,{})
        with self.assertRaisesRegex(ValueError,'FUTURE_SOURCE_RECEIPT'):trace.inputs(s,now)

    def test_first_touch_preserves_intrabar_ambiguity_and_gap_uncertainty(self):
        c=card('capture');c['provenance']['confirm_at']=300.
        bars={300:dict(start=300,open=100.,high=103.,low=98.,close=100.,quality='stream_closed')}
        self.assertEqual(first_contact(c,bars)['status'],'intrabar_ambiguous')
        bars[300]['open']=98.
        self.assertFalse(first_contact(c,bars)['tp1_before_sl'])
        self.assertIsNone(first_contact(c,{})['tp1_before_sl'])
        bars[300]['low']=float('nan')
        self.assertIsNone(first_contact(c,bars)['tp1_before_sl'])


if __name__=='__main__':unittest.main()
