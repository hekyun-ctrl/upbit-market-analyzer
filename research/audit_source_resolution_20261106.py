"""Offline audit of a finite actual public receipt packet, with no promotion.

Reuses the unchanged received-stream importer. No network, notifications,
orders, final-flag override, strategy run or profitability inference.
"""
import copy
import base64
from decimal import Decimal
import gzip
import hashlib
import io
import json
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from lbank_live_evidence import LiveEvidenceStore
from lbank_received_stream import (ReceivedStreamImporter, chart_decoder,
    receipt_time, seal_segment, strict_json)

DATA = ROOT / 'research/zerobase/source_resolution_20261106'
PINNED = {
    'history_request_definition': 'd279bd43c3e65f8f9fd6ef2d00e9a4e259aa8799952fa87347960ad34d2219d6',
    'volume_converter': '418f03db8a84efe0f7f6bf456fb7328b32006122f39861951fd430be7475aa7e',
    'chart_volume_adapter': '4ffb04d129653f91b71eecb328502964df9d967dc5d3194763f1c90bb18cec3a',
    'wire_key_map': '77483b9729d59d330996bd326cfe5630a04225b2fc06ba144dc97ae437707b06',
}
BASE_BLOBS = {
    'lbank_received_stream.py': '8f0f347795c74a1f438517401b6bd2945ddaa069',
    'lbank_live_evidence.py': '707909c16681dc0791395f7a3814ffe9153f215b',
    'lbank_signal_evidence.py': '2d514fbf1c99798f824f658988ed451a79f6a48a',
}

def sha(raw):
    return hashlib.sha256(raw).hexdigest()

def read(name):
    b = (DATA/name).read_bytes()
    if len(b) > 2*1024*1024:
        raise ValueError('PACKET_FILE_CAP')
    return strict_json(b.decode())

def main():
    for name, expected in BASE_BLOBS.items():
        b=(ROOT/name).read_bytes()
        assert hashlib.sha1(b'blob '+str(len(b)).encode()+b'\0'+b).hexdigest()==expected
    evidence=read('frontend_evidence.json')
    assert {e['role'] for e in evidence['excerpts']} == set(PINNED)
    for e in evidence['excerpts']:
        assert e['url'].startswith('https://www.lbank.com/static-client/lbank-front-trade/')
        assert sha(e['text'].encode()) == e['sha256'] == PINNED[e['role']]
        receipt_time(e['received_at'])
    excerpt={e['role']:e['text'] for e in evidence['excerpts']}
    assert 'volume:r.isInverse?' in excerpt['chart_volume_adapter']
    assert 'b.B_)(t,{symbol:r,volume:l})' in excerpt['chart_volume_adapter']
    assert 'volumeMultiple)||1' in excerpt['volume_converter']
    assert 'n*i/t:n*i' in excerpt['volume_converter']
    assert 'j:"timeZone"' in excerpt['wire_key_map']
    assert 'final' not in excerpt['wire_key_map']

    receipt=read('instrument_receipt.json')
    packed=(DATA/'instrument.raw.json.gz.b64').read_bytes()
    assert len(packed)<2*1024*1024
    with gzip.GzipFile(fileobj=io.BytesIO(base64.b64decode(packed.strip(),validate=True))) as source:
        instrument_raw=source.read(2*1024*1024+1)
    assert len(instrument_raw)<=2*1024*1024
    assert receipt['status']==200 and receipt['bytes']==len(instrument_raw)
    assert receipt['sha256']==sha(instrument_raw)=='3922a08d6cf5e2ea680ab659b5093c9661f05509a0ac6a0032d2b5e7545edfae'
    assert receipt_time(receipt['requested_at']) <= receipt_time(receipt['received_at'])
    instruments=strict_json(instrument_raw.decode())
    assert instruments['success'] is True and instruments['error_code']==0
    btc=[i for i in instruments['data'] if i.get('symbol')=='BTCUSDT']
    assert len(btc)==1
    btc=btc[0]
    assert (btc['baseCurrency'],btc['priceCurrency'],btc['clearCurrency'],btc['exchangeID']) == ('BTC','USDT','USDT','Exchange')
    assert Decimal(str(btc['volumeMultiple']))==1

    capture=read('raw_capture.json');sealed=read('sealed_received.json')
    assert len(capture['records'])==15
    assert capture['opened']['request']['a']['i']=='BTCUSDT_5m'
    assert capture['original_receipt_precision']=='milliseconds'
    last=receipt_time(capture['opened']['at']);geometry=[]
    for seq,r in enumerate(capture['records'],1):
        assert r['seq']==seq and r['session']==capture['opened']['session']
        at=receipt_time(r['received_at']);assert last<=at;last=at
        wire=bytes.fromhex(r['wire_hex']);assert sha(wire)==r['wire_sha256']
        decoded=chart_decoder(wire)
        assert len(decoded)==1
        b=decoded[0]['bar']
        assert b['updated']<=at
        assert decoded[0]['final'] is None and decoded[0]['final_basis'] is None
        # For this pinned BTC/SwapU multiplier=1 packet, quote/base is a
        # price-range consistency check, NOT proof of the provider contract.
        vwap=Decimal(str(b['turnover']))/Decimal(str(b['volume']))
        assert Decimal(str(b['low'])) <= vwap <= Decimal(str(b['high']))
        geometry.append(dict(seq=seq,period=b['period'],start=b['start'],
            volume_original=b['volume'],turnover_original=b['turnover'],
            turnover_div_volume=str(vwap),range_consistent=True,final=None))
        s=sealed['payload']['records'][seq]
        assert s['wire_hex']==r['wire_hex'] and s['received_at']==r['received_at']
    assert last<=receipt_time(capture['ended']['at'])
    checks={}
    with tempfile.TemporaryDirectory() as temp:
        store=LiveEvidenceStore(Path(temp)/'actual.db')
        importer=ReceivedStreamImporter(store)
        result=importer.import_segment(sealed)
        assert result['records']==17 and result['qualified'] is False
        checks['actual_packet_imported']=True
        before=store.db.execute('SELECT count(*) FROM received_records').fetchone()[0]
        assert importer.import_segment(sealed)['duplicate'] is True
        assert store.db.execute('SELECT count(*) FROM received_records').fetchone()[0]==before
        checks['duplicate_import_idempotent']=True
        counts=dict(store.db.execute('SELECT coalesce(final,-1),count(*) FROM received_bars GROUP BY final'))
        assert counts=={-1:15}
        assert store.db.execute('SELECT count(*) FROM received_snapshots').fetchone()[0]==0
        assert store.db.execute('PRAGMA integrity_check').fetchone()[0]=='ok'
        assert store.db.execute('PRAGMA foreign_key_check').fetchall()==[]
        checks['all_final_flags_remain_unknown']=True
        store.close()
        for kind in ('wire_tamper','manufactured_final'):
            bad=copy.deepcopy(sealed['payload'])
            if kind=='wire_tamper': bad['records'][1]['wire_hex']='00'+bad['records'][1]['wire_hex'][2:]
            else: bad['records'][1]['final']=True
            bad_store=LiveEvidenceStore(Path(temp)/(kind+'.db'))
            try:
                ReceivedStreamImporter(bad_store).import_segment(seal_segment(bad))
                raise AssertionError('INVALID_EVIDENCE_ACCEPTED')
            except ValueError as exc:
                expected='RAW_WIRE_HASH_MISMATCH' if kind=='wire_tamper' else 'UNKNOWN_RECEIVED_RECORD_SCHEMA'
                assert str(exc)==expected
                assert bad_store.db.execute('SELECT count(*) FROM received_records').fetchone()[0]==0
                checks[kind+'_rejected_atomically']=True
            finally: bad_store.close()
    report=dict(schema='zb_source_resolution_audit_v1',checks=checks,
        received_frames=15,native_5m_forming_snapshots=1,native_1m_forming_revisions=14,
        original_receipt_start=capture['records'][0]['received_at'],
        original_receipt_end=capture['records'][-1]['received_at'],
        verified_final_bars=0,qualified_unseen_observations=0,independent_closed_5m=0,
        unit_evidence=dict(pinned_frontend_conversion_verified=True,
            btc_instrument_volume_multiple=1,observed_price_range_checks=15,
            h_BTC_i_USDT_consistent_for_this_capture=True,
            formal_provider_candle_unit_contract_confirmed=False,
            inference='Official chart transformation + instrument metadata + arithmetic consistency; scoped to this version/capture.'),
        bars=geometry,global_source_blockers_unchanged=True,
        source_blockers=['RAW5M_INDEPENDENCE_UNVERIFIED','CANDLE_VOLUME_UNIT_UNVERIFIED'],
        additional_required=['DOCUMENTED_FINAL_AND_REVISION_SEMANTICS',
            'INDEPENDENT_CLOSED_NATIVE_5M_AND_OVERLAPPING_1M',
            'CONTINUOUS_ALL_INPUT_AND_QUOTE_RECEIPTS','SETTLED_FUNDING_HISTORY'],
        required_comparison_executed=False,profitability_validated=False,
        net_profit=None,outbound=False,orders=False,live_strategy_changes=False)
    (DATA/'validation.json').write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n')
    print(json.dumps({k:v for k,v in report.items() if k!='bars'},ensure_ascii=False,indent=2))

if __name__=='__main__': main()
