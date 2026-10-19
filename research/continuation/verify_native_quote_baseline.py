"""Check saved native depth bytes and timing without making network requests."""
import argparse
import base64
from collections import Counter
from datetime import datetime
from decimal import Decimal
import hashlib
import json
import math
from pathlib import Path
import statistics


def verify(directory):
    directory=Path(directory)
    raw=(directory/"raw.jsonl").read_bytes()
    report=json.loads((directory/"report.json").read_text())
    assert len(raw)==report["raw_jsonl_bytes"]
    assert hashlib.sha256(raw).hexdigest()==report["raw_jsonl_sha256"]
    rows=[json.loads(line) for line in raw.splitlines()]
    assert [r["attempt"] for r in rows]==list(range(1,len(rows)+1))
    assert len(rows)<=12
    valid,fail=[],[]
    measured_spreads=[]
    for row in rows:
        assert row["monotonic_request_duration_seconds"]>=0
        start=datetime.fromisoformat(row["request_start_utc"])
        end=datetime.fromisoformat(row["response_received_utc"])
        assert abs((end-start).total_seconds()-row["monotonic_request_duration_seconds"])<.1
        if row["raw_response"] is not None:
            b=row["raw_response"].encode()
        elif row["raw_response_base64"] is not None:
            b=base64.b64decode(row["raw_response_base64"])
        else:
            assert row["payload_sha256"] is None
            fail.append(row)
            continue
        assert hashlib.sha256(b).hexdigest()==row["payload_sha256"]
        if row["parse_status"]!="valid_depth":
            fail.append(row);continue
        p=json.loads(b);d=p.get("data",p)
        assert d["symbol"]=="BTCUSDT" and row["HTTP_status"]==200
        asks=sorted((Decimal(str(x["price"])),Decimal(str(x["volume"]))) for x in d["asks"])
        bids=sorted(((Decimal(str(x["price"])),Decimal(str(x["volume"]))) for x in d["bids"]),reverse=True)
        ask,bid=asks[0][0],bids[0][0]
        assert ask>bid>0
        assert all(v>0 for _,v in asks+bids)
        spread=float((ask-bid)/((ask+bid)/2)*10000)
        assert abs(spread-row["features"]["spread_bps"])<=.0000051
        assert not row["exchange_book_event_time_verified"] and row["event_age_seconds"] is None
        measured_spreads.append(spread);valid.append(row)
    s=report["summary"]
    assert len(valid)==s["successful_depth"] and len(fail)==s["failed_or_invalid"]
    assert len(rows)==s["attempted"] and 12-len(rows)==s["unattempted"]
    assert len(set(r["payload_sha256"] for r in valid))==s["raw_unique"]
    durations=[r["monotonic_request_duration_seconds"] for r in rows]
    assert abs(statistics.median(durations)-s["HTTP_roundtrip_seconds"]["median"])<1e-12
    return {"status":"raw_bytes_hashes_decimal_spreads_and_capture_times_reconciled",
        "report_sha256":hashlib.sha256((directory/"report.json").read_bytes()).hexdigest(),
        "records_checked":len(rows),"valid_depth":len(valid),"timeouts_or_errors":len(fail),
        "unverified_book_event_times":len(valid),"spread_bps_unrounded":measured_spreads,
        "HTTP_statuses":dict(Counter(str(r["HTTP_status"]) for r in rows)),
        "median_request_duration_seconds":statistics.median(durations),
        "independence":"Separate decimal/scalar check by same researcher,not external institutional validation.",
        "native_fills_verified":False,"strategy_forward_started":False}


if __name__=="__main__":
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("directory",type=Path)
    p.add_argument("--output",type=Path)
    a=p.parse_args();out=json.dumps(verify(a.directory),indent=2,allow_nan=False)+"\n"
    if a.output:a.output.write_text(out)
    print(out)
