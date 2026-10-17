"""Post-entry monitoring of previously SENT LBank BTCUSDT price signals.

These are hypothetical signals, not confirmed positions or exchange orders.
No private credentials, orders, or authenticated position tracking.
"""
import json
import logging
import math
import os
from dataclasses import dataclass, asdict
from lbank_trend_runner import RUNNER_TTL, runner_decision

LOG = logging.getLogger("lbank_signal_risk")


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
    target2: float | None = None
    target1_hit: bool = False
    target1_notified: bool = False
    target2_hit: bool = False
    target1_hit_price: float | None = None
    target2_hit_price: float | None = None
    runner_enabled: bool = False
    strategy: str = "unknown"
    barrier: float | None = None
    runner_last_bar: int = -1
    pending_runner: dict | None = None


class SignalRiskTracker:
    def __init__(self, path=None):
        # Optional persistent record for a Railway mounted volume.
        # No path = lightweight in-memory tracker in existing unit tests.
        self.path = path
        self.signals = []
        self.next_number = 1
        self._restore()

    def _restore(self):
        if not self.path:
            return
        try:
            with open(self.path, encoding="utf-8") as f:
                raw = json.load(f)
            if not isinstance(raw, dict) or raw.get("schema") != 1:
                return
            restored = []
            for item in raw.get("signals", [])[-16:]:
                signal = TrackedSignal(**item)
                if (signal.direction not in (-1, 1) or
                    not all(math.isfinite(float(x)) for x in
                            (signal.entry, signal.stop, signal.target1, signal.sent_at)) or
                    signal.entry <= 0 or signal.stop <= 0 or signal.target1 <= 0 or
                    ((signal.direction * (signal.entry - signal.stop) < 0 and
                      not (signal.runner_enabled and signal.target1_notified)) or
                     (signal.entry == signal.stop and not signal.target1_notified)) or
                    signal.direction * (signal.target1 - signal.entry) <= 0 or
                    (signal.target2 is not None and
                     (not math.isfinite(float(signal.target2)) or
                      signal.target2 <= 0 or
                      signal.direction * (signal.target2 - signal.target1) <= 0))):

                    raise ValueError("invalid tracked reference")
                # Proposals are recomputed from fresh quotes/bars after restart.
                signal.pending_runner = None
                restored.append(signal)
            self.signals = restored
            self.next_number = max(int(raw.get("next_number", 1)),
                                   max((sig.number for sig in restored), default=0) + 1)
        except FileNotFoundError:
            pass
        except (OSError, ValueError, TypeError, KeyError, OverflowError):
            LOG.warning("Saved signal references invalid; using empty tracker")

    def _save(self):
        if not self.path:
            return
        try:
            directory = os.path.dirname(self.path)
            if directory:
                os.makedirs(directory, exist_ok=True)
            tmp = self.path + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump({"schema": 1, "next_number": self.next_number,
                           "signals": [asdict(sig) for sig in self.signals]}, f)
                f.flush()
                os.fsync(f.fileno())
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.path)
        except (OSError, ValueError, TypeError):
            LOG.warning("Could not persist signal followup tracker")

    def add_sent_signal(self, plan, now):
        direction = int(plan["direction"])
        entry = float(plan["price"])
        stop = float(plan["stop"])
        target = float(plan["target1"])
        target2 = float(plan["target2"]) if plan.get("target2") is not None else None
        if direction not in (-1, 1):
            raise ValueError("Invalid direction")
        prices_to_check = [entry, stop, target] + ([] if target2 is None else [target2])
        if (not all(math.isfinite(v) and v > 0 for v in prices_to_check) or
            direction * (entry - stop) <= 0 or
            direction * (target - entry) <= 0 or
            (target2 is not None and direction * (target2 - target) <= 0)):
            raise ValueError("Stop/target on wrong side of entry")
        signal = TrackedSignal(self.next_number, direction, entry, stop, target,
                               now, target2=target2,
                               runner_enabled=bool(plan.get("runner_enabled") and target2 is not None),
                               strategy=plan.get("strategy", "unknown"), barrier=plan.get("barrier"))
        self.next_number += 1
        self.signals.append(signal)
        # At most eight entries/day; retain six-hour runners across midnight.
        self.signals = self.signals[-16:]
        self._save()
        return signal

    def check(self, prices, now, native_state=None):
        """Return (signal, reason, current_price) events after price observations.

        Target flags are latched until Telegram acknowledges delivery. TP1
        acknowledgement keeps TP2 reference tracking alive. Targets are only
        hypothetical price levels and never imply a filled order.
        """
        if not prices:
            return []
        current = float(prices[-1])
        events = []
        survivors = []
        before = [asdict(sig) for sig in self.signals]
        for item in self.signals:
            if not item.runner_enabled and now - item.sent_at >= SIGNAL_TTL_SECONDS:
                continue
            item.pending_runner = None
            if item.target2 is not None and item.direction * (current - item.target2) >= 0:
                if not item.target2_hit:
                    item.target2_hit_price = current
                if not item.target1_hit:
                    item.target1_hit_price = current
                item.target2_hit = True
                item.target1_hit = True
            elif item.direction * (current - item.target1) >= 0:
                if not item.target1_hit:
                    item.target1_hit_price = current
                item.target1_hit = True

            # Urgent stop warning takes precedence over queued target notices.
            if item.direction * (current - item.stop) <= 0:
                events.append((item, "stop_breached", current))
                survivors.append(item)
                continue
            if item.runner_enabled and now - item.sent_at >= RUNNER_TTL:
                events.append((item, "time_exit", current))
                survivors.append(item)
                continue
            if not item.target1_notified and item.target1_hit:
                events.append((item, "target1_reached",
                               item.target1_hit_price or current))
                survivors.append(item)
                continue
            if item.runner_enabled and item.target1_notified:
                proposal = runner_decision(
                    direction=item.direction, entry=item.entry, stop=item.stop,
                    target2=item.target2, sent_at=item.sent_at,
                    last_bar=item.runner_last_bar, state=native_state,
                    now=now, price=current, strategy=item.strategy, barrier=item.barrier)
                if proposal is not None:
                    # A remembered TP2 touch cannot be extended after price has
                    # retreated far below it. Honour the original target notice.
                    can_extend = (not item.target2_hit or
                                  item.direction * (current - item.target2) >= 0)
                    if (proposal["reason"] in ("trend_exit", "neutral_exit") or
                            (can_extend and proposal["reason"] == "target_extended") or
                            (not item.target2_hit and proposal["reason"] == "trail_update")):
                        item.pending_runner = proposal
                        events.append((item, proposal["reason"], current))
                        survivors.append(item)
                        continue
            if item.target1_notified and item.target2 is not None and item.target2_hit:
                events.append((item, "target2_reached",
                               item.target2_hit_price or current))
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
        if before != [asdict(sig) for sig in self.signals]:
            self._save()
        return events

    def acknowledge(self, signal, reason=None):
        """Acknowledge only after delivery; TP1 keeps a known TP2 monitored."""
        if reason in ("trail_update", "target_extended") and signal in self.signals:
            proposal = signal.pending_runner
            if proposal is None or proposal["reason"] != reason:
                raise ValueError("missing delivered runner proposal")
            # Monotonic protection: never lower a long stop or raise a short.
            if signal.direction * (proposal["stop"] - signal.stop) < 0:
                raise ValueError("runner cannot loosen protection")
            signal.stop, signal.target2 = proposal["stop"], proposal["target2"]
            signal.runner_last_bar = proposal["bar_start"]
            signal.pending_runner = None
            if reason == "target_extended":
                signal.target2_hit, signal.target2_hit_price = False, None
        elif reason == "target1_reached" and signal in self.signals:
            signal.target1_notified = True
            # Model the user's remaining-half reference stop after the TP1
            # notification. This changes reference monitoring, NOT venue orders.
            signal.stop = signal.entry
            signal.bad_samples = 0
            if signal.target2 is None:
                self.signals = [s for s in self.signals if s is not signal]
        else:
            # Stop, early invalidation, second target or legacy explicit ack.
            self.signals = [s for s in self.signals if s is not signal]
        self._save()


def format_target_alert(signal, price, time_kst):
    """One-time reference target notification, NOT a trade execution claim."""
    side = "롱" if signal.direction == 1 else "숏"
    move_pct = signal.direction * (signal.target1 / signal.entry - 1) * 100
    return (
        f"[LBank BTCUSDT | 1차 목표 도달 - {side}]\n"
        f"기존 진입 알람: #{signal.number}\n"
        f"한국시간: {time_kst}\n"
        f"현재 1차 목표 {signal.target1:,.1f} USDT ({move_pct:+.2f}%) 도달\n"
        f"목표 접촉 시 선물 가격표본: {price:,.1f} USDT\n"
        f"알람 기준 진입 참고가: {signal.entry:,.1f} USDT\n"
        f"실제 포지션 보유 중이라면 50% 익절 후 "
        f"남은 포지션 손절가는 진입가 {signal.entry:,.1f} USDT로 조정 검토 요함.\n"
        "※ LBank에서 실제 익절 체결 후 잔여 수량과 손절 주문을 직접 확인하세요.\n"
        "※ 진입가 손절은 수수료·슬리피지 차감 후 완전한 손익분기점이 아닙니다.\n"
        "※ 미진입·기청산 상태라면 관찰 참고용 알림입니다. "
        "봇은 실제 보유·체결을 확인하거나 거래소 주문을 실행하지 않습니다.\n"
        "※ 1분 가격표본 기준으로 확인한 목표 도달이며, 표본 사이 순간 터치는 놓칠 수 있습니다."
    )



def format_target2_alert(signal, price, time_kst):
    """TP2 level observed; never imply exchange execution."""
    side = "롱" if signal.direction == 1 else "숏"
    if signal.target2 is None:
        raise ValueError("No second target for this reference signal")
    move_pct = signal.direction * (signal.target2 / signal.entry - 1) * 100
    return (
        f"[LBank BTCUSDT | 2차 목표 도달 - {side}]\n"
        f"기존 진입 알람: #{signal.number}\n"
        f"한국시간: {time_kst}\n"
        f"현재 2차 목표 {signal.target2:,.1f} USDT ({move_pct:+.2f}%) 도달\n"
        f"목표 접촉 시 선물 가격표본: {price:,.1f} USDT\n"
        f"알람 기준 진입 참고가: {signal.entry:,.1f} USDT\n"
        "실제 보유 중이라면 1차 익절 후 남은 50% 익절을 검토하세요.\n"
        "※ 실제 체결과 잔여 수량은 LBank 거래소에서 직접 확인해야 합니다.\n"
        "※ 미진입 또는 이미 청산한 경우 관찰용 알림입니다. 자동주문 없음.\n"
        "※ 실제 순손익은 수수료·슬리피지·펀딩에 따라 달라집니다.\n"
        "※ 1분 선물 가격표본으로 확인한 목표 접촉이며 거래소 실제 체결가가 아닙니다."
    )


def format_risk_alert(signal, reason, price, time_kst):
    side = "롱" if signal.direction == 1 else "숏"
    move_pct = signal.direction * (price / signal.entry - 1) * 100
    if reason not in ("stop_breached", "early_invalidation"):
        raise ValueError("Unknown follow-up risk reason")
    reason_ko = ("참고 손절선 도달 또는 이탈" if reason == "stop_breached"
                 else "반대 방향 움직임이 2회 연속 확인됨")
    return (
        f"[LBank BTCUSDT | 긴급 위험 경고 - 기존 {side} 신호]\n"
        f"기존 진입 알람: #{signal.number}\n"
        "상태: 이전 조건부 진입 신호 무효화 — 이 알람을 근거로 신규 진입 금지.\n"
        "새로운 진입은 별도 신규 2단계 확인 알람이 필요합니다.\n"
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
