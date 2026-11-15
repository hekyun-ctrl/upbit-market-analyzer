"""Conservative, compact trend context for autonomous Telegram alerts.

If the native 5m stream is stale or missing, do not invent a 15m/1h
trend; observations still carry explicit no-entry decisions.
"""
from lbank_b_retest_research import trend_priority


def compact_trend(fives):
    if not fives or len(fives) < 72:
        return "상위 추세 미확인 (연속 5분봉 부족)"
    try:
        context = trend_priority(fives)
        regime = context.get("trend")
        if context.get("status") != "ready":
            return "상위 추세 미확인 (봉 연속성 또는 상위봉 부족)"
        return {
            "bearish": "하락 우세 (완료 15분·1시간봉)",
            "bullish": "상승 우세 (완료 15분·1시간봉)",
            "mixed": "방향 충돌 (15분·1시간봉)",
        }.get(regime, "상위 추세 미확인")
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        return "상위 추세 미확인 (데이터 오류)"
