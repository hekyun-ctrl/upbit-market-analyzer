"""Scheduling and causal timing evidence; never a strategy/position-size gate."""
import math

from lbank_native_entry import SOURCE

POLICY = "native_completed_bar_schedule_v1"


def completed_key(state):
    """A ready feed has already verified closure, continuity and clock alignment."""
    if not state or state.get("source") != SOURCE:
        return None
    try:
        minute, five = state["minute_bars"][-1], state["five_bars"][-1]
        if minute["quality"] != "stream_closed" or five["quality"] != "stream_closed":
            return None
        return minute["start"], five["start"]
    except (KeyError, IndexError, TypeError):
        return None


def setup_evidence(state, quote_at, evaluated_at, first_ready_observed_at, trigger):
    key = completed_key(state)
    if key is None:
        return None
    close_at = key[0] + 60
    return dict(policy=POLICY, trigger=trigger, last_closed_1m_start=key[0],
                last_closed_5m_start=key[1], nominal_1m_close_at=close_at,
                first_ready_observed_at=first_ready_observed_at,
                quote_observed_at=quote_at, candidate_evaluated_at=evaluated_at,
                evaluation_after_nominal_close_seconds=evaluated_at-close_at,
                evaluation_after_first_ready_observation_seconds=evaluated_at-first_ready_observed_at,
                source_publication_at=None,
                publication_note="nominal_close_is_not_actual_publication_time")


def confirmation_evidence(watch, plan, state, quote_at, evaluated_at):
    """Compare prices in side-aware terms, without calling either price profitable."""
    evidence = dict(watch["plan"].get("entry_timing") or {})
    initial, current = float(watch["plan"]["price"]), float(plan["price"])
    direction = plan["direction"]
    if not all(math.isfinite(p) and p > 0 for p in (initial, current)) or direction not in (-1, 1):
        raise ValueError("invalid entry timing prices/direction")
    signed = direction * (current / initial - 1) * 100
    quality = plan.get("entry_quality") or {}
    priority = quality.get("priority") or {}
    ranked = quality.get("ranked_entry") or {}
    first_qualified_at = evidence.get("first_qualified_candidate_detected_at")
    if type(first_qualified_at) not in (int, float) or not math.isfinite(first_qualified_at):
        first_qualified_at = None
    nominal_close_at = evidence.get("nominal_1m_close_at")
    if type(nominal_close_at) not in (int, float) or not math.isfinite(nominal_close_at):
        nominal_close_at = None
    evidence.update(policy=POLICY, watch_quote_at=watch["time"],
                    confirmation_quote_at=quote_at, confirmation_evaluated_at=evaluated_at,
                    first_qualified_candidate_to_confirmation_seconds=(
                        evaluated_at-first_qualified_at if first_qualified_at is not None else None),
                    nominal_close_to_confirmation_seconds=(
                        evaluated_at-nominal_close_at if nominal_close_at is not None else None),
                    watch_age_at_confirmation_seconds=quote_at-watch["time"],
                    watch_quote=initial, confirmation_quote=current,
                    side_adjusted_quote_change_pct=signed,
                    entry_quote_improved_for_side=signed < 0,
                    minute_followthrough=quality.get("minute_followthrough"),
                    higher_direction_intact=ranked.get("higher_direction_intact"),
                    dominant_intact=priority.get("dominant_intact"),
                    extension_atr=ranked.get("extension_atr"),
                    target_distance_atr=ranked.get("target_distance_atr"),
                    strategy_gate_changed=bool(quality.get('breakout_confirmation')),
                    breakout_confirmation=quality.get('breakout_confirmation'),
                    existing_risk_gates_changed=False, orderbook_verified=False,
                    profitability_verified=False)
    return evidence
