"""Observed native alert paths, preserving gaps and unknown intraminute order."""
import json,re
from pathlib import Path
from datetime import datetime


def timestamp(s):return datetime.fromisoformat(s.replace('Z','+00:00')).timestamp()


def audit():
    source=Path(__file__).parent.parent/'fixtures/lbank_strategy_rebuild_native_20261020.json'
    data=json.loads(source.read_text());bars={r['bar']['start']:r['bar'] for r in data['bars']}
    cases=[]
    for reference,e in enumerate(data['entries'],8):
        t=timestamp(e['at']);m=re.search(r'side=(\S+) entry=([\d.]+) stop=([\d.]+) t1=([\d.]+)',e['message'])
        d=1 if m[1]=='롱' else -1;entry,stop,target=map(float,m.group(2,3,4))
        follow=next(r for r in data['followups'] if f'reference={reference} ' in r['message']);end=timestamp(follow['at'])
        window=sorted([b for b in bars.values() if b['start']>=t and b['start']+300<=end],key=lambda b:b['start'])
        favorable=[d*(b['high' if d==1 else 'low']-entry)/entry for b in window]
        adverse=[-d*(b['low' if d==1 else 'high']-entry)/entry for b in window]
        cases.append(dict(reference=reference,entry_at=e['at'],entry=entry,stop=stop,target=target,
            required_target_pct=abs(target-entry)/entry*100,initial_stop_pct=abs(stop-entry)/entry*100,
            full_bars_before_followup=len(window),observed_favorable_pct=max(0.,max(favorable))*100 if favorable else None,
            observed_adverse_pct=max(0.,max(adverse))*100 if adverse else None,
            interior_gaps=[{'after':a['start']+300,'before':b['start']} for a,b in zip(window,window[1:]) if b['start']-a['start']!=300],
            followup=follow,partial_entry_and_followup_candles_excluded=True,
            completeness_or_actual_fills_verified=False))
    return dict(source=str(source.relative_to(Path(__file__).parents[2])),cases=cases,
        applies_to='Five historical alerts before production 139c23d; not current policy performance.',
        target_generation='Production minimum_net_rr_target solves a net reward/risk equation; this is not a calibrated probability of reaching target.',
        interpretation='A favorable observed excursion is not a realizable profit. Missing partial minutes or gaps remain unknown. Do not infer a global rate or exact root cause for every fill from five examples.')


if __name__=='__main__':
    print(json.dumps(audit(),ensure_ascii=False,indent=2))
