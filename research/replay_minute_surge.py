"""Frozen development comparison; no historical quotes are invented."""
import base64,collections,csv,datetime as dt,hashlib,json,pathlib,sys,zlib
ROOT=pathlib.Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from minute_surge import MinuteSurgeEngine
OUT=ROOT/'research/upbit_minute_surge_20261026'
UTC=dt.timezone.utc;KST=dt.timezone(dt.timedelta(hours=9))

def text(t):return dt.datetime.fromtimestamp(t,UTC).astimezone(KST).isoformat()

def outcome(rows,start,entry,stop,target):
    deadline=start+21600
    for b in rows:
        if b['start']<start:continue
        if b['start']>=deadline:break
        sl=b['low']<=stop;tp=b['high']>=target
        if b['open']<=stop:return dict(status='stop_gap_model',at=text(b['start']),gross_pct=(b['open']/entry-1)*100)
        if b['open']>=target:return dict(status='target_model',at=text(b['start']),gross_pct=(target/entry-1)*100)
        if sl and tp:return dict(status='same_minute_order_unknown',at=text(b['start']))
        if sl or tp:return dict(status='target_model' if tp else 'stop_model',at=text(b['start']),gross_pct=((target if tp else stop)/entry-1)*100)
    return dict(status='unknown',reason='no_exit_before_available_data_end_or_horizon')

def main():
    source=json.loads((OUT/'candles_archive.json').read_text())
    raw=zlib.decompress(base64.b64decode(source['zlib_base64']));assert hashlib.sha256(raw).hexdigest()==source['raw_sha256']
    data=json.loads(raw);engine=MinuteSurgeEngine();events=[];summary={}
    first=dt.datetime(2026,10,24,9,tzinfo=KST).timestamp();cutoff=dt.datetime(2026,10,25,12,40,tzinfo=UTC).timestamp()
    for market,values in sorted(data.items()):
        rows=[dict(zip(('start','open','high','low','close','volume','value'),v)) for v in values]
        counts=collections.Counter()
        for i,b in enumerate(rows):
            assert 0<b['low']<=min(b['open'],b['close'])<=max(b['open'],b['close'])<=b['high']
            if b['start']+60>cutoff:continue
            alert=engine.completed(market,{k:v for k,v in b.items() if k!='volume'},b['start']+60)
            if not alert or b['start']+60<first:continue
            p=alert['surge_proof'];counts[alert['signal']]+=1
            entry_bar=rows[i+1] if i+1<len(rows) and rows[i+1]['start']==b['start']+60 else None
            e=entry_bar['open'] if entry_bar else None
            risk=(e-p['structural_low'])/e*100 if e else None
            price_admitted=bool(e and p['level']<=e<=min(b['close']*1.01,p['level']*1.025) and 0<risk<=3)
            result=outcome(rows,entry_bar['start'],e,e*.97,e*1.10) if entry_bar else dict(status='unknown',reason='no_next_minute')
            events.append(dict(event_id=p['setup_id'],parent_id=alert.get('parent_signal_id'),market=market,kind=alert['signal'],
                source_start_kst=text(b['start']),nominal_completed_at=text(b['start']+60),
                next_5m_close=text((b['start']//300+1)*300),five_minute_boundary_lead_seconds=(b['start']//300+1)*300-(b['start']+60),
                prior_level=p['level'],structural_low=p['structural_low'],turnover=b['value'],turnover_ratio=p['turnover_ratio'],
                next_minute_open=e,price_and_raw_structure_only=price_admitted,
                final_entry_status='UNVERIFIED_NO_HISTORICAL_ORDERBOOK_OR_RECEIPT_TIME',modeled_3pct_stop_10pct_target=result,
                cost_scenario_pct=.2,model_net_pct=result.get('gross_pct')-.2 if 'gross_pct' in result else None,
                baseline_actual_alert_time=None,new_actual_alert_time=None,
                limitation='Signals, not sends. 5m boundary is a timing reference, not old-policy replay. Next-minute open omits API/survival/execution delay. Price-only first touch, not actual fills.'))
        group=[e for e in events if e['market']==market]
        summary[market]=dict(observation_minutes=len(rows),first_source=text(rows[0]['start']),last_source=text(rows[-1]['start']),
            detected=dict(counts),price_structure_admitted=sum(e['price_and_raw_structure_only'] for e in group),
            hypothetical_outcomes=dict(collections.Counter(e['modeled_3pct_stop_10pct_target']['status'] for e in group)),
            net_sum_with_unknowns=None,actual_entry_count=None,actual_sends=0)
    result=dict(diagnostic_only=True,profitability_validated=False,events=events,markets=summary,
        source_sha256=hashlib.sha256((OUT/'candles_archive.json').read_bytes()).hexdigest(),
        code_sha256={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in [pathlib.Path(__file__),ROOT/'minute_surge.py']},
        policy='one completed minute, not long-duration accumulation, RSI or volatility compression; restart after failed old level',
        cohort='Four known >100% winners plus six purposive non-100% controls; selected development data',
        qualification='Historical depth/quote timestamps absent: final admission and delivered frequency cannot be backfilled or claimed.')
    (OUT/'comparison.json').write_text(json.dumps(result,ensure_ascii=False,indent=2)+'\n')
    flat=[]
    for e in events:
        flat.append({k:json.dumps(v,ensure_ascii=False) if isinstance(v,dict) else v for k,v in e.items()})
    with (OUT/'events.csv').open('w',newline='') as f:
        w=csv.DictWriter(f,fieldnames=list(flat[0]),lineterminator='\n');w.writeheader();w.writerows(flat)
    print(json.dumps(summary,ensure_ascii=False,indent=2));print('events',len(events))

if __name__=='__main__':main()
