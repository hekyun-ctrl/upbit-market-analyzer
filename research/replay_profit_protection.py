"""Causal quote-only policy replay, not an execution or strategy backtest."""
import json
from pathlib import Path
from lbank_native_entry import SOURCE,wilder_rsi_series
from lbank_profit_protection import new_policy,observe,acknowledge


def replay():
    root=Path(__file__).parent/'fixtures'
    evidence=json.loads((root/'lbank_profit_protection_20261019.json').read_text())
    original=json.loads((root/'lbank_direction_priority_20261019.json').read_text())['bars']
    p=evidence['reference'];policy=new_policy(p['direction'],p['price'],p['stop']);state=None;events=[]
    for q in evidence['quotes']:
        now=q['ts'];expected=int(now//300)*300-300
        bars={b['start']:b for b in original if b['start']+300<=p['sent_at']}
        for row in evidence['bar_publications']:
            if row['available_at']<=now:
                bars[row['bar']['start']]=row['bar']
        tail=[];start=expected
        while start in bars:
            tail.append(bars[start]);start-=300
        tail.reverse()
        end15=int(now//900)*900-900
        c15=[bars[t+600]['close'] for t in range(end15-4*900,end15+1,900)
             if all(t+offset in bars for offset in (0,300,600))]
        native=dict(source=SOURCE,five_bars=tail,last5_start=expected,minute_bars=[],closes15=c15,
                    rsi5={'rsi':wilder_rsi_series([b['close'] for b in tail])})
        state,event=observe(policy,state,direction=p['direction'],entry=p['price'],
                            price=q['price'],now=now,native_state=native)
        if event:
            events.append(dict(at=q['at'],reason=event,price=q['price'],guard=state['guard'],
                               evidence=state.get('touch')))
            if event=='profit_guard_touched':break
            acknowledge(state,event)
    return {'policy':policy,'events':events,'actual_pnl':False,
            'limitations':'Coarse logged quotes, original 5m bars available at their logged publication times. No actual fills, incomplete 15s observations, not a full strategy backtest.'}


if __name__=='__main__':print(json.dumps(replay(),ensure_ascii=False,indent=2))
