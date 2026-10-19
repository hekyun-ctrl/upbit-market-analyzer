"""Bounded public LBank depth capture. No signals,threads,credentials or orders."""
import argparse
import base64
from collections import Counter
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
import math
import os
from pathlib import Path
import statistics
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[2]
PROTOCOL = Path(__file__).with_name("native_quote_baseline_protocol_20261020.json")
PUBLIC_URL = "https://lbkperp.lbank.com/cfd/openApi/v1/pub/marketOrder?depth=5&symbol=BTCUSDT"
MAX_BYTES = 150000
HEADERS = {"date", "age", "cache-control", "content-type", "content-length", "server", "via", "x-cache"}


def sha(b):
    return hashlib.sha256(b).hexdigest()


def utc():
    return datetime.now(timezone.utc).isoformat()


def load_parser():
    p = json.loads(PROTOCOL.read_text())
    path = ROOT/p["existing_parser"]
    if sha(path.read_bytes()) != p["parser_sha256"]:
        raise ValueError("Reviewed parser changed;restore/review before public capture")
    spec = importlib.util.spec_from_file_location("lbank_readonly_depth_parser", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.extract_features


def metadata_fields(obj, wanted, prefix="$", depth=0):
    out = []
    if depth > 5 or not isinstance(obj, dict):
        return out
    for k, v in obj.items():
        path = prefix+"."+str(k)
        if str(k).lower() in wanted and isinstance(v, (str, int, float)):
            out.append({"path": path, "value": str(v) if isinstance(v,float) and not math.isfinite(v) else v})
        if isinstance(v, dict):
            out.extend(metadata_fields(v, wanted, path, depth+1))
    return out


def classify(raw, extractor):
    payload = json.loads(raw)
    if not isinstance(payload, dict):
        raise ValueError("Depth response must be an object")
    if payload.get("success") is False or payload.get("result") is False:
        raise ValueError("API explicitly reported failure")
    code = payload.get("error_code")
    if code is not None and str(code) not in ("0", "0.0", ""):
        raise ValueError("API error_code="+str(code))
    symbols = metadata_fields(payload, {"symbol"})
    if any(x["value"] != "BTCUSDT" for x in symbols):
        raise ValueError("Response instrument mismatch")
    features = extractor(payload)
    book = payload.get("data", payload)
    normalized = {}
    for side in ("asks", "bids"):
        levels = [(float(x["price"]), float(x["volume"])) for x in book[side][:20]]
        normalized[side] = sorted(levels, reverse=(side == "bids"))[:5]
    return {"features": features,
            "book_sha256": sha(json.dumps(normalized, sort_keys=True, separators=(",", ":")).encode()),
            "response_symbol_fields": symbols,
            "response_symbol_verified": bool(symbols),
            "timestamp_candidates": metadata_fields(payload, {"time", "timestamp", "ts", "t", "updatetime", "lastupdatetime", "eventtime", "servertime"}),
            "exchange_book_event_time_verified": False,
            "event_age_seconds": None}


def get_raw(url, timeout):
    if url != PUBLIC_URL:
        raise ValueError("Only frozen public depth URL is allowed")
    request = Request(url, method="GET", headers={"User-Agent": "LBankPublicDepthBaseline/1.0", "Accept": "application/json"})
    try:
        response = urlopen(request, timeout=timeout)
    except HTTPError as exc:
        response = exc
    with response:
        status = response.status
        headers = {k.lower(): v for k, v in response.headers.items() if k.lower() in HEADERS}
        raw = response.read(MAX_BYTES+1)
        final_url = response.geturl()
    return status, headers, raw, final_url


def quantiles(values):
    if not values:
        return None
    values = sorted(values)
    p = (len(values)-1)*.95
    p95 = values[int(p)]+(values[math.ceil(p)]-values[int(p)])*(p-int(p))
    return {"min": values[0], "median": statistics.median(values), "p95": p95, "max": values[-1]}


def summarize(rows, requested):
    good = [r for r in rows if r["parse_status"] == "valid_depth"]
    raw_counts = Counter(r["payload_sha256"] for r in good)
    book_counts = Counter(r["book_sha256"] for r in good)
    starts = [datetime.fromisoformat(r["request_start_utc"]) for r in rows]
    return {"requested_attempts": requested, "attempted": len(rows), "successful_depth": len(good),
        "failed_or_invalid": len(rows)-len(good), "unattempted": requested-len(rows),
        "HTTP_status_counts": dict(Counter(str(r["HTTP_status"]) for r in rows)),
        "error_records": [{"attempt": r["attempt"], "status": r["HTTP_status"], "error": r.get("error")} for r in rows if r["parse_status"] != "valid_depth"],
        "HTTP_roundtrip_seconds": quantiles([r["monotonic_request_duration_seconds"] for r in rows]),
        "request_start_gaps_seconds": [(b-a).total_seconds() for a,b in zip(starts[:-1], starts[1:])],
        "spread_bps": quantiles([r["features"]["spread_bps"] for r in good]),
        "raw_unique": len(raw_counts), "raw_duplicate_observations": sum(n-1 for n in raw_counts.values()),
        "book_unique": len(book_counts), "book_duplicate_observations": sum(n-1 for n in book_counts.values()),
        "response_symbol_verified": sum(r["response_symbol_verified"] for r in good),
        "rows_with_any_timestamp_candidate": sum(bool(r["timestamp_candidates"]) for r in good),
        "verified_book_event_times": 0, "unknown_book_freshness": len(good),
        "source_event_time_policy": "Unverified fields/HTTP Date are not book generation time;RTT/2 is not one-way latency.",
        "profitability_tested": False, "manual_fills_verified": False, "candidate_forward_started": False}


def capture(output, getter=get_raw, clock=time.monotonic, sleeper=time.sleep, now=utc, extractor=None):
    p = json.loads(PROTOCOL.read_text())
    fixed = p["fixed_capture"]
    assert p["public_endpoint_from_existing_reviewed_code"] == PUBLIC_URL
    assert fixed["maximum_attempts"] == 12 and fixed["target_interval_seconds"] == 5
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    extractor = extractor or load_parser()
    start = clock()
    deadline = start+fixed["hard_stop_seconds"]
    session = {"started_utc": now(), "protocol_sha256": sha(PROTOCOL.read_bytes()),
        "code_sha256": sha(Path(__file__).read_bytes()), "parser_sha256": p["parser_sha256"],
        "URL": PUBLIC_URL, "fixed_capture": fixed, "background_continuation": False,
        "freshness_policy": "No inferred event-age or fills"}
    (output/"session.json").write_text(json.dumps(session, indent=2)+"\n")
    rows, stop = [], "maximum_attempts_completed"
    with (output/"raw.jsonl").open("x") as sink:
        for i in range(fixed["maximum_attempts"]):
            due = start+i*fixed["target_interval_seconds"]
            wait = min(max(0, due-clock()), max(0, deadline-clock()))
            if wait:
                sleeper(wait)
            if clock() >= deadline:
                stop = "hard_stop_before_next_attempt"
                break
            t0 = clock()
            row = {"attempt": i+1, "request_start_utc": now(), "request_start_elapsed_seconds": t0-start,
                "HTTP_status": None, "response_headers": {}, "parse_status": "request_error",
                "raw_response": None, "raw_response_base64": None, "payload_sha256": None,
                "raw_complete": False}
            try:
                status, headers, raw, final_url = getter(PUBLIC_URL, min(fixed["http_timeout_seconds"], deadline-t0))
                row.update(HTTP_status=status, response_headers=headers, final_url=final_url,
                    payload_sha256=sha(raw), raw_bytes=len(raw), raw_complete=len(raw)<=MAX_BYTES)
                try:
                    row["raw_response"] = raw.decode("utf-8")
                except UnicodeDecodeError:
                    row["raw_response_base64"] = base64.b64encode(raw).decode()
                if final_url != PUBLIC_URL:
                    raise ValueError("Redirected endpoint was not certified")
                if not 200 <= status < 300:
                    raise ValueError("HTTP status="+str(status))
                if not row["raw_complete"]:
                    raise ValueError("Response exceeds capture limit;partial only")
                row.update(classify(raw, extractor))
                row["parse_status"] = "valid_depth"
            except Exception as exc:
                row["error"] = type(exc).__name__+": "+str(exc)[:300]
            row["response_received_utc"] = now()
            row["monotonic_request_duration_seconds"] = clock()-t0
            row["response_elapsed_seconds"] = clock()-start
            sink.write(json.dumps(row, ensure_ascii=False, allow_nan=False)+"\n")
            sink.flush(); os.fsync(sink.fileno())
            rows.append(row)
            print(json.dumps({"attempt": i+1, "status": row["HTTP_status"], "parse": row["parse_status"]}), flush=True)
            if row["HTTP_status"] in (401,403,429):
                stop = "permission_or_rate_limit_stop_no_bypass"
                break
            if row.get("error") and any("error_code="+str(c) in row["error"] for c in [176,177,178,179,183,10002,10003,10007,10008,10009,10010,10012]):
                stop = "API_permission_or_rate_limit_stop_no_credentials"
                break
    report = {**session, "completed_utc": now(), "duration_seconds": clock()-start,
        "stop_reason": stop, "summary": summarize(rows, fixed["maximum_attempts"]),
        "raw_jsonl_sha256": sha((output/"raw.jsonl").read_bytes()),
        "raw_jsonl_bytes": (output/"raw.jsonl").stat().st_size,
        "production_changed": False, "account_or_order_access": False,
        "interpretation": "One bounded public depth input-quality baseline,not alpha or forward strategy validation."}
    (output/"report.json").write_text(json.dumps(report, indent=2, allow_nan=False)+"\n")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--capture", action="store_true", required=True)
    parser.add_argument("--output", type=Path, required=True)
    a = parser.parse_args()
    r = capture(a.output)
    print(json.dumps(r["summary"], indent=2))
