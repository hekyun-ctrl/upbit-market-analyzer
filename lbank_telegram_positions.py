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
        self.state["position"] = {"side": side, "entry": str(entry),
            "quantity": str(qty), "stop": str(stop), "target": str(target),
            "risk_notice": None, "risk_count": 0,
            "opened_at": time.time(), "neutral_notice": False}
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
        self.position["target"] = str(target)
        self.mutate()
        return "신고 목표가를 갱신했습니다. 실제 거래소 주문은 직접 확인해야 합니다.\n" + self.status()

    def status(self):
        p = self.position
        if not p:
            return ("[BTCUSDT 수동 포지션]\n등록된 실제 보유 신고 없음.\n"
                    "/open long 진입가 수량 손절가 목표가\n"
                    "예: /open long 82712.9 0.0783 83100 83700")
        side = "롱" if p["side"] == "LONG" else "숏"
        return (f"[BTCUSDT 보유 신고 · {side}]\n"
                f"진입가 {fmt(p['entry'],1)} USDT\n"
                f"잔여 수량 {fmt(p['quantity'])} BTC\n"
                f"신고 손절가 {fmt(p['stop'],1)} / 목표가 {fmt(p['target'],1)}\n"
                "※ 텔레그램 신고 기준이며 거래소 체결·잔고와 자동 동기화되지 않습니다.\n"
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


class TelegramBot:
    def __init__(self, ledger):
        self.ledger = ledger
        self.enabled = bool(TOKEN and AUTHORIZED_CHAT)
        self._initialized = False

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
        args = {"chat_id":AUTHORIZED_CHAT, "text": message}
        if buttons:
            args["reply_markup"] = {"inline_keyboard":buttons}
        return self.api("sendMessage",args) is not None

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
