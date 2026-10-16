"""Compact, USDT-first human-readable LBank observation signal.

Presentation only: does not change qualification criteria or place any orders.
KST display and the money-at-risk fields are before TP2, since users often
cannot read details below it on a small Telegram screen.
"""
from datetime import datetime, timedelta, timezone

KST = timezone(timedelta(hours=9))


def format_compact_entry_alert(plan, observed_at_utc, reference_number=None,
                               *, reference_balance, leverage, max_margin_fraction):
    """Show real *margin* to allocate in USDT, not BTC contract quantity.

    Values in `plan` are hypothetical at the sampling instant. This function
    intentionally does not guess account balance, order step, or actual fill.
    """
    utc_str = observed_at_utc.replace("Z", "+00:00")
    seen = datetime.fromisoformat(utc_str)
    if seen.tzinfo is None:
        seen = seen.replace(tzinfo=timezone.utc)
    kst = seen.astimezone(KST).strftime("%m/%d %H:%M")
    side = plan["side"]
    icon = "🟢" if side == "롱" else "🔴"
    reference = f" | 신호번호: #{reference_number}" if reference_number is not None else ""
    margin_pct = 100 * float(plan["margin"]) / reference_balance

    # A verified structure retest can still conflict with a 45m sampled trend.
    # Keep this material warning visible ABOVE the entry, not below TP2.
    ctx = plan.get("sampled_context") or {}
    countertrend = (
        plan.get("strategy") == "trend_retest"
        and plan.get("direction", 0) * float(ctx.get("trend15_pct", 0.0)) < 0
        and not ctx.get("reversal_confirmed", False)
    )
    caution = ("⚠️ 45분 표본 역추세·반전 미확인 | 신중히 판단\n"
               if countertrend else "")

    return (
        f"[LBank BTCUSDT | {icon} {side} | 조건부 진입 검토]\n"
        f"⏱ {kst} KST | 유효 5분{reference}\n"
        "⚠️ 관찰 신호(실전 진입 추천 아님)·실제 5분봉/호가 미검증\n"
        f"{caution}"
        f"📍 진입가: {plan['price']:,.1f} USDT\n"
        f"💰 진입 증거금: {plan['margin']:,.2f} USDT "
        f"(시드 {margin_pct:.2f}%, 최대 {max_margin_fraction * 100:.0f}%)\n"
        f"⚡ 레버리지 {leverage:.0f}배 | 포지션 {plan['notional']:,.2f} USDT\n"
        f"🛑 손절가: {plan['stop']:,.1f} USDT "
        f"| 예상 총손실 -{plan['net_loss']:,.2f} USDT\n"
        f"🎯 목표가1: {plan['target1']:,.1f} USDT "
        f"| 예상 순이익 +{plan['net_gain1']:,.2f} USDT\n"
        f"🎯 목표가2: {plan['target2']:,.1f} USDT "
        f"| 예상 순이익 +{plan['net_gain2']:,.2f} USDT"
    )
