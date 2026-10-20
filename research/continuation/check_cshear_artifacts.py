"""Separate output/Decimal/raw-minute reconciliation, without original-code execution."""
import argparse
import csv
from decimal import Decimal
import hashlib
import io
import json
from pathlib import Path
import zipfile

def main():
    p=argparse.ArgumentParser();p.add_argument('--results',type=Path,required=True)
    p.add_argument('--minute-archive',type=Path,required=True);a=p.parse_args()
    r=json.loads((a.results/'report.json').read_text());x=r['fixtures'];checks=[]
    files={f['path']:f['sha256'] for f in r['source_inventory']['tracked']}
    assert all(f['source_file_sha256']==files[f['file']] for f in x['selected_definitions'])
    checks.append('all8executeddefinitionfilehashes_match_pinned_Git_objects')
    assert r['source_inventory']['public_data_or_ledger_paths']==[]
    assert x['nearest_lookup']['full_label']>x['nearest_lookup']['query']
    assert x['funding_future_append']['prefix']!=x['funding_future_append']['full']
    for row in x['funding_direction_recovery']:
        future=(Decimal(str(row['future_close']))-100)/100
        original_short_pnl=-future+Decimal('.0008')*Decimal('.5')-Decimal('.0006')
        assert abs(float(original_short_pnl)-row['evaluator_pnl'])<1e-12
        inferred=1 if (original_short_pnl+Decimal('.0006'))/future>0 else -1
        assert inferred==row['inferred_side_exit']==row['inferred_side_tp']
    checks.append('original_funding_short_cash_and_both_recovered_direction_functions')
    assert abs(x['sample_end_source_exit'][0]-float(Decimal('.20')-Decimal('.0006')))<1e-12
    assert x['sample_end_source_exit'][1]==4<48
    assert abs(x['stop_gap_source_exit'][0]-float(-Decimal('.02')-Decimal('.0006')))<1e-12
    checks.append('sample_end_and_stop_gap_independent_cash')
    cost_error=Decimal(0)
    for row in r['hypothetical_unit_costs']['rows']:
        d=Decimal(row['side']);m=Decimal(row['cost_multiple']);move=Decimal(row['price_move_fraction'])
        initial=Decimal(100);reference_exit=initial*(1+d*move)
        entry_slippage=initial*Decimal('.0002')*m
        exit_slippage=reference_exit*Decimal('.0002')*m
        entry_notional=initial+d*entry_slippage
        exit_notional=reference_exit-d*exit_slippage
        fees=Decimal('.0006')*m*(entry_notional+exit_notional)
        expected=d*(reference_exit-initial)-entry_slippage-exit_slippage-fees-initial*Decimal('.0001')*m
        cost_error=max(cost_error,abs(expected-Decimal(row['net_cash'])))
    assert cost_error==0;checks.append('8longshort_base_double_full_unit_cashflows')
    zraw=a.minute_archive.read_bytes();assert hashlib.sha256(zraw).hexdigest()==r['real_price_prefix']['zip_sha256']
    with zipfile.ZipFile(io.BytesIO(zraw)) as z:raw=z.read(z.namelist()[0])
    assert hashlib.sha256(raw).hexdigest()==r['real_price_prefix']['csv_sha256']
    rows=csv.reader(io.StringIO(raw.decode()));header=next(rows);minutes=list(rows)
    if header[0].isdigit():minutes.insert(0,header)
    # Independently use each240th minute close: no shared aggregation/accessor code.
    closes=[float(minutes[i][4]) for i in range(239,len(minutes),240)]
    queries=r['real_price_prefix']['rows'];assert len(queries)==len(closes)-1==185
    for i,q in enumerate(queries):
        expected_start=int(minutes[i*240][0])//1000
        assert q['query']==expected_start+10800
        assert q['prefix_selected_label']==expected_start
        assert q['full_selected_label']==expected_start+14400
        assert q['prefix_close']==closes[i] and q['full_close']==closes[i+1]
        assert q['exact_label_control_unchanged']
    assert sum(q['prefix_close']!=q['full_close'] for q in queries)==r['real_price_prefix']['past_query_price_changes']==185
    checks.append('185real_queries_prices_labels_and185exactlabel_positivecontrols')
    result=dict(passed=True,checks=checks,executed_source_definitions=8,unit_cost_checks=8,
        real_queries=185,exact_label_controls=185,unit_cash_max_abs_error=str(cost_error),
        headline_profit_impact_measured=False,market_profitability_backtest_performed=False)
    (a.results/'independent_audit.json').write_text(json.dumps(result,indent=2)+'\n')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
