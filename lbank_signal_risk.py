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
from lbank_entry_quality import direction_priority, POLICY as ENTRY_PRIORITY_POLICY
from lbank_signal_thesis import (POLICY as THESIS_POLICY, REVIEW_SECONDS, TRACKING_SECONDS,
                                 assess_thesis, observed_stop_touch)

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
    entry_priority_policy: str | None = None
    caution_notified: bool = False
    priority_evidence: dict | None = None
    followup_thesis: dict | None = None
    stop_active_since: float | None = None
    last_checked_at: float | None = None
    time_review_notified: bool = False
    last_risk_bar: int = -1
    thesis_status: str | None = None
    pending_risk_reason: str | None = None
    pending_risk_evidence: dict | None = None


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
                               entry_priority_policy=(plan.get('entry_quality') or {}).get('policy'),
                               followup_thesis=json.loads(json.dumps(plan.get('followup_thesis'))),
                               stop_active_since=now,
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
            coherent=(item.followup_thesis or {}).get('policy')==THESIS_POLICY
            if not coherent and not item.runner_enabled and now - item.sent_at >= SIGNAL_TTL_SECONDS:
                continue
            item.pending_runner = None
            if coherent:item.last_checked_at=now
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
                if coherent:
                    item.pending_risk_reason='stop_breached'
                    item.pending_risk_evidence=dict(reason='observed_stop_quote',observed_price=current,observed_at=now,stop=item.stop)
                    item.priority_evidence=item.pending_risk_evidence
                events.append((item, "stop_breached", current))
                survivors.append(item)
                continue
            if coherent:
                if item.pending_risk_reason:
                    item.priority_evidence=item.pending_risk_evidence
                    events.append((item,item.pending_risk_reason,current));survivors.append(item);continue
                touch=observed_stop_touch(native_state,item.direction,item.stop,
                    item.stop_active_since if item.stop_active_since is not None else item.sent_at,now)
                if touch:
                    item.priority_evidence=touch
                    item.pending_risk_reason='stop_breached';item.pending_risk_evidence=touch
                    events.append((item,'stop_breached',current));survivors.append(item);continue
                evidence=assess_thesis(item.followup_thesis,native_state,item.direction,current,item.sent_at,now)
                item.priority_evidence=evidence
                if item.thesis_status!=evidence['status']:
                    LOG.info('SIGNAL_THESIS reference=%d evidence=%s',item.number,json.dumps(evidence,sort_keys=True))
                    item.thesis_status=evidence['status']
                if evidence['invalidated']:
                    item.pending_risk_reason='early_invalidation';item.pending_risk_evidence=evidence
                    events.append((item,'early_invalidation',current));survivors.append(item);continue
                if now-item.sent_at>=TRACKING_SECONDS:
                    events.append((item,'tracking_expired',current));survivors.append(item);continue
            if not coherent and item.runner_enabled and now - item.sent_at >= RUNNER_TTL:
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
                    now=now, price=current, strategy=item.strategy, barrier=item.barrier,
                    thesis_evidence=evidence if coherent else None)
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
            if coherent:
                # Two polls of the same minute are not independent confirmations.
                minute=(native_state or {}).get('minute_bars',[])
                bar=minute[-1]['start'] if minute else -1
                adverse_trigger=adverse>=EARLY_RISK_FRACTION*stop_gap and against_5m
                if bar>item.last_risk_bar:
                    item.last_risk_bar=bar
                    item.bad_samples=item.bad_samples+1 if adverse_trigger else 0
                if (adverse_trigger and evidence['status'] in ('weakened','unknown')
                        and not item.caution_notified
                        and (item.bad_samples>=CONSECUTIVE_RISK_CHECKS or evidence['status']=='unknown')):
                    events.append((item,'risk_caution',current))
                elif now-item.sent_at>=REVIEW_SECONDS and not item.time_review_notified:
                    events.append((item,'time_review',current))
                survivors.append(item)
                continue
            if adverse >= EARLY_RISK_FRACTION * stop_gap and against_5m:
                item.bad_samples += 1
            else:
                item.bad_samples = 0
            if item.bad_samples >= CONSECUTIVE_RISK_CHECKS:
                priority=(direction_priority(native_state,item.direction,current,item.target1,now)
                          if item.entry_priority_policy==ENTRY_PRIORITY_POLICY else {})
                item.priority_evidence=priority
                if priority.get('dominant_intact'):
                    key='low' if item.direction==1 else 'high'
                    breached=any(item.direction*(b[key]-item.stop)<=0
                                 for b in native_state['minute_bars']
                                 if b['start']>=item.sent_at and b.get('quality')=='stream_closed')
                    if breached:
                        priority=dict(priority,dominant_intact=False,post_entry_stop_wick=True)
                        item.priority_evidence=priority
                if priority.get('dominant_intact'):
                    if not item.caution_notified:events.append((item,'risk_caution',current))
                else:
                    events.append((item, "early_invalidation", current))
            survivors.append(item)
        self.signals = survivors
        if before != [asdict(sig) for sig in self.signals]:
            self._save()
        return events

    def acknowledge(self, signal, reason=None, now=None):
        """Acknowledge only after delivery; TP1 keeps a known TP2 monitored."""
        if reason == 'risk_caution' and signal in self.signals:
            signal.caution_notified=True
        elif reason == 'time_review' and signal in self.signals:
            signal.time_review_notified=True
        elif reason in ("trail_update", "target_extended") and signal in self.signals:
            proposal = signal.pending_runner
            if proposal is None or proposal["reason"] != reason:
                raise ValueError("missing delivered runner proposal")
            # Monotonic protection: never lower a long stop or raise a short.
            if signal.direction * (proposal["stop"] - signal.stop) < 0:
                raise ValueError("runner cannot loosen protection")
            if proposal['stop']!=signal.stop:
                signal.stop_active_since=now if now is not None else signal.last_checked_at or signal.sent_at
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
            signal.stop_active_since=now if now is not None else signal.last_checked_at or signal.sent_at
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
    coherent=(signal.followup_thesis or {}).get('policy')==THESIS_POLICY
    evidence=signal.priority_evidence or {}
    if coherent and reason in ('time_review','tracking_expired'):
        status={'intact':'큰 방향 근거 유지','weakened':'방향 근거 혼재','unknown':'원본 자료 미확인'}.get(evidence.get('status'),'상태 재확인')
        title='6시간 재평가 · 추적 계속' if reason=='time_review' else '24시간 알림 추적 종료'
        action=('시간 경과만으로 청산이나 새 진입을 판단하지 않습니다. 목표·손절 추적을 계속합니다.'
                if reason=='time_review' else '이 신호의 자동 알림 추적을 종료합니다. 실제 포지션·손절 주문을 직접 확인하세요. 청산 신호가 아닙니다.')
        return (f'[LBank BTCUSDT | {title} - {side}]\n신호 #{signal.number} | {time_kst} KST\n'
                f'판단: {status}\n현재 {price:,.1f} USDT | 보호 손절 {signal.stop:,.1f} USDT\n'
                f'{action}\n실제 보유·체결 미확인, 자동 주문 없음.')
    if coherent and reason=='risk_caution':
        why=('원본 자료 부족으로 방향을 재판정할 수 없음' if evidence.get('status')=='unknown'
             else '단기 역행과 근거 혼재, 진입 당시 구조의 확정 이탈은 미확인')
        return (f'[LBank BTCUSDT | 근거 재점검 · 추적 유지 - {side}]\n신호 #{signal.number} | {time_kst} KST\n'
                f'판단: {why}\n현재 {price:,.1f} USDT | 원래 보호 손절 {signal.stop:,.1f} USDT\n'
                '신호 무효화·새 진입 지시가 아닙니다. 실제 위험과 기존 손절 주문을 확인하세요.\n'
                '실제 보유·체결 미확인, 자동 주문 없음.')
    if reason == 'risk_caution':
        return (f"[LBank BTCUSDT | 단기 역행 주의 - 기존 {side} 신호]\n"
                f"기존 진입 알람: #{signal.number} | 한국시간: {time_kst}\n"
                "단기 역행이 관측됐지만 완료 5분봉 고저점 구조와 15분 흐름은 유지되고 있습니다.\n"
                "상태: 주의 관찰, 기존 신호의 완전 무효화 판정은 아닙니다.\n"
                f"현재 관측가: {price:,.1f} USDT | 원래 참고 손절: {signal.stop:,.1f} USDT\n"
                "실제 포지션과 위험을 확인하고 기존 손절 주문을 유지하세요.\n"
                "새로운 진입 근거로 사용하지 마세요. 자동 주문·실제 체결 확인은 없습니다.")
    if reason not in ("stop_breached", "early_invalidation"):
        raise ValueError("Unknown follow-up risk reason")
    reason_ko = ("참고 손절선 도달 또는 이탈" if reason == "stop_breached"
                 else "반대 방향 움직임이 2회 연속 확인됨")
    if reason=='early_invalidation' and signal.entry_priority_policy==ENTRY_PRIORITY_POLICY:
        reason_ko='단기 역행 2회 확인·상위 구조 유지 근거 확인되지 않음'
    if coherent:
        if reason=='early_invalidation':
            reason_ko=(f"진입 당시 고정 구조 {evidence.get('anchor'):,.1f}를 완료 5분봉 종가가 이탈"
                       if evidence.get('reason')=='frozen_structure_close_break' else '진입 후 완료 15분 흐름·5분 추세와 고저점의 반대 방향 전환 확인')
        elif evidence.get('reason')=='observed_stop_wick':
            reason_ko=f"진입/보호선 변경 이후 완료 {evidence['period']//60}분봉에서 손절선 침범 관측 ({evidence['observed_extreme']:,.1f})"
        elif evidence.get('reason')=='observed_stop_quote':
            reason_ko=f"참고 손절선 침범 가격표본 관측 ({evidence['observed_price']:,.1f}), 이후 반등으로 취소되지 않음"
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
        f"{'원본 완료봉과 현재가' if coherent else '1분 가격표본'}에 근거한 조건부 경고이며 포지션 보유·주문 체결·실제 손익은 확인할 수 없습니다.\n"
        "거래소 손절 주문을 해제하지 마세요. 자동 청산 주문은 실행하지 않습니다."
    )
