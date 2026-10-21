"""Declared 3-KST-day block bootstrap, not trade shuffle. No parameter search."""
import random,statistics
from .core import *

def block_interval(daily_values,iterations=2000,seed=20261021):
    days=sorted(daily_values)
    if len(days)<3:return {'lower':None,'upper':None,'reason':'LESS_THAN_THREE_DAYS'}
    # Include no-trade days explicitly; caller must provide complete observation calendar.
    if any(b!=a+1 for a,b in zip(days,days[1:])):raise ValueError('INCOMPLETE_DAY_CALENDAR')
    values=[dec(daily_values[d]) for d in days];blocks=[values[i:i+3] for i in range(len(values)-2)];rng=random.Random(seed);means=[]
    for _ in range(iterations):
        sampled=[]
        while len(sampled)<len(values):sampled.extend(rng.choice(blocks))
        means.append(sum(sampled[:len(values)],ZERO)/len(values))
    means.sort();return {'lower':means[int(D('.025')*iterations)],'upper':means[min(iterations-1,int(D('.975')*iterations))],'iterations':iterations,'seed':seed,'unit':'KST3_consecutive_day_blocks','limitation':'Does not remove all regime/episode dependence.'}

def paired_daily_difference(new,old):
    if set(new)!=set(old):raise ValueError('PAIRED_CALENDAR_MISMATCH')
    return block_interval({d:dec(new[d])-dec(old[d]) for d in new})
