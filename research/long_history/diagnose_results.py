"""Descriptive decomposition of already-frozen trade ledgers; no new strategy fit."""
import argparse,json
from pathlib import Path
import pandas as pd


def diagnose(initial,preplanned):
    output={}
    for path,label in ((initial,'initial_2026'),(preplanned,'preplanned_2023')):
        frame=pd.read_csv(path);frame=frame[frame.net_r.notna()]
        if 'evaluation' in frame:
            frame=frame[frame.evaluation.str.startswith('2026:')]
            groups=frame.groupby('evaluation')
        else:groups=frame.groupby('family')
        for key,g in groups:
            gross=g['side']*(g['exit']-g['entry'])/g['unit_net_risk']
            output[f'{label}:{key}']=dict(trades=len(g),
                mean_gross_price_pnl_per_original_net_risk=float(gross.mean()),
                mean_net_r=float(g.net_r.mean()),mean_deduction_r=float((gross-g.net_r).mean()),
                exit_reasons=g.reason.value_counts().to_dict())
    return output


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--initial',required=True)
    p.add_argument('--preplanned',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();Path(a.output).write_text(json.dumps(diagnose(a.initial,a.preplanned),indent=2)+'\n')
