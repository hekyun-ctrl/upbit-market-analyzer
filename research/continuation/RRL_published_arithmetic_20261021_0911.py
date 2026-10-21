"""Arithmetic reconciliation of visually transcribed published tables, NOT a backtest."""
import json,hashlib
from decimal import Decimal as D
from pathlib import Path
p=Path('rrl_published.pdf')
assert hashlib.sha256(p.read_bytes()).hexdigest()=='e422598ceaa3830bd2f443e1bda32e7c2c57e9409dd8b88c0550ec3da062e944'
net=D('3.498');carry=D('.713');execution=D('-.542')
share=carry/net
gross_price=net-carry-execution
assert gross_price+carry+execution==net
assert share<D('.21') and share>D('.20')
conditional_execution_double=net+execution
assert conditional_execution_double==D('2.956')
n=D(250);seedmean=D('2.977');seedse=D('.047')
out=dict(version='RRL_published_table_arithmetic_v1',
 source_url='https://discovery.ucl.ac.uk/id/eprint/10147117/1/The_Recurrent_Reinforcement_Learning_Crypto_Agent.pdf',
 pdf_SHA256=hashlib.sha256(p.read_bytes()).hexdigest(),bytes=len(p.read_bytes()),
 publication='IEEEAccess10:38590-38599, published2022-04-11/current2022-04-15',
 table1_page=38597,table2_page=38598,visually_verified=True,
 original_price_backtest_performed=False,original_trader_ledger_obtained=False,
 table1=dict(daily_observations=1684,position_mean=.405,execution_sum=str(execution),
 carry_sum=str(carry),pnl_sum=str(net),IR='1.46',minimum_daily_pnl='-.155'),
 arithmetic=dict(carry_share_of_net=str(share),carry_percentage_points='71.3',
 inferred_price_component=str(gross_price),net_excluding_carry=str(net-carry),
 conditional_same_path_execution_only_double=str(conditional_execution_double)),
 interpretation='Body/table imply carry+71.3percentagepoints, about20.38%of+349.8% net, unlike abstract/conclusion wording71%ofreturn. Preserve discrepancy; no claim which wording author intended.',
 double_total_cost_result=None,
 double_cost_limit='Negative funding settlements not released, cannot double funding charges without doubling receipts. Same-path execution-only stress is NOT refitted strategy or full-cost validated return.',
 seed_table=dict(trials=250,mean_return=str(seedmean),std='.740',se_mean=str(seedse),
 lower_mean='2.886',upper_mean='3.069',minimum_return='.654',maximum_return='5.021',
 mean_IR='1.160',IR_lower_mean='1.123',IR_upper_mean='1.197'),
 uncertainty='Seed initialisation CI on same market history is not a new-market/serial-correlation adjusted future expectancy bound.',
 missing=['Exact complete orderbook/trade/funding feature list and preprocessing','Fixed original raw quote/trade/funding inputs and SHA','Original public code/weight seed and closed ledger link','Manual fixed initialstop/TP1/size/fill-delay contract','Contract payoff/margin translation to linear LBank USDT'],
 no_promote=True)
Path('report.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out))
