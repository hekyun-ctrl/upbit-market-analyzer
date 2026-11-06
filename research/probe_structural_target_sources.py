"""Finite public-source audit; no keys, private routes, collector or verifier toggles."""
import asyncio
import datetime
import hashlib
import json
from pathlib import Path
import urllib.error
import urllib.request
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor

OUT=Path(__file__).resolve().parent/'zerobase/completion_20261028/source_probe'
URLS=[
 'https://www.lbank.com/docs/contract.html',
 'https://lbkperp.lbank.com/cfd/openApi/v1/pub/instrument?productGroup=SwapU',
 'https://lbkperp.lbank.com/cfd/openApi/v1/pub/getTime',
 'https://lbkperp.lbank.com/cfd/openApi/v1/pub/marketData?productGroup=SwapU']

def utc():return datetime.datetime.now(datetime.timezone.utc).isoformat()
def http(item):
 i,url=item;row=dict(url=url,requested_at=utc(),kind='keyless_public_GET')
 try:
  with urllib.request.urlopen(url,timeout=12) as response:
   body=response.read(3000000);row.update(http_status=response.status)
 except urllib.error.HTTPError as error:
  body=error.read(3000000);row.update(http_status=error.code,error=type(error).__name__)
 except Exception as error:
  body=b'';row.update(error=type(error).__name__,detail=str(error)[:200])
 row.update(received_at=utc(),bytes=len(body),sha256=hashlib.sha256(body).hexdigest())
 # Raw public error/response bodies contain no request credentials.
 (OUT/f'http_{i}.txt').write_bytes(body)
 return row

async def ws_probe():
 import aiohttp
 routes=[('wss://lbkperpws.lbank.com/ws',None,'official_docs_no_candle_subscription_schema'),
         ('wss://uuws.rerrkvifj.com/ws/v3',
          {'x':2,'y':'2000000001','z':1,'a':{'i':'BTCUSDT_5m'},'e':json.dumps({'bvc':'202','isUsd':1})},
          'existing_collector_same_feed_not_independent')]
 rows=[]
 for url,subscription,basis in routes:
  for attempt in (1,2):
   row=dict(url=url,attempt=attempt,requested_at=utc(),basis=basis,subscription=subscription)
   frames=[]
   try:
    async with aiohttp.ClientSession(trust_env=True,timeout=aiohttp.ClientTimeout(total=10)) as session:
     async with session.ws_connect(url,timeout=6,receive_timeout=3,max_msg_size=1000000) as ws:
      row['handshake']='connected'
      if subscription:await ws.send_json(subscription)
      for _ in range(3):
       message=await ws.receive(timeout=3)
       if message.type not in (aiohttp.WSMsgType.TEXT,aiohttp.WSMsgType.BINARY):break
       raw=message.data.encode() if isinstance(message.data,str) else message.data
       frames.append(dict(received_at=utc(),sha256=hashlib.sha256(raw).hexdigest(),bytes=len(raw),hex=raw.hex()))
   except Exception as error:
    row.update(error=type(error).__name__,detail=str(error)[:300])
   row.update(ended_at=utc(),frames=frames)
   rows.append(row)
 return rows

def analyze(report):
 records=[]
 for connection in report['websocket']:
  for frame in connection['frames']:
   raw=bytes.fromhex(frame['hex'])
   if hashlib.sha256(raw).hexdigest()!=frame['sha256']:raise ValueError('RAW_HASH_MISMATCH')
   message=json.loads(raw);data=message.get('d',[])
   if isinstance(data,dict):data=[data]
   for bar in data:
    if bar.get('a')!='BTCUSDT' or bar.get('b') not in ('1m','5m'):continue
    record=dict(route=connection['url'],connection_attempt=connection['attempt'],
     received_at=frame['received_at'],raw_sha256=frame['sha256'],interval=bar['b'],
     source_bar_start=bar['c'],source_update_at=bar['k'],source_frame_ms=message.get('w'),
     source_bar_start_utc=datetime.datetime.fromtimestamp(bar['c'],datetime.timezone.utc).isoformat(),
     source_update_utc=datetime.datetime.fromtimestamp(bar['k'],datetime.timezone.utc).isoformat(),
     nominal_bar_close=bar['c']+(60 if bar['b']=='1m' else 300),
     volume_field_h=bar['h'],turnover_field_i=bar['i'],j_raw=bar.get('j'),tradeTime_raw=bar.get('tradeTime'),
     closed_flag=None,closure_basis='not documented; these updates precede nominal close',
     volume_unit='UNVERIFIED',independent_source=False)
    record['received_minus_source_update_seconds']=(datetime.datetime.fromisoformat(frame['received_at']).timestamp()-bar['k'])
    records.append(record)
 keys=Counter((r['interval'],r['source_bar_start']) for r in records)
 report['raw_candle_records']=records
 report['observations']=dict(raw_1m_messages=sum(r['interval']=='1m' for r in records),
  raw_5m_messages=sum(r['interval']=='5m' for r in records),independent_5m=0,
  usable_verified_closed_bars=0,closed_flag=None,volume_unit=None,
  exact_duplicate_raw_frames=sum(n-1 for n in Counter(r['raw_sha256'] for r in records).values()),
  repeated_bar_key_updates=sum(n-1 for n in keys.values()),
  unique_bar_keys=len(keys),gaps=None,
  restart_attempts=dict(Counter(c['url'] for c in report['websocket'])),
  restart_continuity='same forming bar reappeared with later cumulative update; no closed-bar rollover or durable recovery demonstrated',
  source_timestamp_range=[min(r['source_update_at'] for r in records),max(r['source_update_at'] for r in records)] if records else None)
 report['limitation']='Raw chart-feed candle updates obtained, all forming. Same-feed 5m is not independent. No official final-flag or candle-volume-unit schema; gaps and restart closure continuity unmeasured.'
 return report

if __name__=='__main__':
 OUT.mkdir(parents=True,exist_ok=True)
 if '--analyze-existing' in sys.argv:
  report=analyze(json.loads((OUT/'audit.json').read_text()))
  (OUT/'audit.json').write_text(json.dumps(report,indent=2)+'\n')
  print(json.dumps(report['observations'],indent=2));sys.exit(0)
 with ThreadPoolExecutor(max_workers=4) as pool:requests=list(pool.map(http,enumerate(URLS)))
 websocket=asyncio.run(ws_probe())
 report=dict(started_at=requests[0]['requested_at'],ended_at=utc(),http=requests,websocket=websocket,
  blockers=['RAW5M_INDEPENDENCE_UNVERIFIED','CANDLE_VOLUME_UNIT_UNVERIFIED'],
  verified=False,independent_5m=False,candle_volume_unit='UNVERIFIED',
  observations=dict(native_1m=0,independent_5m=0,closed_flag=None,source_timestamp=None,
    duplicates=None,gaps=None,restart_continuity=None,volume_unit=None),
  limitation='No usable candle records obtained. Null quality metrics are unmeasured, not clean.',
  official_documentation=dict(url=URLS[0],checked_via='web retrieval, because local request 403',
    documented_public_routes=['getTime','instrument','marketData','marketOrder'],
    futures_kline_schema_found=False,candle_volume_unit_schema_found=False,
    next_evidence_needed='Official futures candle subscription/schema or authenticated-free official REST route; independently published native 5m with raw receipt records, overlap, final flags and volume-unit definition. Do not use spot kline.'),
  same_feed_warning='Existing collector aggregates 1m into 5m. Raw 5m channel on that same feed alone does not certify independent publication.')
 report=analyze(report)
 (OUT/'audit.json').write_text(json.dumps(report,indent=2)+'\n')
 print(json.dumps({k:v for k,v in report.items() if k not in ('http','websocket')},indent=2))
