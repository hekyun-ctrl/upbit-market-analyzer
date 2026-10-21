"""Independent source-table/cash-chain reconciliation; no optimizer or signals."""
import csv,json,hashlib
from pathlib import Path
from decimal import Decimal as D
root=Path('original1120/output/tables');report=json.loads(Path('attribution1120/report.json').read_text())
h=list(csv.DictReader((root/'trend_following_history.csv').open()));segments=list(csv.DictReader((root/'trend_following_trade_pnl.csv').open()))
checks=0;equity=D(10000);before=D(0);sum_net=D(0)
for x in h:
 gross=D(x['gross_pnl']);cost=D(x['slippage_cost']);net=D(x['net_pnl'])
 assert abs(gross-cost-net)<D('1e-7');checks+=1
 equity=max(equity+net,D(0))
 assert abs(equity-D(x['V_t']))<D('1e-6');checks+=1
 sum_net+=net
assert abs(sum(D(x['net_pnl_usdt']) for x in segments)-sum_net)<D('1e-6');checks+=1
for a in ('btc','eth'):
 g=lag=c=turn=D(0);prev=D(0)
 for x in h:
  theta=D(x['Theta_t_'+a]);r=D(x[a+'_return']);delta=abs(theta-prev)
  g+=theta*r;lag+=prev*r;c+=delta*D('.0005');turn+=delta;prev=theta
 z=report['components'][a]
 for val,key in [(g,'original_gross'),(g-c,'source_net'),(lag-c,'same_path_lagged_net'),(turn,'turnover')]:
  assert abs(val-D(z[key]))<D('1e-6');checks+=1
assert D(report['components']['btc']['source_net'])>0 and D(report['components']['btc']['same_path_lagged_net'])<0;checks+=1
out=dict(verified=True,checks=checks,source_history_rows=len(h),rebalance_segments_not_closed_trades=len(segments),
 ending_equity=str(equity),net_sum=str(sum_net),
 report_sha256=hashlib.sha256(Path('attribution1120/report.json').read_bytes()).hexdigest(),
 row_artifact_sha256=hashlib.sha256(Path('attribution1120/BTC_ETH_cash_rows.json').read_bytes()).hexdigest(),
 scope='Original-history accounting and same-position-path timing sensitivity, not corrected executable strategy or native performance')
Path('attribution1120/verification.json').write_text(json.dumps(out,indent=2)+'\n');print(json.dumps(out))
