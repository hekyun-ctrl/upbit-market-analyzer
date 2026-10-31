"""Read-only export of one immutable performance SQLite snapshot."""
import argparse, hashlib, json, sqlite3
from pathlib import Path
import lbank_performance_audit as audit


def export(db_path, day):
    uri=Path(db_path).resolve().as_uri()+'?mode=ro'
    db=sqlite3.connect(uri,uri=True);db.row_factory=sqlite3.Row
    try:
        db.execute('BEGIN')
        result={'day':day,'trades':audit.trade_rows(db),
          'pair_comparison':audit.paired_comparison(db),
          'closing_day':audit.closing_day_summary(db,day),
          'coverage':audit.coverage(db)}
        result['snapshot_sha256']=hashlib.sha256(Path(db_path).read_bytes()).hexdigest()
        db.rollback();return result
    finally:db.close()


def main():
    p=argparse.ArgumentParser();p.add_argument('--db',required=True);p.add_argument('--day',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();src=Path(a.db).resolve();dst=Path(a.output).resolve()
    if src==dst:raise SystemExit('output must differ from input database')
    dst.write_text(json.dumps(export(src,a.day),ensure_ascii=False,sort_keys=True,indent=2)+'\n')
if __name__=='__main__':main()
