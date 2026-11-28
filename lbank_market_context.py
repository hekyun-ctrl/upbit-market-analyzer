"""BTC/USDT market-share context, never a live entry/exit/risk-size gate.

CoinGecko's universe is NOT TradingView's top-125 or CoinMarketCap's universe.
Collect timestamped observations, not invented native 5m dominance candles.
All network and SQLite I/O live in an independent daemon. The trading loop
only reads a bounded in-memory snapshot and continues if this source fails.
"""
import copy
import json
import logging
import math
import os
import sqlite3
import threading
import time
import urllib.request
from datetime import datetime, timedelta, timezone

LOG = logging.getLogger("lbank_market_context")
SOURCE = "coingecko_global_v1"
GLOBAL_URL = "https://api.coingecko.com/api/v3/global"
COINS_URL = "https://api.coingecko.com/api/v3/coins/markets?vs_currency=usd&ids=bitcoin,tether&sparkline=false"
POLL = 300
MAX_AGE = 900
MAX_SKEW = 600
HISTORY_TOLERANCE = 600
PEG_OBSERVATION = .003  # 0.30%, informational, not a depeg confirmation.
KST = timezone(timedelta(hours=9))


def fetch_json(url):
    if url not in (GLOBAL_URL, COINS_URL):
        raise ValueError("unsupported market source")
    headers = {"Accept":"application/json", "User-Agent":"LBankContextMonitor/1.0"}
    if os.getenv("COINGECKO_DEMO_API_KEY"):
        headers["x-cg-demo-api-key"] = os.environ["COINGECKO_DEMO_API_KEY"]
    request = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(request, timeout=8) as response:
        if not response.geturl().startswith("https://api.coingecko.com/"):
            raise ValueError("unexpected redirect")
        payload = response.read(1_000_001)
        if len(payload)>1_000_000:
            raise ValueError("oversized response")
        return json.loads(payload)


def number(value):
    value = float(value)
    if not math.isfinite(value) or value<=0:
        raise ValueError("invalid market number")
    return value


def timestamp(value):
    dt = datetime.fromisoformat(value.replace("Z","+00:00"))
    if dt.tzinfo is None:
        raise ValueError("missing source timezone")
    return dt.timestamp()


def parse_snapshot(global_data, coins, now):
    g = global_data["data"]
    by_id = {c["id"]:c for c in coins}
    btc, usdt = by_id["bitcoin"], by_id["tether"]
    t, tb, tu = number(g["updated_at"]), timestamp(btc["last_updated"]), timestamp(usdt["last_updated"])
    if any(not -60<=now-x<=MAX_AGE for x in (t,tb,tu)) or max(t,tb,tu)-min(t,tb,tu)>MAX_SKEW:
        raise ValueError("stale_future_or_skewed_source")
    total = number(g["total_market_cap"]["usd"])
    bd, ud = (number(g["market_cap_percentage"][k]) for k in ("btc","usdt"))
    if bd>=100 or ud>=100 or bd+ud>100:
        raise ValueError("invalid_market_shares")
    return {"source":SOURCE,"market_at":t,"btc_at":tb,"usdt_at":tu,"observed_at":now,
            "total_usd":total,"btc_d":bd,"usdt_d":ud,"usdt_cap_usd":total*(ud/100),
            "btc_usd":number(btc["current_price"]),"usdt_usd":number(usdt["current_price"]),
            "usdt_supply":number(usdt["circulating_supply"])}


def fresh(point, now):
    return (point.get("source")==SOURCE and
            all(-60<=now-point[key]<=MAX_AGE for key in ("market_at","btc_at","usdt_at")))


def changes(current, base):
    result = {"elapsed_seconds":current["market_at"]-base["market_at"],
              "btc_d_pp":current["btc_d"]-base["btc_d"],
              "usdt_d_pp":current["usdt_d"]-base["usdt_d"]}
    for key in ("btc_usd","total_usd","usdt_cap_usd","usdt_supply"):
        result[key+"_pct"] = (current[key]/base[key]-1)*100
    return result


def summarize(points, now):
    if not points:
        return {"ready":False,"reason":"warming_up","source":SOURCE}
    current = points[-1]
    if not fresh(current,now):
        return {"ready":False,"reason":"stale","source":SOURCE}
    horizons = {}
    for name, seconds in (("1h",3600),("4h",14400)):
        target = current["market_at"]-seconds
        choices = [p for p in points[:-1] if p.get("source")==SOURCE
                   and abs(p["market_at"]-target)<=HISTORY_TOLERANCE]
        if not choices:
            continue
        base = min(choices,key=lambda p:abs(p["market_at"]-target))
        window = [p for p in points if p["market_at"]>=base["market_at"]]
        if any(b["market_at"]-a["market_at"]>MAX_AGE for a,b in zip(window,window[1:])):
            continue
        horizons[name] = changes(current,base)
    delta = horizons.get("1h")
    regime, label, direction = "warming_up","1시간 비교자료 축적 중",0
    notes = []
    if delta:
        p, t, d = delta["btc_usd_pct"],delta["total_usd_pct"],delta["usdt_d_pp"]
        regime,label = "mixed","시장 신호 혼재"
        if p>=.2 and t>=.2 and d<=-.03:
            regime,label,direction = "up_alignment","BTC·시장 상승 동행",1
        elif p<=-.2 and t<=-.2 and d>=.03:
            regime,label,direction = "down_alignment","BTC·시장 하락 동행",-1
        if delta["btc_d_pp"]>=.05:
            notes.append("시장 약세 속 BTC 상대강세" if p<-.2 else "BTC 비중 확대")
        elif delta["btc_d_pp"]<=-.05:
            notes.append("BTC 비중 축소")
        if d>=.03 and t<=-.2 and abs(delta["usdt_cap_usd_pct"])<=.1:
            notes.append("USDT 비중 상승에 전체 시총 감소 동반")
        if abs(delta["usdt_supply_pct"])>=.1:
            notes.append("USDT 공급 변화 동반·자금유입과 동일하지 않음")
    peg_pct = (current["usdt_usd"]-1)*100
    if abs(current["usdt_usd"]-1)>=PEG_OBSERVATION:
        notes.append("USDT 달러 이탈 관측·단일 집계 출처")
    return {"ready":True,**current,"horizons":horizons,"regime":regime,"label":label,
            "alignment_direction":direction,"peg_deviation_pct":peg_pct,"notes":notes,
            "live_gate":False,"flow_inferred":False}


def alert_line(context):
    if not context or not context.get("ready"):
        return "🌐 시장 보조자료 미확인·진입 차단 없음"
    point = context
    stamp = datetime.fromtimestamp(point["market_at"],KST).strftime("%H:%M")
    base = (f"🌐 CG {stamp} BTC비중 {point['btc_d']:.2f}% · USDT비중 {point['usdt_d']:.2f}% "
            f"· USDT ${point['usdt_usd']:.4f}")
    delta = point["horizons"].get("1h")
    if delta:
        base += f" | 1h Δ {delta['btc_d_pp']:+.2f}/{delta['usdt_d_pp']:+.2f}%p"
        base += " · "+point["label"]
    else:
        base += " | 1h 자료 대기"
    if abs(point["peg_deviation_pct"])>=PEG_OBSERVATION*100:
        base += " ⚠️ 달러 이탈 관측"
    return base


def annotate(plan, reader, now):
    """Called after all original entry gates. Never changes its price/risk/side."""
    try:
        context = reader.snapshot(now) if reader is not None else {"ready":False,"reason":"unavailable","source":SOURCE}
        # Freeze the data known at this signal, rather than attach future history.
        context["line"] = alert_line(context)
        return dict(plan,market_context=context)
    except Exception:
        return plan


class MarketContextThread(threading.Thread):
    def __init__(self, path=None):
        super().__init__(name="lbank-market-context",daemon=True)
        self.path = path or os.getenv("LBANK_MARKET_CONTEXT_DB","/data/lbank_market_context.sqlite3")
        self._lock = threading.Lock()
        self._context = {"ready":False,"reason":"warming_up","source":SOURCE}
        self.stop_event = threading.Event()

    def snapshot(self, now):
        with self._lock:
            value = copy.deepcopy(self._context)
        if value.get("ready") and not fresh(value,now):
            return {"ready":False,"reason":"stale","source":SOURCE}
        return value

    def poll_once(self, db, now=None):
        # Clock AFTER network fetches prevents stale replies becoming fresh.
        global_data, coins = fetch_json(GLOBAL_URL), fetch_json(COINS_URL)
        now = time.time() if now is None else now
        point = parse_snapshot(global_data,coins,now)
        latest = db.execute("SELECT MAX(ts) FROM snapshots").fetchone()[0]
        if latest is not None and point["market_at"]<latest:
            raise ValueError("source_clock_regressed")
        with db:
            # Do not create repeated pseudo-bars from the same provider update.
            db.execute("INSERT OR IGNORE INTO snapshots VALUES(?,?)",(point["market_at"],json.dumps(point)))
            db.execute("DELETE FROM snapshots WHERE ts<?",(now-7*86400,))
        rows = db.execute("SELECT detail FROM snapshots ORDER BY ts").fetchall()
        points = [json.loads(row[0]) for row in rows if json.loads(row[0]).get("source")==SOURCE]
        # Latest cached coin prices can update while global data is unchanged;
        # past stored snapshots remain frozen at first observation.
        points = [p for p in points if p["market_at"]<point["market_at"]]+[point]
        context = summarize(points,now)
        with self._lock:
            self._context = context
        LOG.info("MARKET_CONTEXT_UPDATE %s",json.dumps(context,sort_keys=True))
        return context

    def run(self):
        while not self.stop_event.is_set():
            try:
                os.makedirs(os.path.dirname(os.path.abspath(self.path)),exist_ok=True)
                db = sqlite3.connect(self.path,timeout=1)
                try:
                    db.execute("PRAGMA journal_mode=WAL")
                    db.execute("CREATE TABLE IF NOT EXISTS snapshots(ts REAL PRIMARY KEY, detail TEXT NOT NULL)")
                    self.poll_once(db)
                finally:
                    db.close()
            except Exception as exc:
                # No raw URL/headers or credentials in logs; no blocking retry.
                LOG.warning("MARKET_CONTEXT_UNAVAILABLE exception=%s existing_alerts_unchanged=true",type(exc).__name__)
            self.stop_event.wait(POLL)

    def report_line(self, now):
        value = self.snapshot(now)
        text = "\n"+alert_line(value)
        if value.get("ready"):
            stamp = datetime.fromtimestamp(value["market_at"],KST).strftime("%H:%M")
            text += f"\nCoinGecko {stamp} KST · {value['label']}"
            if value["notes"]:
                text += " · "+" / ".join(value["notes"])
        return text
