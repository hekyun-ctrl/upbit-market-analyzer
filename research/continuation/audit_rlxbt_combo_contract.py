"""Public-source contract audit, not an engine run or market backtest.

Evaluate only published predicates on synthetic feature labels. Never invent the
missing feature definitions or convert synthetic case counts into market rates.
"""
import argparse
import ast
import csv
from datetime import datetime, timezone
from decimal import Decimal
import hashlib
import itertools
import json
from pathlib import Path
import re
import subprocess
import urllib.request

ARTICLE_URL='https://rlxbt.com/api/articles/btc-4-leg-combo-strategy-sharpe-102-294-return-under-005-commissions'
SOURCE_COMMIT='b4f72a3e65472417ae09e7d7b438523ee6b61e0e'
PROTOCOL='rlxbt_public_combo_contract_v1'
FEATURES=('vol_regime_persistence_24','weekend_vol_shock','is_monday','is_thursday')


def digest(raw):return hashlib.sha256(raw).hexdigest()
def canonical(value):return json.dumps(value,sort_keys=True,separators=(',',':'),ensure_ascii=False).encode()


def condition(expression, values):
    tree=ast.parse(expression.replace('&&',' and '),mode='eval')
    def visit(node):
        if isinstance(node,ast.Expression):return visit(node.body)
        if isinstance(node,ast.Name):
            if node.id not in FEATURES or node.id not in values:raise ValueError('unknown/missing original feature')
            return Decimal(str(values[node.id]))
        if isinstance(node,ast.Constant) and type(node.value) in (int,float):return Decimal(str(node.value))
        if isinstance(node,ast.UnaryOp) and isinstance(node.op,ast.USub):return -visit(node.operand)
        if isinstance(node,ast.BoolOp) and isinstance(node.op,ast.And):return all(visit(v) for v in node.values)
        if isinstance(node,ast.Compare) and len(node.ops)==1:
            left,right=visit(node.left),visit(node.comparators[0]);op=node.ops[0]
            if isinstance(op,ast.Lt):return left<right
            if isinstance(op,ast.Gt):return left>right
            if isinstance(op,ast.LtE):return left<=right
            if isinstance(op,ast.Eq):return left==right
        raise ValueError('unsupported original expression')
    return bool(visit(tree))


def extract(article):
    block=re.search(r'```json\s*(\{.*?\})\s*```',article['content'],re.S)
    if not block:raise ValueError('original strategy JSON not present')
    strategy=json.loads(block.group(1))
    if len(strategy['entry_rules'])!=4:raise ValueError('source strategy version changed')
    return dict(id=article['id'],title=article['title'],published_at=article['publishedAt'],
        strategy=strategy,author_claims=article.get('backtestResults'),
        researchManifest=article.get('researchManifest'),full_article_prose_redistributed=False)


def truth_table(strategy):
    rows=[]
    # One below/equal/above representative for each published numerical boundary.
    for vol,shock,weekday in itertools.product(
            ['-0.2','-0.0988','0','0.0412','0.1','0.3410','0.5'],[-1,0,1],range(7)):
        values=dict(vol_regime_persistence_24=vol,weekend_vol_shock=shock,
                    is_monday=int(weekday==0),is_thursday=int(weekday==3))
        active=[r for r in strategy['entry_rules'] if condition(r['condition'],values)]
        exits=[r for r in strategy['exit_rules'] if condition(r['condition'],values)]
        directions=[r['direction'] for r in active]
        rows.append(dict(features=values,weekday_monday_zero=weekday,
            signals=[r.get('signal') for r in active],directions=directions,
            opposite_entries=len(set(directions))>1,
            duplicate_same_direction=len(directions)>len(set(directions)),
            entry_and_exit=bool(active and exits),exit_rules=[r['reason'] for r in exits]))
    return dict(rows=rows,cases=len(rows),opposite_entry_cases=sum(r['opposite_entries'] for r in rows),
        same_direction_multiple_signal_cases=sum(r['duplicate_same_direction'] for r in rows),
        simultaneous_entry_exit_cases=sum(r['entry_and_exit'] for r in rows),
        market_frequency_estimated=False,engine_priority_reproduced=False)


def cash(direction, exit_fraction, multiple=1):
    d=Decimal(direction);m=Decimal(multiple);entry=Decimal('100')
    fee=Decimal('.0006')*m;slip=Decimal('.0002')*m;funding=Decimal('.0001')*m
    entry_fill=entry*(1+d*slip);exit_reference=entry*(1+d*Decimal(str(exit_fraction)))
    exit_fill=exit_reference*(1-d*slip)
    gross=d*(exit_fill-entry_fill);commission=fee*(entry_fill+exit_fill);reserve=funding*entry_fill
    return dict(reference_notional=str(entry),entry_fill=str(entry_fill),exit_fill=str(exit_fill),
        gross_after_slippage=str(gross),commission=str(commission),funding_reserve=str(reserve),
        net=str(gross-commission-reserve),cost_multiple=multiple,
        hypothetical_unit_cashflow_only=True,actual_lbank_cost_verified=False)


def source_inventory(repo):
    commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=repo,text=True).strip()
    if commit!=SOURCE_COMMIT:raise ValueError('source repository not pinned to reviewed commit')
    entries=subprocess.check_output(['git','ls-tree','-rz','HEAD'],cwd=repo).split(b'\0')
    manifest=[];definitions=[];headers=[];gitlinks=[]
    for entry in filter(None,entries):
        metadata,path=entry.split(b'\t',1)
        mode,kind,oid=metadata.decode().split();name=path.decode()
        if kind!='blob':
            gitlinks.append(dict(path=name,mode=mode,type=kind,object_id=oid,content_examined=False))
            continue
        # Read the tracked Git object, never follow working-tree symlinks.
        raw=subprocess.check_output(['git','cat-file','blob',oid],cwd=repo)
        manifest.append(dict(path=name,mode=mode,object_id=oid,bytes=len(raw),sha256=digest(raw)))
        text=raw.decode('utf-8',errors='replace')
        if any(feature in text for feature in FEATURES[:2]):definitions.append(name)
        if name.endswith('.csv'):
            headers.append(dict(path=name,columns=next(csv.reader(text.splitlines()),[])))
    unit=(repo/'studies/002-silent-failures/probe.py').read_text()
    return dict(commit=commit,files=manifest,file_count=len(manifest),unfetched_gitlinks=gitlinks,
        tracked_entry_count=len(manifest)+len(gitlinks),feature_name_occurrence_files=definitions,
        csv_headers=headers,public_probe_fraction_unit_documented='band = 0.01           # 1% each side' in unit,
        public_probe_is_article_runtime_binary=False,
        combo_private_marker_guard_present="'combo_v1'" in (repo/'scripts/leak-guard.sh').read_text())


def audit(source, inventory):
    strategy=source['strategy'];table=truth_table(strategy);costs=[]
    for direction in (1,-1):
        for multiple in (1,2):
            tp=cash(direction,strategy['take_profit_pct'],multiple)
            sl=cash(direction,-Decimal(str(strategy['stop_loss_pct'])),multiple)
            costs.append(dict(direction=direction,tp=tp,sl=sl,
                net_reward_to_initial_net_risk=str(Decimal(tp['net'])/-Decimal(sl['net']))))
    return dict(protocol=PROTOCOL,canonical_source_sha256=digest(canonical(source)),
        source_commit=SOURCE_COMMIT,truth_table=table,unit_cashflow=costs,
        units=dict(public_probe_means_fraction=True,source_stop_fraction=str(strategy['stop_loss_pct']),
            source_target_fraction=str(strategy['take_profit_pct']),article_engine_version_pinned=False),
        source_inventory=inventory,feature_formulas_recovered=False,
        source_market_data_recovered=False,closed_ledger_recovered=False,
        source_market_backtest_performed=False,engine_or_daemon_executed=False,
        market_pf=None,net_expectancy_95_lower=None,tp1_wilson_lower=None,
        five_minute_urgent_rate=None,initial_adverse_excursion=None,manual_fills=None,
        native_funding_and_double_actual_costs=None,drawdown_R=None,eligible=False,forward_started=False,
        decision='exclude_this_public_release_as_replacement_evidence_missing_features_data_ledger_execution_contract',
        interpretation='Synthetic predicate cases and hypothetical cashflow; not market occurrence rates, strategy PnL, or an engine defect finding.')


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--source-repo',type=Path,required=True)
    ap.add_argument('--output-dir',type=Path,required=True);ap.add_argument('--source-extract',type=Path)
    args=ap.parse_args();args.output_dir.mkdir(parents=True,exist_ok=True)
    start=datetime.now(timezone.utc).isoformat()
    retrieval=None
    if args.source_extract:
        source=json.loads(args.source_extract.read_text())
    else:
        req=urllib.request.Request(ARTICLE_URL,headers={'User-Agent':'public-source-contract-audit/1.0'})
        with urllib.request.urlopen(req,timeout=25) as response:
            raw=response.read(1024*1024);status=response.status
        source=extract(json.loads(raw));retrieval=dict(url=ARTICLE_URL,status=status,bytes=len(raw),
            raw_sha256=digest(raw),canonical_sha256=digest(canonical(source)),
            raw_contains_dynamic_view_counters=True,stable_recovery_compare='canonical factual strategy extract only')
    result=audit(source,source_inventory(args.source_repo))
    result.update(started_utc=start,completed_utc=datetime.now(timezone.utc).isoformat(),
                  retrieval=retrieval,implementation_sha256=digest(Path(__file__).read_bytes()))
    for filename,value in [('source_extract.json',source),('report.json',result)]:
        destination=args.output_dir/filename
        if destination.exists():raise FileExistsError(destination)
        destination.write_text(json.dumps(value,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({k:result[k] for k in ('canonical_source_sha256','decision','source_market_backtest_performed')},indent=2))
    print(json.dumps({k:v for k,v in result['truth_table'].items() if k!='rows'},indent=2))


if __name__=='__main__':main()
