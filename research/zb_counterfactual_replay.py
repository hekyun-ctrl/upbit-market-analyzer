"""Strict counterfactual signal replay (paper analysis, NOT a trading engine).

Historical signal timing and all following 1m bars must be supplied by the
caller. Entry is next complete bar OPEN after signal visibility; uncertain
same-bar TP/SL is counted as STOP. Fees, slippage and incompletes reported.
No profitable-signal claims without real auditable data.
"""
from dataclasses import dataclass
import math


@dataclass(frozen=True)
class ReplaySignal:
    observed_at: int
    side: int
    stop: float
    target: float
    strategy: str = "unknown"
    why_blocked: str = ""


def replay_one(signal, closed_minutes, *, fee_fraction=.0006,
               exit_slippage_fraction=.0003, max_bars=60):
    result=dict(strategy=signal.strategy, blocked=signal.why_blocked,
                observed_at=signal.observed_at, classification="not_testable",
                fills="modelled_next_open_not_verified", funding="excluded")
    if (signal.side not in (-1,1) or max_bars<1
            or not all(math.isfinite(x) and x>0
                       for x in (signal.stop,signal.target))
            or not (0<=fee_fraction<1 and 0<=exit_slippage_fraction<1)):
        return result
    bars=sorted(closed_minutes,key=lambda b:b["start"])
    after=[b for b in bars if b["start"] >= signal.observed_at]
    if not after:
        result["classification"]="no_future_bars"
        return result
    if after[0]["start"] > signal.observed_at+60:
        result["classification"]="missing_next_entry_bar"
        return result
    used=after[:max_bars]
    expected=used[0]["start"]
    for b in used:
        try:
            if (b["start"] != expected or b.get("quality") != "stream_closed"
                    or not all(math.isfinite(float(b[k])) and float(b[k])>0
                               for k in ("open","high","low","close"))
                    or b["low"]>min(b["open"],b["close"])
                    or b["high"]<max(b["open"],b["close"])):
                result["classification"]="data_gap_or_invalid_candle"
                return result
        except (KeyError,TypeError,ValueError,OverflowError):
            result["classification"]="data_gap_or_invalid_candle"
            return result
        expected+=60
    entry=used[0]["open"]*(1+signal.side*exit_slippage_fraction)
    if signal.side*(signal.stop-entry)>=0 or signal.side*(signal.target-entry)<=0:
        result["classification"]="invalid_at_next_open"
        return result
    result.update(entry=entry,stop=signal.stop,target=signal.target)
    for offset,b in enumerate(used):
        stop_hit=(b["low"]<=signal.stop if signal.side==1
                  else b["high"]>=signal.stop)
        target_hit=(b["high"]>=signal.target if signal.side==1
                    else b["low"]<=signal.target)
        if stop_hit or target_hit:
            # Ambiguous intra-bar order => pessimistic stop-first result.
            ambiguous=bool(stop_hit and target_hit)
            exit_price=(signal.stop*(1-signal.side*exit_slippage_fraction)
                        if stop_hit else signal.target*(1-signal.side*exit_slippage_fraction))
            pnl=signal.side*(exit_price-entry)-fee_fraction*(entry+exit_price)
            result.update(classification="stop_first" if stop_hit else "target_first",
                          ambiguous=ambiguous,exit=exit_price,
                          net_usdt_per_btc=pnl,held_minutes=offset+1)
            return result
    result["classification"]="unresolved"
    result["last_closed_price"]=used[-1]["close"]
    result["minutes_observed"]=len(used)
    return result


def summarize_replays(replays):
    tested=[r for r in replays if r.get("classification") in ("stop_first","target_first")]
    total=sum(r["net_usdt_per_btc"] for r in tested)
    wins=sum(r["classification"]=="target_first" for r in tested)
    loss=sum(r["classification"]=="stop_first" for r in tested)
    return dict(total=len(replays),resolved=len(tested),wins=wins,losses=loss,
                unresolved=sum(r.get("classification")=="unresolved" for r in replays),
                untestable=len(replays)-len(tested)-sum(r.get("classification")=="unresolved" for r in replays),
                win_rate=(wins/len(tested) if tested else None),
                net_per_btc_total=(total if tested else None),
                note="historical_model_not_live_execution")
