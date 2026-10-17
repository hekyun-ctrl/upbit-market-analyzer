"""Persistent, auditable observation accounting. Never exchange/actual PnL.

Two predeclared exit policies share each delivered signal: partial targets and
partial targets plus exit on an early warning. Entry at the signal quote and
target-touch fills are hypothetical. Missing quotes censor a result; they do
not become wins. SQLite transactions retain TP1 state across restarts.
"""
import json
import math
import os
import sqlite3
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))
POLICIES = ("levels", "warning_exit")


def day_key(ts):
    return datetime.fromtimestamp(ts, KST).date().isoformat()


def positive(value):
    return math.isfinite(float(value)) and float(value) > 0


def leg_pnl(p, price, fraction):
    """Taker both sides, adverse exit slippage and a declared funding reserve."""
    actual_exit = price * (1 - p["direction"] * p["slippage"])
    unit = (p["direction"] * (actual_exit - p["price"])
            - p["fee"] * (p["price"] + actual_exit)
            - p["funding_reserve"] * p["price"])
    return fraction * p["quantity_btc"] * unit


class PerformanceBook:
    def __init__(self, path, balance, goal_pct=5.0):
        if not positive(balance) or not positive(goal_pct):
            raise ValueError("invalid reference balance/goal")
        self.balance, self.goal_pct = float(balance), float(goal_pct)
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self.db = sqlite3.connect(path, timeout=2)
        self.db.row_factory = sqlite3.Row
        self.db.execute("PRAGMA journal_mode=WAL")
        self.db.execute("PRAGMA synchronous=FULL")
        self.db.executescript('''
          CREATE TABLE IF NOT EXISTS samples(ts REAL PRIMARY KEY, price REAL NOT NULL);
          CREATE TABLE IF NOT EXISTS decisions(ts REAL PRIMARY KEY, detail TEXT NOT NULL);
          CREATE TABLE IF NOT EXISTS signals(
            id TEXT, policy TEXT, opened REAL, last_at REAL, last_price REAL,
            plan TEXT, stage INTEGER DEFAULT 0, partial REAL DEFAULT 0,
            status TEXT DEFAULT 'open', closed REAL, net REAL,
            PRIMARY KEY(id, policy));
          CREATE TABLE IF NOT EXISTS reports(day TEXT PRIMARY KEY, state TEXT, at REAL);
        ''')
        self.last_prune = 0.0

    def sample(self, ts, price, decision=None):
        if not positive(ts) or not positive(price):
            raise ValueError("invalid sample")
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO samples VALUES(?,?)", (ts, price))
            if decision is not None:
                self.db.execute("INSERT OR REPLACE INTO decisions VALUES(?,?)",
                                (ts, json.dumps(decision, ensure_ascii=False, allow_nan=False)))
            if ts - self.last_prune >= 3600:
                self.db.execute("DELETE FROM samples WHERE ts<?", (ts - 30 * 86400,))
                self.db.execute("DELETE FROM decisions WHERE ts<?", (ts - 7 * 86400,))
                self.db.execute("DELETE FROM signals WHERE status!='open' AND opened<?",
                                (ts - 90 * 86400,))
                self.db.execute("DELETE FROM reports WHERE at<?", (ts - 90 * 86400,))
                self.last_prune = ts

    def add(self, reference, plan, ts, fee=.0006, slippage=.0002, funding=.0001):
        fields = ("price", "stop", "target1", "target2", "quantity_btc", "net_loss")
        if (plan["direction"] not in (-1, 1) or not positive(ts)
                or not all(positive(plan[k]) for k in fields)
                or not all(math.isfinite(x) and 0 <= x <= .01 for x in (fee, slippage, funding))):
            raise ValueError("invalid plan")
        d = plan["direction"]
        if not (d * (plan["price"] - plan["stop"]) > 0
                and d * (plan["target1"] - plan["price"]) > 0
                and d * (plan["target2"] - plan["target1"]) > 0):
            raise ValueError("invalid target geometry")
        p = {k: plan[k] for k in fields}
        p.update(direction=d, strategy=plan.get("strategy", "unknown"),
                 fee=fee, slippage=slippage, funding_reserve=funding,
                 reference_balance=self.balance, data_source="lbank_perpetual_ticker",
                 fills="hypothetical_quote_entry_and_target_touch_not_verified")
        # Timestamp in identity prevents collisions with an older reset signal ID.
        identity = f"{int(ts * 1000)}:{reference}"
        with self.db:
            for policy in POLICIES:
                self.db.execute('''INSERT OR IGNORE INTO signals
                    (id,policy,opened,last_at,last_price,plan) VALUES(?,?,?,?,?,?)''',
                    (identity, policy, ts, ts, plan["price"], json.dumps(p)))
        return identity

    def _close(self, row, ts, price, reason, p):
        remain = .5 if row["stage"] else 1.0
        net = None if reason == "censored_gap" else row["partial"] + leg_pnl(p, price, remain)
        self.db.execute('''UPDATE signals SET status=?,closed=?,last_at=?,last_price=?,net=?
            WHERE id=? AND policy=?''', (reason, ts, ts, price, net, row["id"], row["policy"]))
        return {"id": row["id"], "policy": row["policy"], "status": reason,
                "net_usdt_model": net, "actual_pnl": False}

    def observe(self, ts, price):
        if not positive(ts) or not positive(price):
            raise ValueError("invalid observation")
        events = []
        with self.db:
            for raw in self.db.execute("SELECT * FROM signals WHERE status='open'").fetchall():
                row, p = dict(raw), json.loads(raw["plan"])
                if ts <= row["last_at"]:
                    continue
                if ts - row["last_at"] > 150:
                    events.append(self._close(row, ts, price, "censored_gap", p))
                    continue
                stop = p["price"] if row["stage"] else p["stop"]
                if p["direction"] * (price - stop) <= 0:
                    # Use observed adverse price, not an optimistic stop fill.
                    events.append(self._close(row, ts, price,
                                             "entry_stop" if row["stage"] else "stop", p))
                    continue
                if ts - row["opened"] >= 7200:
                    events.append(self._close(row, ts, price, "time_exit", p))
                    continue
                if not row["stage"] and p["direction"] * (price - p["target1"]) >= 0:
                    row["stage"], row["partial"] = 1, leg_pnl(p, p["target1"], .5)
                    self.db.execute("UPDATE signals SET stage=1,partial=? WHERE id=? AND policy=?",
                                    (row["partial"], row["id"], row["policy"]))
                if row["stage"] and p["direction"] * (price - p["target2"]) >= 0:
                    events.append(self._close(row, ts, p["target2"], "target2", p))
                else:
                    self.db.execute("UPDATE signals SET last_at=?,last_price=? WHERE id=? AND policy=?",
                                    (ts, price, row["id"], row["policy"]))
        return events

    def early_warning(self, reference, sent_at, ts, price):
        identity = f"{int(sent_at * 1000)}:{reference}"
        row = self.db.execute("SELECT * FROM signals WHERE id=? AND policy='warning_exit' AND status='open'",
                              (identity,)).fetchone()
        if row is not None:
            p = json.loads(row["plan"])
            with self.db:
                return self._close(row, ts, price,
                                   "censored_gap" if ts - row["last_at"] > 150 else "early_warning", p)

    def summary(self, day=None):
        result = {}
        rows = self.db.execute("SELECT * FROM signals ORDER BY opened,id,policy").fetchall()
        for policy in POLICIES:
            selected = [r for r in rows if r["policy"] == policy
                        and (day is None or day_key(r["opened"]) == day)]
            finished = [r for r in selected if r["net"] is not None]
            nets = [r["net"] for r in finished]
            result[policy] = {"signals": len(selected), "closed": len(finished),
                "open": sum(r["status"] == "open" for r in selected),
                "censored": sum(r["status"] == "censored_gap" for r in selected),
                "net_usdt_model": sum(nets), "wins": sum(n > 0 for n in nets),
                "reference_return_pct": sum(nets) / self.balance * 100,
                "actual_account_pnl": False}
        return result

    def daily_message(self, now):
        """One 22:50 KST cohort report/day, reserved before sending.

        Ambiguous send failures stay reserved to prevent duplicate reports.
        Reports never turn simulated outcomes into an actual daily goal claim.
        """
        local, day = datetime.fromtimestamp(now, KST), day_key(now)
        if (local.hour, local.minute) < (22, 50):
            return None
        with self.db:
            cursor = self.db.execute("INSERT OR IGNORE INTO reports VALUES(?,?,?)",
                                     (day, "reserved", now))
        if cursor.rowcount == 0:
            return None
        stats = self.summary(day)
        a, b = stats["levels"], stats["warning_exit"]
        return (f"[LBank BTCUSDT | {day} 알람 모의 성과 중간집계]\n"
                f"기록 시작 이후 오늘 생성 신호 {a['signals']}건 | 완료 {a['closed']} · 관찰중 {a['open']} · 자료공백 {a['censored']}\n"
                f"50% 분할익절·진입가 손절: {a['net_usdt_model']:+.2f} USDT ({a['reference_return_pct']:+.2f}%)\n"
                f"조기경고 청산 모형: {b['net_usdt_model']:+.2f} USDT | 완료 {b['closed']}건\n"
                f"목표 {self.goal_pct:g}% = 기준자금의 {self.balance*self.goal_pct/100:.2f} USDT\n"
                "미완료·자료공백·이전 날짜 신호는 순이익에서 제외됩니다.\n"
                "비용 추정 반영·가격표본 모의계산이며 실제 체결·계좌 순수익이 아닙니다.\n"
                "목표 미달을 이유로 위험이나 매매 횟수를 늘리지 않습니다.")

    def report_delivery(self, now, delivered):
        with self.db:
            self.db.execute("UPDATE reports SET state=? WHERE day=?",
                            ("delivered" if delivered else "delivery_unconfirmed", day_key(now)))
