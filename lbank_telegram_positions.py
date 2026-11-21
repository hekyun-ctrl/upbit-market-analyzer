"""Authorized Telegram control of a manually reported BTCUSDT futures position.

Position ledger is a USER REPORT, never an exchange balance or order feed.
Commands do not create, edit, cancel, or close actual exchange orders.
"""
import json
import logging
import os
import time
import urllib.parse
import urllib.request
from decimal import Decimal, ROUND_DOWN
from lbank_telegram_transport import TelegramTransport
from lbank_profit_protection import new_policy, observe as profit_observe, acknowledge as profit_ack, format_notice as format_profit_notice

from lbank_signal_thesis import POLICY as THESIS_POLICY, assess_thesis

LOG = logging.getLogger("lbank_position_bot")
PATH = os.getenv("LBANK_POSITION_STATE_PATH", "/data/lbank_position_state.json")
TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
AUTHORIZED_CHAT = str(os.getenv("TELEGRAM_CHAT_ID", "")).strip()
STEP = Decimal("0.0001")


def num(text):
    d = Decimal(str(text))
    if not d.is_finite() or not 0 < d < Decimal("10000000"):
        raise ValueError("양수 값만 허용됩니다.")
    return d


def fmt(n, places=4):
    return format(Decimal(str(n)), f".{places}f").rstrip("0").rstrip(".")


class PositionState:
    """JSON persisted in a mounted Railway volume, atomically replaced."""
    def __init__(self, path=PATH):
        self.path = path
        self.state = {"offset": None, "position": None, "revision": 0, "pending": None}
        if os.path.isfile(self.path):
            with open(self.path, encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                self.state.update({k: data[k] for k in self.state if k in data})

    @property
    def position(self):
        return self.state["position"]

    def save(self):
        directory = os.path.dirname(self.path)
        if directory:
            os.makedirs(directory, exist_ok=True)
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.state, f, ensure_ascii=False)
            f.flush()
            os.fsync(f.fileno())
        os.chmod(tmp, 0o600)
        os.replace(tmp, self.path)

    def mutate(self):
        self.state["revision"] += 1
        self.state["pending"] = None
        self.save()

    def open(self, direction, entry, qty, stop, target):
        if self.position is not None:
            raise ValueError("이미 등록된 포지션이 있습니다. /status 후 확인하세요.")
        side = str(direction).upper()
        if side not in ("LONG", "SHORT", "롱", "숏"):
            raise ValueError("방향은 long 또는 short로 입력해 주세요.")
        side = "LONG" if side in ("LONG", "롱") else "SHORT"
        entry, qty, stop, target = map(num, (entry, qty, stop, target))
        if qty < STEP:
            raise ValueError("수량은 최소 0.0001 BTC입니다.")
        if abs((stop - entry) / entry) > Decimal("0.10"):
            raise ValueError("손절가는 진입가와 10% 이상 떨어질 수 없습니다.")
        sign = 1 if side == "LONG" else -1
        if sign * (target - entry) <= 0:
            raise ValueError("목표가는 진입 방향의 수익 구간이어야 합니다.")
        opened_at=time.time()
        self.state["position"] = {"side": side, "entry": str(entry),
            "quantity": str(qty), "stop": str(stop), "target": str(target),
            "risk_notice": None, "risk_count": 0,
            "opened_at": opened_at, "neutral_notice": False,
            "target_notice": False, "target_stage": 1,
            "target_hit_price": None,
            "profit_policy": new_policy(sign,float(entry),float(stop)), "profit_state": None,
            "management_thesis": dict(policy=THESIS_POLICY,entry=float(entry),anchor=None,initial_stop=float(stop)),
            "management_pending": None, "management_exit_notice": None}
        self.mutate()
        return self.status()

    def partial(self, qty):
        if self.position is None:
            raise ValueError("관리 중인 포지션이 없습니다.")
        qty = num(qty)
        remain = num(self.position["quantity"])
        if qty > remain:
            raise ValueError("청산 신고 수량이 잔여 수량보다 큽니다.")
        if remain - qty < STEP:
            self.state["position"] = None
            self.mutate()
            return "잔여 수량 전량 청산으로 기록했습니다. 실제 거래소 청산은 별도 확인하세요."
        self.position["quantity"] = str(remain - qty)
        self.mutate()
        return "부분청산 신고를 반영했습니다.\n" + self.status()

    def close(self):
        if self.position is None:
            return "이미 등록된 포지션이 없습니다."
        self.state["position"] = None
        self.mutate()
        return "전량 청산 완료 신고를 기록했습니다. LBank 거래소 주문은 자동 변경하지 않습니다."

    def move_stop(self, new_stop):
        if self.position is None:
            raise ValueError("관리 중인 포지션이 없습니다.")
        new_stop = num(new_stop)
        old = num(self.position["stop"])
        side = self.position["side"]
        if (side == "LONG" and new_stop < old) or (side == "SHORT" and new_stop > old):
            raise ValueError("손절가를 더 불리하게 넓히는 변경은 거부합니다.")
        self.position["stop"] = str(new_stop)
        self.position["risk_notice"] = None
        self.position["risk_count"] = 0
        self.mutate()
        return "신고 손절가를 갱신했습니다. 실제 거래소 손절 주문은 직접 수정해야 합니다.\n" + self.status()

    def change_target(self, target):
        if self.position is None:
            raise ValueError("관리 중인 포지션이 없습니다.")
        target = num(target)
        entry = num(self.position["entry"])
        direction = 1 if self.position["side"] == "LONG" else -1
        if direction * (target - entry) <= 0:
            raise ValueError("익절 목표는 진입가 대비 수익 구간이어야 합니다.")
        prior_target = num(self.position["target"])
        prior_notice = self.position.get("target_notice", False)
        if target != prior_target:
            # The user can set TP2 using /tp only AFTER the first
            # delivered target alert. We must never claim a TP1 notice is TP2.
            if prior_notice and direction * (target - prior_target) > 0:
                self.position["target_stage"] = 2
            self.position["target"] = str(target)
            self.position["target_notice"] = False
            self.position["target_hit_price"] = None
        self.mutate()
        return "신고 목표가를 갱신했습니다. 실제 거래소 주문은 직접 확인해야 합니다.\n" + self.status()

    def profit_check(self, price, now, native_state=None):
        p=self.position
        if not p or not p.get('profit_policy'):
            return None
        if p.get('management_thesis'):
            evidence=assess_thesis(p['management_thesis'],native_state,1 if p['side']=='LONG' else -1,
                price,p['opened_at'],now,allow_profitable_reversal=True)
            if evidence['invalidated'] or p.get('management_pending'):
                if not p.get('management_pending'):p['management_pending']=evidence
                self.save()
                return 'position_reversal'
        if p.get('target_notice'):
            return None
        p['profit_state'], reason=profit_observe(p['profit_policy'],p.get('profit_state'),
            direction=1 if p['side']=='LONG' else -1,entry=float(p['entry']),
            price=price,now=now,native_state=native_state,minimum_guard=float(p['stop']))
        self.save()
        return reason

    def acknowledge_profit(self, reason):
        if reason!='position_reversal':
            profit_ack(self.position['profit_state'],reason)
        if reason in ('profit_guard_touched','position_reversal'):
            # Keep the user-reported position until /close; avoid repeated exits.
            self.position['profit_policy']=None
            self.position['management_exit_notice']=reason
        self.save()

    def target_reached(self, prices):
        """One-shot reported position notice; price event survives send failure."""
        p = self.position
        if not p or p.get("target_notice", False) or p.get("management_exit_notice") or not prices:
            return None
        if p.get("target_hit_price") is not None:
            return float(p["target_hit_price"])
        current = float(prices[-1])
        direction = 1 if p["side"] == "LONG" else -1
        if direction * (current - float(p["target"])) >= 0:
            p["target_hit_price"] = current
            self.save()
            return current
        return None

    def acknowledge_target(self):
        """Mark as delivered, never as executed by LBank."""
        if self.position:
            self.position["target_notice"] = True
            self.save()

    def status(self):
        p = self.position
        if not p:
            return ("[BTCUSDT 수동 포지션]\n등록된 실제 보유 신고 없음.\n"
                    "/open long 진입가 수량 손절가 목표가\n"
                    "예: /open long 82712.9 0.0783 83100 83700")
        side = "롱" if p["side"] == "LONG" else "숏"
        guard=(p.get('profit_state') or {}).get('guard')
        protection=f"목표 전 수익 보호 기준 {guard:,.1f} USDT (주문 미확인)\n" if guard is not None else ''
        return (f"[BTCUSDT 보유 신고 · {side}]\n"
                f"진입가 {fmt(p['entry'],1)} USDT\n"
                f"잔여 수량 {fmt(p['quantity'])} BTC\n"
                f"신고 손절가 {fmt(p['stop'],1)} / 목표가 {fmt(p['target'],1)}\n"
                f"{protection}※ 텔레그램 신고 기준이며 거래소 체결·잔고와 자동 동기화되지 않습니다.\n"
                "실제 손절·익절 주문 변경은 LBank에서 별도 진행하세요.")

    def half(self):
        if not self.position:
            raise ValueError("관리 중인 포지션이 없습니다.")
        amount = (num(self.position["quantity"]) / 2).quantize(STEP, rounding=ROUND_DOWN)
        if amount < STEP:
            raise ValueError("잔여 수량이 작아서 절반 청산 신고가 불가합니다.")
        return self.partial(amount)

    def risk(self, prices):
        """Risk notice if a reported stop is reached or price approaches it with adverse drift.

        This is a *warning* about a self-reported position, not an execution.
        """
        if self.position is None or len(prices) < 2:
            return None
        p = self.position
        price = float(prices[-1])
        stop = float(p["stop"])
        side = 1 if p["side"] == "LONG" else -1
        if side * (price - stop) <= 0:
            reason = "신고 손절가 도달/이탈"
        elif len(prices) >= 6 and 0 < side * (price - stop) / stop <= .0015 and side * (price - prices[-6]) / price < -.0005:
            p["risk_count"] += 1
            reason = "신고 손절가 근접과 반대 움직임" if p["risk_count"] >= 2 else None
        else:
            p["risk_count"] = 0
            reason = None
        if reason and p["risk_notice"] is None:
            return (reason, price)
        return None

    def acknowledge_risk(self, reason):
        if self.position:
            self.position["risk_notice"] = reason
            self.save()


def format_position_management(position, reason, price, time_kst):
    if reason=='position_reversal':
        return (f"[LBank BTCUSDT | 신고 포지션 큰 방향 반전 확인]\n{time_kst} KST\n"
                f"신고 진입가 {float(position['entry']):,.1f} | 현재 {price:,.1f} USDT\n"
                "신고 이후 완료 15분 방향·5분 EMA와 고저점이 반대로 전환됐습니다.\n"
                "수익·손실 여부와 관계없이 실제 보유와 청산 여부를 확인하세요.\n"
                "새 진입 신호가 아닙니다. 신고 기준·실제 체결 미확인·자동주문 없음.")
    return format_profit_notice(position['profit_state'],reason,price,time_kst,'사용자 신고 체결가 기준')


def format_position_target_alert(position, price, time_kst):
    """Notice at current *reported* target; not evidence of order execution."""
    side = "롱" if position["side"] == "LONG" else "숏"
    direction = 1 if side == "롱" else -1
    entry = float(position["entry"])
    target = float(position["target"])
    delta = direction * (target / entry - 1) * 100
    stage = 2 if position.get("target_stage") == 2 else 1
    action = (
        "실제 보유 중이라면 남은 50% 익절과 잔여 포지션 정리를 검토하세요.\n"
        if stage == 2 else
        f"실제 보유 중이라면 50% 익절 후 "
        f"잔여 손절가는 진입가 {entry:,.1f} USDT로 조정 요함.\n"
    )
    return (
        f"[LBank BTCUSDT | 신고 {side} 포지션 {stage}차 목표 도달]\n"
        f"한국시간: {time_kst}\n"
        f"현재 {stage}차 목표 {target:,.1f} USDT ({delta:+.2f}%) 도달\n"
        f"목표 접촉 시 선물 가격표본: {price:,.1f} USDT\n"
        f"{action}"
        "※ LBank에서 실제 청산·손절 주문을 직접 실행하고 체결을 확인하세요.\n"
        "※ 거래소 체결·실제 보유는 확인되지 않았습니다. 이미 청산했다면 무시하세요.\n"
        "※ 진입가 손절도 수수료·슬리피지 차감 후 순손실이 날 수 있습니다.\n"
        "※ 1분 가격표본은 구간내 순간 목표 터치를 놓칠 수 있습니다."
    )


class TelegramBot:
    def __init__(self, ledger):
        self.ledger = ledger
        self.enabled = bool(TOKEN and AUTHORIZED_CHAT)
        self._initialized = False
        self.delivery = TelegramTransport(token=TOKEN, chat_id=AUTHORIZED_CHAT)

    def api(self, method, args=None):
        if not self.enabled:
            return None
        payload = urllib.parse.urlencode({
            k: json.dumps(v, ensure_ascii=False) if isinstance(v, (dict,list)) else str(v)
            for k,v in (args or {}).items()
        }).encode()
        url = "https://api.telegram.org/bot" + TOKEN + "/" + method
        # Never log URL, response body or token on failure.
        try:
            with urllib.request.urlopen(urllib.request.Request(url, data=payload, method="POST"), timeout=9) as reply:
                result = json.load(reply)
            return result.get("result") if result.get("ok") else None
        except Exception as exc:
            LOG.warning("Telegram %s request failed (%s)", method, type(exc).__name__)
            return None

    def send(self, message, buttons=None):
        # Same validated transport as market/macro alerts. getUpdates and
        # callbackQuery handling remain in this class without modification.
        return self.enabled and self.delivery.send(message, buttons=buttons)

    def keyboard(self):
        pos = self.ledger.position
        rev = self.ledger.state["revision"]
        if not pos:
            return [[{"text":"보유 상태 확인","callback_data":f"status:{rev}"}]]
        return [
            [{"text":"잔여 수량의 50% 청산 신고","callback_data":f"askhalf:{rev}"}],
            [{"text":"전량 청산 신고","callback_data":f"askclose:{rev}"}],
            [{"text":"보유 상태 확인","callback_data":f"status:{rev}"}],
        ]

    def _authorized(self, update):
        msg = update.get("message") or (update.get("callback_query") or {}).get("message") or {}
        if str((msg.get("chat") or {}).get("id")) != AUTHORIZED_CHAT:
            return False
        return not (update.get("message") or {}).get("from",{}).get("is_bot",False)

    def dispatch_command(self, line):
        parts = line.strip().split()
        if not parts:
            return "명령어를 입력해 주세요."
        key = parts[0].split("@")[0].lower()
        if key in ("/start", "/help", "/menu"):
            return ("[LBank BTCUSDT 수동 포지션 관리]\n"
                "/open long 82712.9 0.0783 83100 83700 : 신규 등록\n"
                "/partial 0.01 : 0.01 BTC 청산 신고\n"
                "/close : 전량 청산 신고\n"
                "/status : 잔여 수량 확인\n"
                "/stop 83100 : 손절가 신고 변경\n"
                "/tp 83700 : 익절가 신고 변경\n"
                "아래 버튼으로 50% 또는 전량 청산 신고도 가능합니다.\n"
                "※ 모든 명령은 서버 기록만 갱신하며 거래소 주문을 실행하지 않습니다.")
        if key == "/status" and len(parts)==1:
            return self.ledger.status()
        if key == "/open" and len(parts)==6:
            return self.ledger.open(*parts[1:])
        if key == "/partial" and len(parts)==2:
            return self.ledger.partial(parts[1])
        if key == "/close" and len(parts)==1:
            return self.ledger.close()
        if key == "/stop" and len(parts)==2:
            return self.ledger.move_stop(parts[1])
        if key == "/tp" and len(parts)==2:
            return self.ledger.change_target(parts[1])
        return "명령 형식이 다릅니다. /help 로 사용법을 확인해 주세요."

    def _callback(self, cb):
        data = cb.get("data","")
        self.api("answerCallbackQuery",{"callback_query_id":cb.get("id","")})
        parts = data.split(":")
        if len(parts)!=2:
            return "잘못된 버튼 입력입니다."
        action, raw_rev=parts
        try:
            revision = int(raw_rev)
        except ValueError:
            return "잘못된 버튼 입력입니다."
        if revision != self.ledger.state["revision"]:
            return "지난 버튼입니다. /status 로 새 메뉴를 받아주세요."
        if action=="status":
            return self.ledger.status()
        if action in ("askhalf","askclose"):
            if not self.ledger.position:
                return "등록된 보유 포지션이 없습니다."
            operation = "half" if action=="askhalf" else "close"
            self.ledger.state["pending"] = {"action":operation,"revision":revision,"time":time.time()}
            self.ledger.save()
            self.send(
                "LBank에서 실제 청산을 완료하셨나요? 완료하신 경우에만 확정해 주세요.\n"
                "이 버튼은 거래소 주문을 실행하지 않습니다.",
                [[{"text":"청산 완료 신고 확정","callback_data":f"confirm:{revision}"},
                  {"text":"취소","callback_data":f"cancel:{revision}"}]])
            return None
        if action=="cancel":
            self.ledger.state["pending"] = None
            self.ledger.save()
            return "신고를 취소했습니다."
        if action=="confirm":
            pending=self.ledger.state["pending"]
            if not pending or pending["revision"]!=revision or time.time()-pending["time"]>300:
                return "신고 확인이 만료됐습니다. 다시 눌러주세요."
            if pending["action"]=="half":
                return self.ledger.half()
            if pending["action"]=="close":
                return self.ledger.close()
        return "잘못된 버튼 입력입니다."

    def dispatch_update(self, update):
        if not self._authorized(update):
            LOG.warning("Unauthorized Telegram update ignored")
            return
        try:
            if update.get("callback_query"):
                text = self._callback(update["callback_query"])
            else:
                text = self.dispatch_command(update.get("message",{}).get("text",""))
        except (ValueError,ArithmeticError) as exc:
            text = "신고 반영 불가: "+str(exc)
        except Exception as exc:
            LOG.error("Telegram command exception: %s",type(exc).__name__)
            text = "명령 처리 오류가 발생했습니다. /status로 확인하세요."
        if text:
            self.send(text,self.keyboard())

    def poll(self):
        if not self.enabled:
            return
        if not self._initialized:
            webhook = self.api("getWebhookInfo")
            if webhook is None:
                return
            if webhook.get("url"):
                LOG.error("Bot has an existing webhook; refusing to change it or start getUpdates")
                self.enabled = False
                return
            if self.ledger.state["offset"] is None:
                # Avoid processing old /close commands left in the Telegram queue.
                tail = self.api("getUpdates",{"offset":-1,"timeout":0,"limit":1})
                if tail is None:
                    return
                if tail:
                    self.ledger.state["offset"] = max(u["update_id"] for u in tail)+1
                    self.ledger.save()
            self._initialized = True
            LOG.info("Telegram inbound commands enabled for authorized chat only")
        kwargs={"timeout":0,"limit":10}
        if self.ledger.state["offset"] is not None:
            kwargs["offset"]=self.ledger.state["offset"]
        updates = self.api("getUpdates",kwargs)
        if updates is None:
            return
        for update in updates:
            update_id=update.get("update_id")
            if not isinstance(update_id,int):
                continue
            if self.ledger.state["offset"] is not None and update_id<self.ledger.state["offset"]:
                continue
            self.dispatch_update(update)
            self.ledger.state["offset"]=update_id+1
            self.ledger.save()
