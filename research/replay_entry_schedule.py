"""Compare actual old/new run loops with the same injected scheduling fixture.

This is NOT market-data backtesting. All network/delivery and qualification
outputs are controlled; the independent native-gate tests cover strategy gates.
"""
import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import types
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import lbank_futures_monitor as current
import test_lbank_confirmation_cadence as fixture


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--baseline-ref',default='6c3e22e6ca5c580807de83bf68e871d4c9362761')
    parser.add_argument('--output')
    args=parser.parse_args()
    source=subprocess.check_output(['git','show',args.baseline_ref+':lbank_futures_monitor.py'],cwd=ROOT)
    baseline=types.ModuleType('lbank_monitor_schedule_baseline')
    baseline.__file__=str(ROOT/'lbank_futures_monitor.py')
    exec(compile(source,'<pinned-baseline-monitor>','exec'),baseline.__dict__)
    outcomes={}
    for name,module in [('baseline',baseline),('changed',current)]:
        with patch.object(fixture,'monitor',module):
            run=fixture.run_clock(ready_at=9015.,qualify_at=9015.,stop_at=9120.)
        outcomes[name]=dict(candidate_evaluation_seconds=[t-9000. for t in run['classify_times']],
            confirmation_seconds=[t-9000. for t,_,_ in run['confirmed']],
            simulated_send_seconds=[t-9000. for t,_ in run['sent']],
            sample_save_seconds=sorted({t-9000. for _,rows in run['saved'] for t,_ in rows}))
    result=dict(baseline_ref=args.baseline_ref,baseline_monitor_sha256=hashlib.sha256(source).hexdigest(),
        changed_monitor_sha256=hashlib.sha256((ROOT/'lbank_futures_monitor.py').read_bytes()).hexdigest(),
        fixture=dict(native_ready_at_seconds=15,qualification_true_from_seconds=15,
                     risk_tick_seconds=15,price_sample_seconds=60,minimum_watch_seconds=30),
        outcome=outcomes,market_replay=False,actual_delivery=False,profitability_test=False)
    assert outcomes['baseline']['simulated_send_seconds']==[90.]
    assert outcomes['changed']['simulated_send_seconds']==[45.]
    assert outcomes['baseline']['sample_save_seconds']==outcomes['changed']['sample_save_seconds']==[0.,60.]
    output=json.dumps(result,ensure_ascii=False,sort_keys=True,indent=2)+'\n'
    if args.output:Path(args.output).write_text(output)
    print(output)


if __name__=='__main__':main()
