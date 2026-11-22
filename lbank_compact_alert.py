"""One-decision manual LBank entry alert. Display only; never places orders."""
from datetime import datetime, timezone, timedelta
from lbank_signal_thesis import entry_thesis_line

KST = timezone(timedelta(hours=9))
MIN_NET_RR = 1.5


def route_label(plan):
    """A/B/C classify execution paths, never confidence rankings."""
    route = plan.get("entry_route")
    if route in ("breakout", "quote_crossing") or plan.get("strategy") == "breakout":
        return "A · 돌파형"
    if route == "continuation":
        return "B · 추세지속형"
    return "C · 눌림/지지반등형"


def format_compact_entry_alert(plan, observed_at_utc, reference_number=None,
                               *, reference_balance, leverage, max_margin_fraction):
    """Show status first; never confuse an interest zone with entry approval."""
    observed = datetime.fromisoformat(observed_at_utc.replace("Z", "+00:00"))
    if observed.tzinfo is None:
        observed = observed.replace(tzinfo=timezone.utc)
    kst = observed.astimezone(KST).strftime("%m/%d %H:%M")
    expires = (observed + timedelta(minutes=5)).astimezone(KST).strftime("%H:%M:%S")
    entry, stop, target1, target2 = (float(plan[k]) for k in
                                    ("price", "stop", "target1", "target2"))
    risk, gain1, gain2 = (float(plan[k]) for k in ("net_loss", "net_gain1", "net_gain2"))
    rr1 = gain1 / risk if risk > 0 else 0.0
    rr_split = (gain1 + gain2) / (2 * risk) if risk > 0 else 0.0
    direction, side = int(plan["direction"]), plan["side"]
    icon = "🟢" if direction == 1 else "🔴"
    valid = (direction in (-1, 1) and (side == "롱") == (direction == 1)
             and side in ("롱", "숏") and min(entry, stop, target1, target2) > 0
             and direction * (entry - stop) > 0 and
             direction * (target1 - entry) > 0 and
             direction * (target2 - target1) > 0 and
             rr1 >= MIN_NET_RR - 1e-7)
    status = ("✅ 조건부 진입 검토 가능 · 2단계 확인 통과" if valid
              else "⛔ 진입 금지 · 손익비 또는 가격 구조 미달")
    ctx = plan.get("sampled_context") or {}
    counter = (plan.get("strategy") == "trend_retest"
               and direction * float(ctx.get("trend15_pct", 0.0)) < 0
               and not ctx.get("reversal_confirmed", False))
    native = plan.get("data_source") == "lbank_native_1m_aggregated_5m"
    warning = ((("⚠️ 45분 원본 봉 역추세·반전 미확인\n" if native
                 else "⚠️ 45분 표본 역추세·반전 미확인\n") if counter else ""))
    number = f" | 신호번호: #{reference_number}" if reference_number is not None else ""
    margin_pct = 100 * float(plan["margin"]) / reference_balance
    risk_pct = 100 * risk / reference_balance
    seed_exposure = float(plan["notional"]) / reference_balance
    protect = " | 1차 목표 전 수익 보호" if plan.get("profit_policy") else ""
    context = plan.get("rsi_context") or {}
    extra = " | 보조: " + str(context.get("label", "")) if context.get("ready") else ""
    ranked = plan.get("entry_decision_priority") or {}
    if ranked.get("overrides"):
        extra += " | 15분·5분 방향 우선·앞선 지지/저항 유의"
    elif ranked.get("higher_direction_intact"):
        extra += " | 15분·5분 방향 일치"
    technique = (plan.get("public_techniques") or {}).get("label")
    if technique:
        extra += " | 참고: " + str(technique)
    market = (plan.get("market_context") or {}).get("line")
    if market:
        extra += " | " + str(market)
    source = ("원본 5분봉 기반/호가 미검증" if native else "실제 5분봉/호가 미검증")
    return (
        f"[LBank BTCUSDT | {icon} {side} | {route_label(plan)}]\n"
        f"{status}\n"
        f"⏱ {kst} KST | 유효 5분({expires}까지){number}\n"
        f"{warning}"
        f"📍 진입가: {entry:,.1f} | 🛑 손절가: {stop:,.1f} USDT\n"
        f"🎯 목표가1: {target1:,.1f} | 목표가2: {target2:,.1f} USDT{protect}\n"
        f"📊 1차 순RR {rr1:.2f} | 분할 순RR {rr_split:.2f} (최소 1.50)\n"
        f"💰 진입 증거금: {plan['margin']:,.2f} USDT (시드 {margin_pct:.2f}%)"
        f" | 예상 총손실 -{risk:,.2f} USDT (기준시드 {risk_pct:.2f}%)\n"
        f"⚡ 레버리지 {leverage:.0f}배 | 포지션 {plan['notional']:,.2f} USDT"
        f" | 기준시드 노출 {seed_exposure:.2f}배 | 증거금 상한 {max_margin_fraction*100:.0f}%\n"
        f"⚠️ 관찰 신호(실전 진입 추천 아님)·{source}. 실전 주문 지시 아님. 현재 호가·손절 주문 직접 확인. 자동주문 없음.{extra}"
    )
