"""Post-entry monitoring of previously SENT LBank BTCUSDT price signals.

These are hypothetical signals, not confirmed positions or exchange orders.
No private credentials, orders, or authenticated position tracking.
"""
from dataclasses import dataclass

SIGNAL_TTL_SECONDS = 7200
EARLY_RISK_FRACTION = 0.55
MIN_COUNTER_MOVE_5M = 0.0008
CONSECUTIVE_RISK_CHECKS = 2


@dataclass
class TrackedSignal:
    number: int
    direction: int
    entry: float
    stop: float
    target1: float
    sent_at: float
    bad_samples: int = 0


class SignalRiskTracker:
    def __init__(self):
        self.signals = []
        self.next_number = 1

    def add_sent_signal(self, plan, now):
        direction = int(plan["direction"])
        entry = float(plan["price"])
        stop = float(plan["stop"])
        target = float(plan["target1"])
        if direction not in (-1, 1):
            raise ValueError("Invalid direction")
        if direction * (entry - stop) <= 0 or direction * (target - entry) <= 0:
            raise ValueError("Stop/target on wrong side of entry")
        signal = TrackedSignal(self.next_number, direction, entry, stop, target, now)
        self.next_number += 1
        self.signals.append(signal)
        self.signals = self.signals[-3:]
        return signal

    def check(self, prices, now):
        """Return (signal, reason, current_price) events.

        Events need to be acknowledged only AFTER Telegram confirms delivery.
        Never infer real account holdings from a sent signal.
        """
        if not prices:
            return []
        current = prices[-1]
        events = []
        survivors = []
        for item in self.signals:
            if now - item.sent_at >= SIGNAL_TTL_SECONDS:
                continue
            # A reference TP may have been reached; stop reporting as if open.
            if item.direction * (current - item.target1) >= 0:
                continue
            if item.direction * (current - item.stop) <= 0:
                events.append((item, "stop_breached", current))
                survivors.append(item)
                continue
            adverse = -item.direction * (current - item.entry)
            stop_gap = abs(item.entry - item.stop)
            against_5m = len(prices) >= 6 and (
                item.direction * (current - prices[-6]) / item.entry <= -MIN_COUNTER_MOVE_5M
            )
            if adverse >= EARLY_RISK_FRACTION * stop_gap and against_5m:
                item.bad_samples += 1
            else:
                item.bad_samples = 0
            if item.bad_samples >= CONSECUTIVE_RISK_CHECKS:
                events.append((item, "early_invalidation", current))
            survivors.append(item)
        self.signals = survivors
        return events

    def acknowledge(self, signal):
        # One event per reference signal to avoid repeated emergency messages.
        self.signals = [s for s in self.signals if s is not signal]


def format_risk_alert(signal, reason, price, time_kst):
    side = "롱" if signal.direction == 1 else "숏"
    move_pct = signal.direction * (price / signal.entry - 1) * 100
    reason_ko = (
        "참고 손절선 도달 또는 이탈" if reason == "stop_breached"
        else "반대 방향 움직임이 2회 연속 확인됨"
    )
    return (
        f"[LBank BTCUSDT | 긴급 위험 경고 - 기존 {side} 신호]\n"
        f"한국시간: {time_kst}\n"
        f"판단 근거: {reason_ko}\n"
        f"기존 신호 관심가: {signal.entry:,.1f} USDT\n"
        f"현재 관측가: {price:,.1f} USDT\n"
        f"기존 참고 손절가: {signal.stop:,.1f} USDT\n"
        f"신호 기준 방향별 가격 변화: {move_pct:+.3f}% (수수료 미반영)\n"
        "이 신호에 따라 실제 포지션을 보유 중이라면 즉시 포지션과 손절 주문을 확인하고 "
        "시장가 청산을 포함한 위험 축소를 검토하세요.\n"
        "미진입/기청산 상태라면 무시하세요.\n"
        "1분 가격표본에 근거한 조건부 경고이며 포지션 보유·주문 체결·실제 손익은 확인할 수 없습니다.\n"
        "거래소 손절 주문을 해제하지 마세요. 자동 청산 주문은 실행하지 않습니다."
    )
