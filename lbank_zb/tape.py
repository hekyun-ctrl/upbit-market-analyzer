"""Replay a saved integration event tape without network or notifications.

python -m lbank_zb.tape --input events.jsonl --output report.json
Each row: id, kind, received, payload. Costs default to UNKNOWN; evidence
events carry versioned public costs. No source label grants performance proof.
"""
import argparse
import json
from pathlib import Path
from .core import Costs,D,encode
from .journal import replay_events


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--input',required=True)
    parser.add_argument('--output',required=True)
    parser.add_argument('--db',default=':memory:')
    parser.add_argument('--latency',type=int,choices=[60,120],default=60)
    args=parser.parse_args()
    rows=[json.loads(line) for line in Path(args.input).read_text().splitlines() if line.strip()]
    report=replay_events(rows,Costs(None,None,D(2),None,None,None,None),args.db,args.latency)
    Path(args.output).write_text(json.dumps(report,default=encode,ensure_ascii=False,indent=2)+'\n')


if __name__=='__main__':main()
