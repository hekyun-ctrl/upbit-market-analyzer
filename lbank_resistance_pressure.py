"""Descriptive resistance/pressure observations; never grants entry or overrides risk."""
import math
from statistics import median
from lbank_trend_runner import valid_state,atr_wilder
from lbank_entry_quality import confirmed_pivot_barriers
from lbank_entry_priority import BARRIER_CLUSTER_ATR
POLICY='native_resistance_pressure_observation_v1'
def resistance_pressure(state,direction,now):
    empty=dict(policy=POLICY,ready=False,live_gate=False,entry_override=False,
               strength_comparison=None,reason='invalid_or_incomplete_state')
    try:
        if direction not in (-1,1) or not valid_state(state,now):return empty
        bars=state['five_bars'];quote=float(state['quote']);atr=atr_wilder(bars)
        if len(bars)<23 or atr is None or atr<=0 or not math.isfinite(quote) or quote<=0:return empty
        last=bars[-3:];base=bars[-23:-3];v0=median(b['volume'] for b in base)
        volume_ratio=sum(b['volume'] for b in last)/3/v0 if v0>0 else None
        progress=direction*(last[-1]['close']-base[-1]['close'])
        distance=sum(abs(b['close']-a['close']) for a,b in zip([base[-1]]+last,last))
        # Include crossed pivots, so levels cannot disappear from this observation at quote crossing.
        pivots=confirmed_pivot_barriers(bars,direction,0 if direction==1 else float('inf'))
        level_key='high' if direction==1 else 'low'
        nearest=sorted(pivots,key=lambda p:abs(p['price']-quote))[:5]
        levels=[]
        for p in nearest:
            clustered=[(i,b) for i,b in enumerate(bars) if b['start'] in
                {x['start'] for x in pivots if abs(x['price']-p['price'])<=BARRIER_CLUSTER_ATR*atr}]
            reactions=[]
            for i,b in clustered:
                # Only already completed post-pivot bars; no quote/future volumes.
                after=bars[i+1:min(i+3,len(bars))]
                reactions.append(max([0.]+[direction*(b[level_key]-x['close'])/atr for x in after]))
            levels.append(dict(price=p['price'],start=p['start'],distance_atr=direction*(p['price']-quote)/atr,
                confirmed_pivot_touches=len(clustered),median_rejection_atr=median(reactions) if reactions else None,
                quote_crossed=direction*(quote-p['price'])>0,
                closed5_crossed=direction*(bars[-1]['close']-p['price'])>0))
        return dict(empty,ready=True,reason='observed_not_a_validated_predictor',direction=direction,
            at=now,last5_start=bars[-1]['start'],quote=quote,atr5=atr,
            signed_three_bar_progress_atr=progress/atr,
            signed_close_path_efficiency=progress/distance if distance else 0.,
            three_bar_volume_to_prior20_median=volume_ratio,
            total_volume_is_not_aggressor_buy_sell_flow=True,nearby_confirmed_levels=levels)
    except (KeyError,TypeError,ValueError,IndexError,OverflowError):return empty

class PressureObserver:
    def __init__(self):self.key=None
    def step(self,state,now):
        if not state or not valid_state(state,now):return []
        key=state['five_bars'][-1]['start']
        if key==self.key:return []
        rows=[resistance_pressure(state,d,now) for d in (1,-1)]
        self.key=key
        return rows

