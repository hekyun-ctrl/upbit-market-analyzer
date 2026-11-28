"""Low-noise *non-actionable* BTCUSDT trend onset notice.

Only completed contiguous exchange-native-derived 5m bars can trigger this
message. It does not call or bypass the entry, risk, stop or target gates and
never places orders. A notice is information, not a trade signal.
"""
import math
from datetime import datetime, timedelta, timezone

from lbank_trend_route import rank_entry_routes

KST = timezone(timedelta(hours=9))
COOLDOWN_SAME_SIDE = 45 * 60
COOLDOWN_OPPOSITE = 15 * 60
MAX_DAILY_PREVIEWS = 8


def trend_onset_preview(route, state, now, alert_state):
    """Return an early *informational* note or None, without mutating state."""
    if not route or not route.get("ready") or route.get("regime") != "impulse":
        return None
    direction = route.get("direction")
    if direction not in (-1, 1) or not isinstance(state, dict):
        return None
    five = state.get("five_bars")
    if not isinstance(five, list) or len(five) < 21:
        return None
    last = five[-1]
    try:
        start, close = int(last["start"]), float(last["close"])
        quote = float(state["quote"])
        at = float(now)
    except (KeyError, ValueError, TypeError, OverflowError):
        return None
    if (last.get("quality") != "stream_closed" or start % 300
            or not all(math.isfinite(x) for x in (at, close, quote))
            or close <= 0 or quote <= 0
            or not start + 300 <= at <= start + 420):
        return None
    # A continuing impulse is NOT a new event. No repeated noise while the
    # same trend remains active or merely updates its latest quote.
    prior = rank_entry_routes(dict(state, five_bars=five[:-1]))
    if (prior.get("ready") and prior.get("direction") == direction
            and prior.get("regime") == "impulse"):
        return None

    day = datetime.fromtimestamp(at, tz=KST).date().isoformat()
    if (alert_state.get("trend_preview_day") == day
            and int(alert_state.get("trend_preview_count", 0)) >= MAX_DAILY_PREVIEWS):
        return None
    if alert_state.get("trend_preview_last_bar") == start:
        return None
    last_at = alert_state.get("trend_preview_last_at")
    last_dir = alert_state.get("trend_preview_last_direction")
    if isinstance(last_at, (int, float)) and math.isfinite(last_at):
        if at - last_at < (COOLDOWN_SAME_SIDE if last_dir == direction
                           else COOLDOWN_OPPOSITE):
            return None
    m = route.get("momentum_pct") or {}
    m5, m15, m1h = (m.get(k) for k in ("5m", "15m", "1h"))
    if any(v is None or not isinstance(v, (int, float)) or not math.isfinite(v)
           for v in (m5, m15, m1h)):
        return None
    if direction * m5 <= .05 or direction * m15 <= .12:
        return None

    side = "상승" if direction == 1 else "하락"
    icon = "🟢" if direction == 1 else "🔴"
    ref = (route.get("reference_high_6x5m") if direction == 1
           else route.get("reference_low_6x5m"))
    if not isinstance(ref, (int, float)) or not math.isfinite(ref) or ref <= 0:
        return None
    aligned = direction * m1h > .20
    upper = "1시간 흐름 일치" if aligned else "1시간 미확인/역행 가능성"
    kst = datetime.fromtimestamp(at, tz=KST).strftime("%m/%d %H:%M")
    note = (
        f"[ZB BTCUSDT | {icon} {side} 추세 조기 포착 · 진입 미승인]\n"
        f"⏱ {kst} KST | 완료 5분봉 전환 감지\n"
        f"📈 완료봉 종가 {close:,.1f} | 최근 관측가 {quote:,.1f} USDT\n"
        f"📍 직전 6개 완료봉 {'고점' if direction == 1 else '저점'} {ref:,.1f}\n"
        f"📊 5분 {m5:+.2f}% | 15분 {m15:+.2f}% | {upper}\n"
        "⏳ 공식 진입 미승인: 손절·목표·수수료 차감 손익비 검증 통과 전입니다.\n"
        "⚠️ 관심 알림이며 매수·매도 지시가 아닙니다. 지연·급변 가능, 자동주문 없음."
    )
    return dict(message=note, at=at, bar=start, direction=direction, day=day)


def record_delivered_preview(alert_state, event):
    """Persist separately from formal signal cooldown/count on API ACK."""
    day = event["day"]
    alert_state["trend_preview_count"] = (
        int(alert_state.get("trend_preview_count", 0)) + 1
        if alert_state.get("trend_preview_day") == day else 1)
    alert_state["trend_preview_day"] = day
    alert_state["trend_preview_last_at"] = event["at"]
    alert_state["trend_preview_last_bar"] = event["bar"]
    alert_state["trend_preview_last_direction"] = event["direction"]
