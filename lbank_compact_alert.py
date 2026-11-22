"""One-decision manual LBank entry alert. Display only; never places orders."""
from datetime import datetime, timezone, timedelta

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
    status = ("✅ 진입 검토 가능 · 2단계 확인 통과" if valid
              else "⛔ 진입 금지 · 손익비 또는 가격 구조 미달")
    ctx = plan.get("sampled_context") or {}
    counter = (plan.get("strategy") == "trend_retest"
               and direction * float(ctx.get("trend15_pct", 0.0)) < 0
               and not ctx.get("reversal_confirmed", False))
    warning = ("⚠️ 역추세·반전 미확인\n" if counter else "")
    number = f" | 신호 #{reference_number}" if reference_number is not None else ""
    margin_pct = 100 * float(plan["margin"]) / reference_balance
    return (
        f"[LBank BTCUSDT | {icon} {side} | {route_label(plan)}]\n"
        f"{status}\n"
        f"⏱ {kst} KST | {expires}까지 참고{number}\n"
        f"{warning}"
        f"📍 진입 {entry:,.1f} | 🛑 손절 {stop:,.1f} USDT\n"
        f"🎯 목표1 {target1:,.1f} | 목표2 {target2:,.1f} USDT\n"
        f"📊 1차 순RR {rr1:.2f} | 분할 순RR {rr_split:.2f} (최소 1.50)\n"
        f"💰 증거금 {plan['margin']:,.2f} USDT (시드 {margin_pct:.2f}%)"
        f" | 손절 시 예상 -{risk:,.2f} USDT\n"
        f"⚡ 레버리지 {leverage:.0f}배 | 포지션 {plan['notional']:,.2f} USDT"
        f" | 증거금 상한 {max_margin_fraction*100:.0f}%\n"
        "⚠️ 관찰 신호·실전 주문 지시 아님. 현재 호가·손절 주문 직접 확인. 자동주문 없음."
    )
