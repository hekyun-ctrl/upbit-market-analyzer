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
    half_gain = float(plan["net_gain1"]) * .5
    combined_gain = half_gain + float(plan["net_gain2"]) * .5
    rr = combined_gain / float(plan["net_loss"])

    # A verified structure retest can still conflict with a 45m sampled trend.
    # Keep this material warning visible ABOVE the entry, not below TP2.
    ctx = plan.get("sampled_context") or {}
    countertrend = (
        plan.get("strategy") == "trend_retest"
        and plan.get("direction", 0) * float(ctx.get("trend15_pct", 0.0)) < 0
        and not ctx.get("reversal_confirmed", False)
    )
    native = plan.get("data_source") == "lbank_native_1m_aggregated_5m"
    context_label = "45분 원본 봉" if native else "45분 표본"
    caution = (f"⚠️ {context_label} 역추세·반전 미확인 | 신중히 판단\n"
               if countertrend else "")

    source_note = (
        "⚠️ 관찰 신호(실전 진입 추천 아님)·원본 5분봉 기반/호가 미검증\n"
        if native else
        "⚠️ 관찰 신호(실전 진입 추천 아님)·실제 5분봉/호가 미검증\n"
    )
    runner_note = " | 추세 확인 시 목표 연장·보호선 조정" if plan.get("runner_enabled") else ""
    context = plan.get("rsi_context") or {}
    if context.get("ready"):
        source_note = source_note.rstrip("\n") + " | 보조: " + context["label"] + "\n"
    market_note = (plan.get("market_context") or {}).get("line", "")
    if market_note:
        source_note += market_note + "\n"
    technique_note = (plan.get("public_techniques") or {}).get("label", "")
    if technique_note:
        source_note = source_note.rstrip("\n") + " | 참고: " + technique_note + "\n"
    if plan.get("entry_quality"):
        quality=plan['entry_quality']
        label=('상위 구조 우선' if quality.get('priority',{}).get('dominant_intact')
               else '기존 심사 통과·보조근거 점검')
        source_note = source_note.rstrip("\n") + " | " + label + "\n"
    return (
        f"[LBank BTCUSDT | {icon} {side} | 조건부 진입 검토]\n"
        f"⏱ {kst} KST | 유효 5분{reference}\n"
        f"{source_note}{caution}"
        f"📍 진입가: {plan['price']:,.1f} USDT\n"
        f"💰 진입 증거금: {plan['margin']:,.2f} USDT "
        f"(시드 {margin_pct:.2f}%, 최대 {max_margin_fraction * 100:.0f}%)\n"
        f"⚡ 레버리지 {leverage:.0f}배 | 분할 순RR {rr:.2f} | 포지션 {plan['notional']:,.2f} USDT "
        f"| 기준시드 노출 {plan['notional']/reference_balance:.2f}배\n"
        f"🛑 손절가: {plan['stop']:,.1f} USDT "
        f"| 예상 총손실 -{plan['net_loss']:,.2f} USDT "
        f"(기준시드 {plan['net_loss']/reference_balance*100:.2f}%)\n"
        f"🎯 목표가1: {plan['target1']:,.1f} USDT "
        f"| 50% 익절 가정 +{half_gain:,.2f} USDT\n"
        f"🎯 목표가2: {plan['target2']:,.1f} USDT "
        f"| 나머지 50% 익절 시 합계 +{combined_gain:,.2f} USDT{runner_note}"
    )
