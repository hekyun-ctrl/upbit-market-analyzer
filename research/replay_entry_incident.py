"""Verify an incident archive checksum and repeat its pure native entry gates."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import sys
import subprocess
import types
import zlib

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import lbank_futures_monitor as monitor
from lbank_entry_replay_audit import replay


def decode_archive(logs):
    records=logs.get('structuredContent',logs).get('deploy',[])
    groups={}
    for row in records:
        message=row.get('message','')
        if 'ENTRY_REPLAY_ARCHIVE ' not in message:continue
        part=json.loads(message.split('ENTRY_REPLAY_ARCHIVE ',1)[1])
        group=groups.setdefault(part['sha256'],{})
        previous=group.get(part['index'])
        if previous is not None and previous!=part:raise ValueError('conflicting archive chunk')
        group[part['index']]=part
    complete=[]
    for sha,parts in groups.items():
        count=next(iter(parts.values()))['chunks']
        if sorted(parts)!=list(range(count)):continue
        encoded=''.join(parts[i]['payload'] for i in range(count))
        if len(encoded)>1024*1024:raise ValueError('oversized archive')
        compressed=base64.b64decode(encoded,validate=True)
        decoder=zlib.decompressobj();raw=decoder.decompress(compressed,4*1024*1024)
        if not decoder.eof or decoder.unconsumed_tail:raise ValueError('oversized/incomplete compressed archive')
        if hashlib.sha256(raw).hexdigest()!=sha:raise ValueError('archive checksum mismatch')
        complete.append((json.loads(raw),sha))
    if not complete:raise ValueError('no complete verified archive in bounded logs')
    return complete[-1]


def main():
    parser=argparse.ArgumentParser()
    source=parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--railway-log');source.add_argument('--bundle')
    parser.add_argument('--save-bundle');parser.add_argument('--output')
    parser.add_argument('--source-ref',help='Default: archived runtime commit; WORKTREE explicitly uses changed code')
    parser.add_argument('--allow-policy-change',action='store_true')
    args=parser.parse_args()
    if args.railway_log:
        bundle,sha=decode_archive(json.loads(Path(args.railway_log).read_text()))
    else:
        bundle=json.loads(Path(args.bundle).read_text())
        sha=hashlib.sha256(json.dumps(bundle,sort_keys=True,ensure_ascii=False,allow_nan=False).encode()).hexdigest()
    source_ref=args.source_ref or bundle.get('runtime_source_commit')
    gates=monitor
    source_sha=hashlib.sha256(Path(monitor.__file__).read_bytes()).hexdigest()
    if source_ref and source_ref!='WORKTREE':
        source=subprocess.check_output(['git','show',source_ref+':lbank_futures_monitor.py'],
                                       cwd=Path(__file__).resolve().parents[1])
        source_sha=hashlib.sha256(source).hexdigest()
        gates=types.ModuleType('pinned_incident_monitor')
        gates.__file__=monitor.__file__
        exec(compile(source,'<pinned-incident-monitor>','exec'),gates.__dict__)
    mapping=dict(fee='TAKER_FEE',slippage='STOP_SLIPPAGE',funding_buffer='FUNDING_BUFFER',
        reference_balance='REF_BALANCE',risk_fraction='RISK_FRACTION',leverage='LEVERAGE',
        max_margin_fraction='MAX_MARGIN_FRACTION')
    for key,field in mapping.items():
        if key in bundle.get('model_config',{}):setattr(gates,field,bundle['model_config'][key])
    result=replay(bundle,gates.classify,gates.confirm_watch)
    output=dict(archive_sha256=sha,gate_source=source_ref,gate_source_sha256=source_sha,result=result,
                matches_archived_result=result==bundle.get('result'),policy_comparison=args.allow_policy_change)
    if args.save_bundle:Path(args.save_bundle).write_text(json.dumps(bundle,sort_keys=True,ensure_ascii=False,indent=2)+'\n')
    rendered=json.dumps(output,sort_keys=True,ensure_ascii=False,indent=2)+'\n'
    if args.output:Path(args.output).write_text(rendered)
    print(rendered)
    if result!=bundle.get('result') and not args.allow_policy_change:
        raise SystemExit('replay differs from archived result')


if __name__=='__main__':main()
