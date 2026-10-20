"""Post-result descriptive attribution only; never chooses or changes a rule."""
import argparse,json
from pathlib import Path
ap=argparse.ArgumentParser();ap.add_argument('reports',nargs='+');ap.add_argument('--output',required=True);a=ap.parse_args()
rows=[]
for path in a.reports:
    d=json.loads(Path(path).read_text())
    for v in d['variants']:
        groups={}
        for t in v['closed_trades']:
            key=t['strategy']+(' long' if t['direction']==1 else ' short')
            g=groups.setdefault(key,dict(closed=0,TP1=0,gross_R=0.,net_R=0.,reasons={}))
            g['closed']+=1;g['TP1']+=t['reason']=='target1';g['gross_R']+=t['gross_R'];g['net_R']+=t['net_R']
            g['reasons'][t['reason']]=g['reasons'].get(t['reason'],0)+1
        rows.append(dict(start=d['start_utc'],end=d['end_exclusive_utc'],variant=v['variant'],groups=groups))
out=dict(descriptive_after_outcomes=True,selection_bias_corrected=False,new_strategy_rule=False,rows=rows)
Path(a.output).write_text(json.dumps(out,ensure_ascii=False,indent=2)+'\n')
