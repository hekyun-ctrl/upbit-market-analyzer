"""Persistent, auditable observation accounting. Never exchange/actual PnL.

Predeclared exit policies share each delivered signal: partial targets,
warning exits, and (new eligible signals only) a native-bar trend runner.
Entry at the signal quote and
target-touch fills are hypothetical. Missing quotes censor a result; they do
not become wins. SQLite transactions retain TP1 state across restarts.
"""
import json
import math
import os
import sqlite3
from collections import Counter
from datetime import datetime, timedelta, timezone
from lbank_trend_runner import runner_decision, RUNNER_TTL, POLICY_VERSION

KST = timezone(timedelta(hours=9))
POLICIES = ("levels", "warning_exit", "trend_runner")


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
    def __init__(self, path, balance, goal_pct=7.0):
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
          CREATE TABLE IF NOT EXISTS runner_adjustments(
            id TEXT, ts REAL, detail TEXT NOT NULL, PRIMARY KEY(id,ts));
          CREATE TABLE IF NOT EXISTS risk_cautions(
            id TEXT, ts REAL, detail TEXT NOT NULL, PRIMARY KEY(id,ts));
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
                self.db.execute("DELETE FROM runner_adjustments WHERE ts<?", (ts - 90 * 86400,))
                self.db.execute("DELETE FROM risk_cautions WHERE ts<?", (ts - 90 * 86400,))
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
                 reference_balance=self.balance,
                 data_source=plan.get("data_source", "lbank_perpetual_ticker"),
                 observation_source="lbank_perpetual_ticker",
                 runner_enabled=bool(plan.get("runner_enabled")),
                 entry_quality=plan.get("entry_quality"),
                 runner_last_bar=-1, barrier=plan.get("barrier"),
                 policy_version=POLICY_VERSION if plan.get("runner_enabled") else "fixed_levels_v2",
                 market_context=plan.get("market_context"),
                 public_techniques=plan.get("public_techniques"),
                 fills="hypothetical_quote_entry_and_target_touch_not_verified")
        # Timestamp in identity prevents collisions with an older reset signal ID.
        identity = f"{int(ts * 1000)}:{reference}"
        with self.db:
            for policy in POLICIES:
                if policy == "trend_runner" and not p["runner_enabled"]:
                    continue
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

    def observe(self, ts, price, native_state=None):
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
                is_runner = row["policy"] == "trend_runner"
                stop = p.get("runner_stop", p["price"]) if row["stage"] else p["stop"]
                if p["direction"] * (price - stop) <= 0:
                    # Use observed adverse price, not an optimistic stop fill.
                    events.append(self._close(row, ts, price,
                                             ("trailing_stop" if is_runner and stop != p["price"] else
                                              "entry_stop") if row["stage"] else "stop", p))
                    continue
                if ts - row["opened"] >= (RUNNER_TTL if is_runner else 7200):
                    events.append(self._close(row, ts, price, "time_exit", p))
                    continue
                if not row["stage"] and p["direction"] * (price - p["target1"]) >= 0:
                    row["stage"], row["partial"] = 1, leg_pnl(p, p["target1"], .5)
                    self.db.execute("UPDATE signals SET stage=1,partial=? WHERE id=? AND policy=?",
                                    (row["partial"], row["id"], row["policy"]))
                if is_runner and row["stage"]:
                    proposal = runner_decision(
                        direction=p["direction"], entry=p["price"],
                        stop=p.get("runner_stop", p["price"]), target2=p["target2"],
                        sent_at=row["opened"], last_bar=p.get("runner_last_bar", -1),
                        state=native_state, now=ts, price=price,
                        strategy=p["strategy"], barrier=p.get("barrier"))
                    if proposal is not None:
                        self.db.execute("INSERT OR IGNORE INTO runner_adjustments VALUES(?,?,?)",
                                        (row["id"], ts, json.dumps(proposal)))
                        if proposal["reason"] in ("trend_exit", "neutral_exit"):
                            events.append(self._close(row, ts, price, proposal["reason"], p))
                            continue
                        p.update(runner_stop=proposal["stop"], target2=proposal["target2"],
                                 runner_last_bar=proposal["bar_start"])
                        self.db.execute("UPDATE signals SET plan=? WHERE id=? AND policy=?",
                                        (json.dumps(p), row["id"], row["policy"]))
                if row["stage"] and p["direction"] * (price - p["target2"]) >= 0:
                    events.append(self._close(row, ts, p["target2"], "target2", p))
                else:
                    self.db.execute("UPDATE signals SET last_at=?,last_price=? WHERE id=? AND policy=?",
                                    (ts, price, row["id"], row["policy"]))
        return events

    def record_caution(self,reference,sent_at,ts,price,evidence):
        identity=f"{int(sent_at*1000)}:{reference}"
        detail=dict(price=price,evidence=evidence,invalidated=False,actual_pnl=False)
        with self.db:
            self.db.execute("INSERT OR IGNORE INTO risk_cautions VALUES(?,?,?)",
                            (identity,ts,json.dumps(detail,allow_nan=False)))

    def early_warning(self, reference, sent_at, ts, price):
        identity = f"{int(sent_at * 1000)}:{reference}"
        rows = self.db.execute("SELECT * FROM signals WHERE id=? AND policy IN ('warning_exit','trend_runner') AND status='open'",
                               (identity,)).fetchall()
        with self.db:
            for row in rows:
                p = json.loads(row["plan"])
                self._close(row, ts, price,
                            "censored_gap" if ts - row["last_at"] > 150 else "early_warning", p)

    def summary(self, day=None):
        result = {}
        rows = self.db.execute("SELECT * FROM signals ORDER BY opened,id,policy").fetchall()
        for policy in POLICIES:
            selected = [r for r in rows if r["policy"] == policy
                        and (day is None or day_key(r["opened"]) == day)]
            finished = [r for r in selected if r["net"] is not None]
            nets = [r["net"] for r in finished]
            gains, losses = [n for n in nets if n > 0], [n for n in nets if n < 0]
            equity = peak = drawdown = 0.
            for r in sorted(finished, key=lambda r: (r["closed"], r["id"])):
                equity += r["net"]
                peak = max(peak, equity)
                drawdown = max(drawdown, peak - equity)
            # Wilson interval is descriptive; serially dependent trades violate
            # its iid assumption. It is not a predictive success probability.
            n, w = len(nets), len(gains)
            if n:
                z, rate = 1.96, w / n
                center = (rate + z*z/(2*n)) / (1+z*z/n)
                radius = z * math.sqrt(rate*(1-rate)/n + z*z/(4*n*n)) / (1+z*z/n)
                interval = [100*(center-radius), 100*(center+radius)]
            else:
                interval = None
            result[policy] = {"signals": len(selected), "closed": len(finished),
                "open": sum(r["status"] == "open" for r in selected),
                "censored": sum(r["status"] == "censored_gap" for r in selected),
                "net_usdt_model": sum(nets), "wins": sum(n > 0 for n in nets),
                "reference_return_pct": sum(nets) / self.balance * 100,
                "losses": len(losses), "win_rate_pct": 100*w/n if n else None,
                "mean_win_usdt": sum(gains)/len(gains) if gains else None,
                "mean_loss_usdt": sum(losses)/len(losses) if losses else None,
                "expectancy_usdt": sum(nets)/n if n else None,
                "profit_factor": sum(gains)/abs(sum(losses)) if losses else None,
                "closed_model_drawdown_usdt": drawdown,
                "win_rate_wilson95_iid_only": interval,
                "actual_account_pnl": False}
        return result

    def paired_runner_comparison(self, day=None):
        """Compare ONLY common completed entries; exclude censored/open pairs."""
        pairs = self.db.execute('''SELECT a.opened,a.net AS baseline,b.net AS runner
          FROM signals a JOIN signals b ON a.id=b.id
          WHERE a.policy='warning_exit' AND b.policy='trend_runner'
          AND a.net IS NOT NULL AND b.net IS NOT NULL''').fetchall()
        pairs = [p for p in pairs if day is None or day_key(p["opened"]) == day]
        return {"completed_pairs": len(pairs),
                "baseline_net": sum(p["baseline"] for p in pairs),
                "runner_net": sum(p["runner"] for p in pairs),
                "difference_usdt": sum(p["runner"]-p["baseline"] for p in pairs),
                "actual_account_pnl": False}

    def market_context_summary(self, day=None):
        """Frozen entry-time cohorts; independent exit policies, never add them."""
        groups = {}
        rows = self.db.execute("SELECT policy,opened,plan,net,status FROM signals").fetchall()
        for row in rows:
            if day is not None and day_key(row["opened"]) != day:
                continue
            p = json.loads(row["plan"])
            ctx = p.get("market_context") or {}
            label = "unavailable"
            if ctx.get("ready"):
                bias = ctx.get("alignment_direction",0)
                label = ("aligned" if bias==p["direction"] else "opposed") if bias else (
                    "warming_up" if ctx.get("regime")=="warming_up" else "mixed")
            group = groups.setdefault(row["policy"],{}).setdefault(label,
                {"signals":0,"completed":0,"censored":0,"net_usdt_model":0.,"mean_net_usdt":None})
            group["signals"] += 1
            if row["net"] is not None:
                group["completed"] += 1
                group["net_usdt_model"] += row["net"]
                group["mean_net_usdt"] = group["net_usdt_model"]/group["completed"]
            elif row["status"]=="censored_gap":
                group["censored"] += 1
        return {"policies":groups,"actual_account_pnl":False,
                "filter_effect_measured":False,"causal_improvement_verified":False}

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
        runner, paired = stats["trend_runner"], self.paired_runner_comparison(day)
        rate = "미집계" if runner["win_rate_pct"] is None else f"{runner['win_rate_pct']:.1f}%"
        decisions = self.decision_summary(day)
        market = decisions["last_market"]
        diagnostic = (f"진입심사 기록 {decisions['checks']}회: 패턴 없음 {decisions['reasons'].get('market_no_setup',0)} · "
                      f"비용/목표 탈락 {decisions['reasons'].get('risk_or_target',0)} · "
                      f"데이터 대기 {decisions['reasons'].get('native_data_unavailable',0)}\n")
        if market:
            diagnostic += (f"최근 심사 1시간 고저폭 {market['range60_pct']:.3f}% / "
                           f"비용 반영 최소 1차 목표 거리 {market['minimum_target_pct']:.3f}%\n")
        return (f"[LBank BTCUSDT | {day} 알람 모의 성과 중간집계]\n"
                f"기록 시작 이후 오늘 생성 신호 {a['signals']}건 | 완료 {a['closed']} · 관찰중 {a['open']} · 자료공백 {a['censored']}\n"
                f"50% 분할익절·진입가 손절: {a['net_usdt_model']:+.2f} USDT ({a['reference_return_pct']:+.2f}%)\n"
                f"조기경고 청산 모형: {b['net_usdt_model']:+.2f} USDT | 완료 {b['closed']}건\n"
                f"추세연장 모형: {runner['net_usdt_model']:+.2f} USDT | 완료 {runner['closed']} · 관찰중 {runner['open']} · 공백 {runner['censored']}\n"
                f"추세연장 최종 순익 승률 {rate} | 동일신호 완료 {paired['completed_pairs']}쌍, 기존 대비 {paired['difference_usdt']:+.2f} USDT\n"
                f"목표 {self.goal_pct:g}% = 기준자금의 {self.balance*self.goal_pct/100:.2f} USDT\n"
                f"{diagnostic}"
                "미완료·자료공백·이전 날짜 신호는 순이익에서 제외됩니다.\n"
                "비용 추정 반영·가격표본 모의계산이며 실제 체결·계좌 순수익이 아닙니다.\n"
                "모형별 결과는 비교용으로 서로 합산하지 않습니다.\n"
                "목표 미달을 이유로 위험이나 매매 횟수를 늘리지 않습니다.")

    def decision_summary(self, day):
        """Evaluation counts, NOT distinct trade opportunities or missed wins."""
        start = datetime.fromisoformat(day).replace(tzinfo=KST).timestamp()
        rows = self.db.execute("SELECT detail FROM decisions WHERE ts>=? AND ts<? ORDER BY ts",
                               (start, start+86400)).fetchall()
        reasons, last_market = Counter(), None
        for row in rows:
            detail = json.loads(row["detail"])
            reasons[detail.get("reason", "unknown")] += 1
            if "market" in detail:
                last_market = detail["market"]
            elif detail.get("reason") in ("native_data_unavailable", "native_volatility_unavailable"):
                last_market = None
        return {"checks": len(rows), "reasons": dict(reasons), "last_market": last_market}

    def report_delivery(self, now, delivered):
        with self.db:
            self.db.execute("UPDATE reports SET state=? WHERE day=?",
                            ("delivered" if delivered else "delivery_unconfirmed", day_key(now)))
