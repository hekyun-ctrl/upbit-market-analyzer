"""Offline-only paired breakout/retest opportunity comparer.

It consumes already captured snapshots and never polls, sends, or orders.  Both
routes share the same frozen structural stop/target and cost model.  A retest
is an independent route, never a prerequisite for the breakout route.
"""
import hashlib,json,math


def opportunity_id(direction,level,break_start,box_end):
    raw=f'{direction}:{level:.8f}:{break_start}:{box_end}'.encode()
    return hashlib.sha256(raw).hexdigest()[:20]


def _outcome(rows, route, frozen, direction, pnl):
    if route is None:return None
    last=route['ts']
    for r in rows:
        if r['ts']<=route['ts']:continue
        if r['ts']-last>150:return {'status':'censored_gap','net':None}
        last=r['ts'];price=r['price']
        if direction*(price-frozen['stop'])<=0:
            return {'status':'stop','net':frozen['quantity_btc']*pnl(direction,route['entry'],price)}
        if direction*(price-frozen['target1'])>=0:
            return {'status':'target','net':frozen['quantity_btc']*pnl(direction,route['entry'],frozen['target1'],exit_kind='take_profit')}
    return {'status':'unresolved','net':None}


def compare(rows, plan_factory, pnl=None, *, expiry=900):
    if expiry<=0:raise ValueError('invalid expiry')
    rows=sorted(rows,key=lambda x:x['ts'])
    if any(not math.isfinite(float(r['price'])) or r['price']<=0 for r in rows):raise ValueError('invalid price')
    if any(rows[i]['ts']<=rows[i-1]['ts'] for i in range(1,len(rows))):raise ValueError('timestamps must be unique and increasing')
    opportunities={}
    for r in rows:
        five=r.get('five_bars') or []
        if len(five)<21:continue
        prior=five[-21:-1];br=five[-1]
        if br.get('quality')!='stream_closed':continue
        hi=max(x['high'] for x in prior);lo=min(x['low'] for x in prior)
        for d,level in ((1,hi),(-1,lo)):
            if d*(br['close']-level)<=0:continue
            oid=opportunity_id(d,level,br['start'],prior[-1]['start'])
            o=opportunities.setdefault(oid,{'id':oid,'direction':d,'level':level,'break_start':br['start'],
              'expires_at':r['ts']+expiry,'breakout':None,'retest':None,'frozen':None})
            if o['breakout'] is None:
                plan=plan_factory(r,d,level)
                if plan:
                    o['breakout']={'ts':r['ts'],'entry':r['price']};o['frozen']=plan
        for o in opportunities.values():
            if o['retest'] is not None or o['breakout'] is None or r['ts']<=o['breakout']['ts'] or r['ts']>o['expires_at']:continue
            mins=r.get('minute_bars') or []
            if not mins:continue
            b=mins[-1];d=o['direction'];level=o['level']
            touched=b['low']<=level<=b['close'] if d==1 else b['close']<=level<=b['high']
            if touched and d*(b['close']-b['open'])>0:
                plan=plan_factory(r,d,level,frozen=o['frozen'])
                if plan:o['retest']={'ts':r['ts'],'entry':r['price']}
    result=[]
    for o in opportunities.values():
        a,b=o['breakout'],o['retest'];row=dict(o)
        row['route_status']='both' if a and b else 'breakout_only' if a else 'retest_only' if b else 'no_entry'
        row['lead_seconds']=b['ts']-a['ts'] if a and b else None
        row['signed_entry_advantage']=o['direction']*(a['entry']-b['entry']) if a and b else None
        if pnl is not None and o['frozen'] is not None:
            row['breakout_outcome']=_outcome(rows,a,o['frozen'],o['direction'],pnl)
            row['retest_outcome']=_outcome(rows,b,o['frozen'],o['direction'],pnl)
            an=row['breakout_outcome']['net'] if row['breakout_outcome'] else None
            bn=row['retest_outcome']['net'] if row['retest_outcome'] else None
            row['net_difference_retest_minus_breakout']=bn-an if an is not None and bn is not None else None
        row['one_and_four_hour_direction_is_reference_only']=True
        result.append(row)
    return sorted(result,key=lambda x:(x['break_start'],x['id']))
