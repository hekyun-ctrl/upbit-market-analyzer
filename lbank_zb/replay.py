"""python -m lbank_zb.replay --input native.jsonl --output result.json
Strict normalized1m/5m input; no API calls, orders, outbound alerts, invented fees.
"""
import argparse,json,hashlib
from pathlib import Path
from .core import *
from .store import Store,bar_from
from .engine import Engine
from .context import VERSION as CONTEXT_VERSION

def replay(path,costs,latency=60,strategies=None,db=':memory:',context_mode='record'):
    events=[json.loads(x) for x in Path(path).read_text().splitlines() if x.strip()]
    # Arrival order is authoritative. Same arrival: raw1m then native5m.
    if any(events[i]['received']>events[i+1]['received'] for i in range(len(events)-1)):raise ValueError('ARRIVAL_ORDER')
    events.sort(key=lambda r:(r['received'],r['start']+r['period'],r['period']))
    store=Store(db,Engine(costs,latency,strategies,context_mode));bar_hashes=[]
    try:
        if store.engine.context_mode!=context_mode:raise ValueError('CONTEXT_MODE_DIFFERS_FROM_SNAPSHOT')
        for raw in events:
            b=bar_from(raw);store.ingest(b,b.received);bar_hashes.append(digest(b))
        e=store.engine;report=e.report();report.update({'input_SHA256':hashlib.sha256(Path(path).read_bytes()).hexdigest(),'event_hash':digest(e.events),'code_version':'ZB-1.0','parameter_hash':digest({'config':config(),'costs':costs,'latency':latency,'strategies':sorted(e.strategies)}),'raw_records':len(events),'unique_records':len(e.seen),'data_start':events[0]['start'] if events else None,'data_end':max(r['start']+r['period'] for r in events) if events else None,'events':e.events})
        report['higher_timeframes']={'version':CONTEXT_VERSION,'mode':e.context_mode,'signals':e.signal_contexts}
        report['parameter_hash']=digest({'config':config(),'costs':costs,'latency':latency,'strategies':sorted(e.strategies),'higher_timeframes':{'version':CONTEXT_VERSION,'mode':e.context_mode}})
        return report
    finally:store.close()

def main():
    a=argparse.ArgumentParser();a.add_argument('--input',required=True);a.add_argument('--output',required=True);a.add_argument('--scenario',help='Explicit cost/product assumptions JSON; omitted means UNKNOWN');a.add_argument('--latency',type=int,choices=[60,120],default=60);a.add_argument('--db',default=':memory:');a.add_argument('--context-mode',choices=['off','record'],default='record');args=a.parse_args()
    from .store import costs_from
    costs=costs_from(json.loads(Path(args.scenario).read_text())) if args.scenario else Costs(None,None,D(2),None,None,None,None)
    result=replay(args.input,costs,args.latency,db=args.db,context_mode=args.context_mode);Path(args.output).write_text(json.dumps(result,default=encode,indent=2,ensure_ascii=False)+'\n')
if __name__=='__main__':main()
