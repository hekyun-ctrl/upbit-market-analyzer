"""Bounded keyless native stream -> ZB journal -> restart acceptance probe.
No transport, orders, account requests, historical-data or profit claims.
"""
import asyncio
import hashlib
import json
import sys
import tempfile
import time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import aiohttp
from lbank_native_candles import NativeCandleStore, WS_URL, decode_frame
from lbank_zb.public import PublicClient
from lbank_zb.core import Costs,D,dumps,encode
from lbank_zb.live import LiveEngine
from lbank_zb.journal import EventStore
from lbank_zb.service import EngineThread
from lbank_zb.store import snapshot


async def capture(path,seconds=90):
    native=NativeCandleStore(path)
    async with aiohttp.ClientSession(trust_env=True) as client:
        async with client.ws_connect(WS_URL,timeout=12,receive_timeout=20) as ws:
            await ws.send_json({'x':2,'y':'2000000001','z':1,
                                'a':{'i':'BTCUSDT_5m'},'e':json.dumps({'bvc':'202','isUsd':1})})
            async def heartbeat():
                while True:
                    await asyncio.sleep(6)
                    await ws.send_json({'action':'ping','ping':str(int(time.time()*1000))})
            task=asyncio.create_task(heartbeat())
            stop=time.monotonic()+seconds
            try:
                while time.monotonic()<stop:
                    msg=await ws.receive(timeout=20)
                    if msg.type in (aiohttp.WSMsgType.TEXT,aiohttp.WSMsgType.BINARY):
                        native.ingest(decode_frame(msg.data),time.monotonic(),int(time.time()))
                return {'arrivals':native.db.execute('SELECT count(*) FROM native_arrivals').fetchone()[0],
                        'closed_1m':native.db.execute("SELECT count(*) FROM native_bars WHERE period=60 AND quality='stream_closed'").fetchone()[0]}
            finally:
                task.cancel()
                await asyncio.gather(task,return_exceptions=True)
                native.db.close()


def main():
    result={'orders':0,'messages':0,'actual_fills':False,'performance_claim':False}
    with tempfile.TemporaryDirectory() as tmp:
        try:
            result.update(asyncio.run(capture(tmp+'/native.sqlite')))
            client=PublicClient()
            worker=EngineThread(native_path=tmp+'/native.sqlite',state_path=tmp+'/engine.sqlite',client=client)
            s=EventStore(tmp+'/engine.sqlite',LiveEngine(Costs(None,None,D(2),None,None,None,None)))
            worker.poll(s,int(time.time()))
            before=snapshot(s.engine)
            inputs=s.db.execute('SELECT key,kind,received,payload FROM external_inputs ORDER BY rowid').fetchall()
            result.update({'journal_inputs':len(inputs),'native_1m_ingested':len(s.engine.one),
                           'blocked_reasons':s.engine.entry_blocks,
                           'public_metadata_observed':worker.evidence['record']})
            s.close()
            s=EventStore(tmp+'/engine.sqlite',LiveEngine(Costs(None,None,D(2),None,None,None,None)))
            result['restart_state_equal']=snapshot(s.engine)==before
            duplicate_results=[s.consume(key,kind,json.loads(payload),received) for key,kind,received,payload in inputs]
            result['all_duplicates_ignored']=not any(duplicate_results)
            result['no_ready']=not s.engine.counts.get('READY')
            result['snapshot_sha256']=hashlib.sha256(before.encode()).hexdigest()
            s.close()
            result['acceptance_passed']=bool(result['closed_1m'] and result['restart_state_equal'] and result['all_duplicates_ignored'] and result['no_ready'])
        except Exception as exc:
            result.update({'acceptance_passed':False,'error':type(exc).__name__+': '+str(exc)[:180]})
    Path('research/zerobase/forward_acceptance_20261023.json').write_text(json.dumps(result,default=encode,indent=2)+'\n')
    print(json.dumps(result,default=encode))


if __name__=='__main__':main()
