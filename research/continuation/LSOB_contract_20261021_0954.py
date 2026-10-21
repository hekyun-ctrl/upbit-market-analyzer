"""Bounded original LSOB source contract fixtures; no original-price performance claim."""
import json,hashlib,dataclasses
from pathlib import Path
from unittest.mock import patch
import numpy as np
import pandas as pd
import original as s
def frame(rows):
 d=pd.DataFrame(rows,columns=['Open','High','Low','Close'])
 d['Timestamp']=pd.date_range('2025-01-01',periods=len(d),freq='h',tz='UTC');d['Volume']=1.
 return d
cfg=s.Config()
base=frame([[100,102,99,101],[101,105,99,100],[100,101,97,98],[98,99,95,96],[96,97,93,94],[94,96,93,95]])
changed=base.copy();changed.loc[5,['Open','High','Low','Close']]=[94,95,91,92]
assert base.iloc[:5].equals(changed.iloc[:5])
prefix=s.build_short_setup(base.iloc[:5].copy(),1,104,cfg)
full=s.build_short_setup(base,1,104,cfg)
mutated=s.build_short_setup(changed,1,104,cfg)
assert prefix is not None and full is not None and mutated is not None
assert prefix.created_idx==4 and full.created_idx==4 and mutated.created_idx==5
assert full.entry==mutated.entry and full.stop==mutated.stop
# Only inject setup discovery to isolate ORIGINAL pending-fill / exit execution.
rows=[[99,99.5,98.5,99]]*8+[[99,106,99,101],[101,102,89,90]]
df=frame(rows)
fake=s.Setup('short',4,110,108,100,100,105,90,7)
def swings(df,left,right):
 sh=np.zeros(len(df),dtype=bool);sh[3]=True;return sh,np.zeros(len(df),dtype=bool)
def build(df,i,level,cfg):return fake if i==4 else None
execution_cfg=s.Config(swing_left=1,swing_right=1)
with patch.object(s,'find_swings',swings),patch.object(s,'build_short_setup',build),patch.object(s,'build_long_setup',lambda *a:None):
 trades,eq,inval=s.run_backtest(df,execution_cfg)
assert len(trades)==1
t=trades[0]
assert t.entry_idx==8 and t.exit_idx==9 and t.outcome=='target'
assert df.iloc[8]['Open']<t.entry<t.stop<df.iloc[8]['High']
from decimal import Decimal as D
qty=D(str(t.qty));en=D(str(t.entry));stop=D(str(t.stop))*(1+D(str(cfg.slippage_pct)))
correct_stop=D(-1)*(stop-en)*qty-en*qty*D(str(cfg.maker_fee))-stop*qty*D(str(cfg.taker_fee))
assert correct_stop<0 and t.pnl>0
source=Path('original.py').read_bytes()
out=dict(version='LSOB_710eef59_bounded_execution_contract_v1',
 source_repo='maxs231/lsob-backtest',commit='710eef5926f9f37f43581b8609c56c8e08f49403',
 source_SHA256=hashlib.sha256(source).hexdigest(),
 future_suffix=dict(prefix_cut_after_index=4,unchanged_prefix=True,
 original_created_idx=full.created_idx,mutated_future_created_idx=mutated.created_idx,
 finding='build_short_setup scans future same-direction candles beyond evaluation index; main requires created_idx==current index, so unseen future changes past setup availability.'),
 entry_bar_stop=dict(setup_discovery_injected_only=True,original_execution_function=True,
 entry_bar=8,entry_bar_OHLC=df.iloc[8][['Open','High','Low','Close']].to_dict(),
 source_trade=dataclasses.asdict(t),source_final_equity=float(eq.iloc[-1]),
 original_booked_positive_pnl=t.pnl,source_cost_model_stop_pnl_if_entry_bar_stop_applied=str(correct_stop),
 finding='Open below short entry; rally crosses entry then stop inside same candle. Original engine fills but first checks exit next candle, subsequently books target.'),
 original_raw_prices_obtained=False,original_196trade_ledger_obtained=False,
 original_price_backtest_performed=False,README_return_verified=False,
 raw_input_limit='Git tree noCSV; fetch default start/end=time.time moving730days and includes current partial candle. Exact originaldatawindow/hash not committed.',
 additional_contract_limits=['No funding in run_backtest','Body-close/new-sweep invalidation inspected before resting limit touch inside samebar, unresolved event ordering','Open end trade not returned in closed ledger; equity contains mark-to-market without total exitcost','Reported total_setups sums diagnostic cap/liquidation counts too','Original frequency196/730 <1day and risk2percent not operating authorization'],
 orders=0,messages=0,production_changed=False,promotion=False,
 decision='Do not adopt current public release or patch/optimize it to salvage advertised gains; positive authorclaims retained as unverified, not proof all liquidity-sweep methods fail.')
Path('report.json').write_text(json.dumps(out,default=str,indent=2)+'\n')
print(json.dumps(out,default=str))
