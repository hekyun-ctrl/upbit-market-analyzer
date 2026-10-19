"""Protect raw-source release from incomplete or incompatible trade evidence."""
import unittest
import json,tempfile
from pathlib import Path
import numpy as np
import pandas as pd
from research.continuation.reconcile_trade_archives import aggregate_window,compare_original,coverage_diagnostics,load_audit_inputs,TARGETS


class SourceEvidenceTests(unittest.TestCase):
    start=1730149800

    def trades(self):
        # Two same-millisecond trades per minute test exchange ID ordering.
        rows=[(100,9.,1.,9.,(self.start-1)*1000,True)]
        for minute in range(5):
            for offset in range(2):
                ident=101+minute*2+offset;price=10.+minute+offset/2
                rows.append((ident,price,2.,price*2,(self.start+minute*60)*1000,offset==0))
        rows.append((111,15.,1.,15.,(self.start+300)*1000,False))
        return pd.DataFrame(rows,columns=['id','price','qty','quote_qty','time','is_buyer_maker'])

    def test_exchange_id_order_and_taker_volume(self):
        bars,ids=aggregate_window(self.trades(),self.start)
        self.assertEqual(ids['target_rows'],10)
        np.testing.assert_array_equal(bars.open,[10,11,12,13,14])
        np.testing.assert_array_equal(bars.close,[10.5,11.5,12.5,13.5,14.5])
        np.testing.assert_array_equal(bars.volume,[4]*5)
        np.testing.assert_array_equal(bars.buy_volume,[2]*5)
        np.testing.assert_array_equal(bars.buy_quote_volume,[21,23,25,27,29])

    def test_target_id_gap_refused(self):
        trades=self.trades().drop(index=4)
        with self.assertRaisesRegex(ValueError,'IDs incomplete'):
            aggregate_window(trades,self.start)

    def test_missing_minute_refused_even_with_consecutive_ids(self):
        trades=self.trades().drop(index=[5,6]).copy()
        trades['id']=range(100,100+len(trades))
        with self.assertRaisesRegex(ValueError,'minutes absent'):
            aggregate_window(trades,self.start)

    def test_edge_coverage_required(self):
        for drop in (0,11):
            with self.subTest(edge=drop),self.assertRaisesRegex(ValueError,'edge coverage'):
                aggregate_window(self.trades().drop(index=drop),self.start)

    def test_unknown_maker_flag_refused(self):
        trades=self.trades().astype({'is_buyer_maker':'object'})
        trades.loc[3,'is_buyer_maker']='unknown'
        with self.assertRaisesRegex(ValueError,'maker flag'):
            aggregate_window(trades,self.start)

    def test_equal_volume_does_not_hide_bad_price(self):
        bars,_=aggregate_window(self.trades(),self.start)
        original=bars.copy();original.loc[self.start,'open']+=.1
        match,differences=compare_original(bars,original)
        self.assertFalse(match);self.assertEqual(differences['volume'],0)

    def test_equal_prices_do_not_hide_bad_volume(self):
        bars,_=aggregate_window(self.trades(),self.start)
        original=bars.copy();original.loc[self.start+60,'volume']+=1
        match,differences=compare_original(bars,original)
        self.assertFalse(match);self.assertEqual(differences['volume'],1)

    def test_complete_match_releases(self):
        bars,_=aggregate_window(self.trades(),self.start)
        self.assertTrue(compare_original(bars,bars.copy())[0])

    def test_failure_records_missing_ids_and_edge(self):
        details=coverage_diagnostics(self.trades().drop(index=[0,4]),self.start)
        self.assertFalse(details['before_present'])
        self.assertTrue(details['after_present'])
        self.assertEqual(details['context_id_discontinuities'],1)
        self.assertEqual(details['missing_context_ids'],1)
        self.assertEqual(details['id_gap_examples'][0]['before_id'],103)
        self.assertEqual(details['id_gap_examples'][0]['after_id'],105)

    def test_mutated_cached_monthly_archive_refused(self):
        source=Path(__file__).resolve().parents[1]/'long_history'/'results'
        months={day[:7] for day in TARGETS}
        with tempfile.TemporaryDirectory() as tmp:
            for interval in ('1m','5m'):
                entries=json.loads((source/f'source_manifest_2024_2026_{interval}.json').read_text())['archives']
                for record in entries:
                    if record['kind']=='klines' and record['month'] in months:
                        p=Path(tmp)/'klines'/interval/record['url'].rsplit('/',1)[-1]
                        p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(b'mutated source')
            with self.assertRaisesRegex(ValueError,'differs from frozen source SHA'):
                load_audit_inputs(tmp)


if __name__=='__main__':unittest.main()
