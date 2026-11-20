"""Strict counterfactual signal replay (paper analysis, NOT a trading engine).

Historical signal timing and all following 1m bars must be supplied by the
caller. Entry is next complete bar OPEN after signal visibility; uncertain
same-bar TP/SL is counted as STOP. Fees, slippage and incompletes reported.
No profitable-signal claims without real auditable data.
"""
from dataclasses import dataclass
import math
from collections import Counter


@dataclass(frozen=True)
class ReplaySignal:
    observed_at: float
    side: int
    stop: float
    target: float
    strategy: str = "unknown"
    why_blocked: str = ""
    trigger: float | None = None


def net_outcome(side, entry, level, *, fee_fraction, exit_slippage_fraction,
                funding_reserve_fraction=0):
    exit_price = level * (1 - side * exit_slippage_fraction)
    pnl = (side * (exit_price-entry) - fee_fraction*(entry+exit_price)
           - funding_reserve_fraction*entry)
    return exit_price, pnl


def assess_geometry(side, entry, stop, target, *, fee_fraction=.0006,
                    exit_slippage_fraction=.0003, funding_reserve_fraction=0,
                    min_net_rr=None, max_stop_fraction=None):
    prices = (entry, stop, target)
    costs_values = (fee_fraction, exit_slippage_fraction, funding_reserve_fraction)
    if (side not in (-1, 1) or not all(type(x) in (int, float) and math.isfinite(x)
                                       and x > 0 for x in prices)
            or not all(type(x) in (int, float) and math.isfinite(x) and 0 <= x < 1
                       for x in costs_values)):
        return {"status": "invalid_geometry"}
    if (side*(entry-stop) <= 0 or side*(target-entry) <= 0):
        return {"status":"outside_stop_target"}
    costs = dict(fee_fraction=fee_fraction,
                 exit_slippage_fraction=exit_slippage_fraction,
                 funding_reserve_fraction=funding_reserve_fraction)
    _, gain = net_outcome(side, entry, target, **costs)
    _, stop_pnl = net_outcome(side, entry, stop, **costs)
    loss = -stop_pnl
    ratio = gain / loss if loss > 0 else None
    distance = abs(entry-stop)/entry
    status = ("stop_distance" if max_stop_fraction is not None and distance > max_stop_fraction
              else "insufficient_net_rr" if ratio is None or
                  min_net_rr is not None and ratio < min_net_rr else "qualified_shadow")
    return dict(status=status, net_rr=ratio, stop_fraction=distance,
                net_loss_per_btc=loss, net_gain_per_btc=gain,
                btc_per_usdt_risk=1/loss if loss > 0 else None)


def replay_one(signal, closed_minutes, *, fee_fraction=.0006,
               exit_slippage_fraction=.0003, funding_reserve_fraction=0, max_chase_fraction=None,
               max_bars=60, period=60, min_net_rr=None, max_stop_fraction=None):
    result=dict(strategy=signal.strategy, blocked=signal.why_blocked,
                observed_at=signal.observed_at, classification="not_testable",
                fills="modelled_next_open_not_verified", period=period,
                fee_fraction=fee_fraction, slippage_fraction_each_side=exit_slippage_fraction,
                funding_reserve_fraction=funding_reserve_fraction)
    if (signal.side not in (-1,1) or type(max_bars) is not int or max_bars < 1
            or period not in (60, 300)
            or not math.isfinite(signal.observed_at) or signal.observed_at < 0
            or not all(math.isfinite(x) and x>0
                       for x in (signal.stop,signal.target))
            or not all(math.isfinite(x) and 0 <= x < 1 for x in
                       (fee_fraction, exit_slippage_fraction, funding_reserve_fraction))):
        return result
    expected_start = int(signal.observed_at//period)*period+period
    try:
        after = [bar for bar in closed_minutes if bar["start"] >= expected_start]
    except (KeyError, TypeError):
        return result
    if not after:
        return dict(result, classification="no_future_bars")
    starts = Counter(bar["start"] for bar in after)
    indexed = {bar["start"]:bar for bar in after}
    if expected_start not in indexed:
        return dict(result, classification="missing_next_entry_bar")
    cost = dict(fee_fraction=fee_fraction, exit_slippage_fraction=exit_slippage_fraction,
                funding_reserve_fraction=funding_reserve_fraction)
    for offset in range(max_bars):
        expected = expected_start+offset*period
        if expected not in indexed:
            if expected <= max(indexed):
                return dict(result, classification="data_gap_or_invalid_candle")
            break
        b = indexed[expected]
        try:
            if (type(b["start"]) is not int or starts[expected] != 1
                    or b.get("period", period) != period or b.get("quality") != "stream_closed"
                    or not all(math.isfinite(float(b[k])) and float(b[k])>0
                               for k in ("open","high","low","close"))
                    or b["low"]>min(b["open"],b["close"])
                    or b["high"]<max(b["open"],b["close"])):
                return dict(result, classification="data_gap_or_invalid_candle")
        except (KeyError,TypeError,ValueError,OverflowError):
            return dict(result, classification="data_gap_or_invalid_candle")
        side = signal.side
        if offset == 0:
            entry = b["open"]*(1+side*exit_slippage_fraction)
            geometry = assess_geometry(side, entry, signal.stop, signal.target,
                **cost, min_net_rr=min_net_rr, max_stop_fraction=max_stop_fraction)
            result.update(entry=entry, entry_at=expected, stop=signal.stop,
                          target=signal.target, geometry=geometry)
            if geometry["status"] == "outside_stop_target":
                return dict(result, classification="invalid_at_next_open")
            if signal.trigger is not None:
                displacement = side*(b["open"]/signal.trigger-1)
                if displacement <= 0:
                    return dict(result, classification="structure_lost_before_entry")
                if max_chase_fraction is not None and displacement >= max_chase_fraction:
                    return dict(result, classification="late_before_entry")
            if geometry["status"] != "qualified_shadow":
                return dict(result, classification=geometry["status"])
        opening_stop = side*(b["open"]-signal.stop) <= 0
        opening_target = side*(b["open"]-signal.target) >= 0
        stop_hit = (b["low"] <= signal.stop if side == 1 else b["high"] >= signal.stop)
        target_hit = (b["high"] >= signal.target if side == 1 else b["low"] <= signal.target)
        result.update(last_closed_price=b["close"], minutes_observed=(offset+1)*period//60)
        if not (stop_hit or target_hit):
            continue
        loss = opening_stop or (not opening_target and stop_hit)
        ambiguous = not (opening_stop or opening_target) and stop_hit and target_hit
        level = b["open"] if opening_stop else signal.stop if loss else signal.target
        execution, pnl = net_outcome(side, entry, level, **cost)
        return dict(result, classification="stop_first" if loss else "target_first",
                    ambiguous=ambiguous, exit=execution, exit_bar_start=expected,
                    net_usdt_per_btc=pnl, held_minutes=(offset+1)*period//60)
    return dict(result, classification="unresolved")


def summarize_replays(replays):
    tested=[r for r in replays if r.get("classification") in ("stop_first","target_first")]
    pnl=[r["net_usdt_per_btc"] for r in tested]
    wins=sum(x>0 for x in pnl)
    loss=sum(x<0 for x in pnl)
    gross_loss=-sum(x for x in pnl if x<0)
    equity=high=drawdown=0.
    for r in sorted(tested, key=lambda r: r["exit_bar_start"]):
        equity+=r["net_usdt_per_btc"]
        high=max(high,equity)
        drawdown=max(drawdown,high-equity)
    return dict(total=len(replays),resolved=len(tested),wins=wins,losses=loss,
                target_first=sum(r["classification"]=="target_first" for r in tested),
                stop_first=sum(r["classification"]=="stop_first" for r in tested),
                unresolved=sum(r.get("classification")=="unresolved" for r in replays),
                untestable=len(replays)-len(tested)-sum(r.get("classification")=="unresolved" for r in replays),
                win_rate=(wins/len(tested) if tested else None),
                net_per_btc_total=(sum(pnl) if tested else None),
                profit_factor=(sum(x for x in pnl if x>0)/gross_loss if gross_loss else None),
                max_drawdown_usdt_per_btc=(drawdown if tested else None),
                note="historical_model_closed_trade_drawdown_per_btc_not_account_equity")
