"""Pinned original-release ledger availability and execution-contract audit.

Only explicitly reviewed AST definitions are executed; no source package import,
optimizer, data loader, live entrypoint, network, or original market backtest.
Fixtures are synthetic and are never counted as market trades or performance.
"""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import itertools
import json
import subprocess
from dataclasses import dataclass, field
from datetime import datetime, timezone
from decimal import Decimal as D
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

PIN = '5ed0d6e39d8a3e951bea40c66e2638c213012e1b'
DEFINITIONS = {
    'shared/backtest_engine.py': [
        'BacktestResult', 'WalkForwardResult', '_entry_price', '_exit_price',
        '_trade_pnl', '_make_param_grid', '_calc_sharpe', '_calc_sharpe_for_freq',
        'run_trades', 'trades_to_equity', 'WalkForwardEngine'],
    'shared/metrics.py': ['calc_metrics', '_empty_metrics'],
    'strategy_1_funding_mr/signals.py': ['generate_signals'],
}


def sha(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def reviewed_namespace(repo):
    repo = Path(repo)
    head = subprocess.check_output(['git', '-C', str(repo), 'rev-parse', 'HEAD'], text=True).strip()
    if head != PIN:
        raise ValueError('wrong source commit')
    ns = dict(np=np, pd=pd, dataclass=dataclass, field=field, Any=Any,
              Callable=Callable, itertools=itertools, COMMISSION=.0004,
              SLIPPAGE=.0001, _PERIODS_PER_YEAR={'15m':252*24*4},
              tqdm=lambda x, **kwargs:x, __name__=__name__)
    evidence = []
    for filename, names in DEFINITIONS.items():
        path = repo / filename
        tree = ast.parse(path.read_text())
        selected = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.ClassDef)) and n.name in names]
        if {n.name for n in selected} != set(names):
            raise ValueError('missing reviewed definition')
        for n in selected:
            evidence.append(dict(path=filename, name=n.name, line=n.lineno,
                ast_sha256=hashlib.sha256(ast.dump(n).encode()).hexdigest()))
        exec(compile(ast.Module(body=selected, type_ignores=[]), str(path), 'exec'), ns)
    return ns, evidence


def frame(values=(100,100,100,100,100,100), times=None):
    ix = pd.date_range('2024-01-01', periods=len(values), freq='15min', tz='UTC') if times is None else pd.to_datetime(times, utc=True, format='mixed')
    return pd.DataFrame({'open':values,'high':values,'low':values,'close':values,
        'funding_rate':.001, 'mark_price':100.0}, index=ix)


def fixture_results(repo):
    ns, defs = reviewed_namespace(repo)
    run = ns['run_trades']
    def trade(f, **kwargs):
        return run(f, pd.Series([1]+[0]*(len(f)-1), index=f.index), **kwargs).iloc[0]
    flat = frame()
    pos = trade(flat, hold_bars=2)
    carry_frame = frame(tuple([100.]*40))
    carry = trade(carry_frame, hold_bars=32)
    negative = carry_frame.copy(); negative['funding_rate'] = -.001
    negative_result = trade(negative, hold_bars=32)
    end = trade(frame((100,100,110,120)), hold_bars=16)
    gap = trade(frame((100,100,120), times=['2024-01-01','2024-01-01T00:15:00Z','2024-01-03']), hold_bars=1)
    stop_frame = frame((100,100,90,90,90,90))
    stop = trade(stop_frame, hold_bars=3, sl_pct=.02)
    target_frame = frame(); target_frame.iloc[1, target_frame.columns.get_loc('high')] = 110
    target = trade(target_frame, hold_bars=3, tp_pct=.02)
    both_frame = target_frame.copy(); both_frame.iloc[1, both_frame.columns.get_loc('low')] = 90
    both = trade(both_frame, hold_bars=3, tp_pct=.02, sl_pct=.02)
    e = float(target.entry_price)
    # Unit quantity, fee on actual entry+exit notionals, adverse exit slip.
    corrected_target_exit = D(str(e))*D('1.02')*D('.9999')
    corrected_target_net = (corrected_target_exit-D(str(e))-D('.0004')*(D(str(e))+corrected_target_exit))/D(str(e))
    source_pnl = ns['_trade_pnl'](100.,110.,1)
    cash_net = (D('10')-D('.0004')*D('210'))/D('100')
    source_metrics = ns['calc_metrics'](pd.Series([0,source_pnl], index=flat.index[:2]), freq='15m')
    # Constructor cost settings are stored but never passed to global run_trades.
    daily = frame(tuple([100.]*274), pd.date_range('2023-01-01', periods=274, freq='D', tz='UTC'))
    def once(f, params):
        s = pd.Series(0, index=f.index); s.iloc[1]=1; return s
    default = ns['WalkForwardEngine'](verbose=False, min_trades=1).run(daily, once, {'hold_bars':[2]})
    changed = ns['WalkForwardEngine'](verbose=False,min_trades=1,commission=.01,slippage=.02).run(daily,once,{'hold_bars':[2]})
    feature = pd.DataFrame({'close':np.linspace(120,80,160), 'funding_rate':-.001,
        'buy_ratio':.6},index=pd.date_range('2024-01-01',periods=160,freq='15min',tz='UTC'))
    old = ns['generate_signals'](feature.iloc[:100],{})
    full = ns['generate_signals'](feature,{})
    results = dict(
        funding_rate_sign_changed_pnl=dict(positive=float(carry.pnl_pct),negative=float(negative_result.pnl_pct),
            entry=str(carry.entry_time),exit=str(carry.exit_time),synthetic_settlement='2024-01-01T08:00:00Z',
            equal=bool(carry.pnl_pct==negative_result.pnl_pct),note='synthetic fixed signals cross08:00 settlement; no funding cashflow used'),
        end_truncation=dict(requested_bars=16,returned_bars=int(end.hold_actual),
            reason=str(end.exit_reason),pnl_fraction=float(end.pnl_pct)),
        gap_index_time=dict(returned_bars=int(gap.hold_actual),elapsed_seconds=(gap.exit_time-gap.entry_time).total_seconds(),
            nominal_bar_seconds=900,reason=str(gap.exit_reason)),
        stop_gap=dict(open_at_gap=90.,stop_fill=float(stop.exit_price),reason=str(stop.exit_reason)),
        target_exit_slippage=dict(source_exit=float(target.exit_price),source_target=e*1.02,
            corrected_adverse_exit=str(corrected_target_exit),source_net_fraction=float(target.pnl_pct),
            actual_notionals_fee_and_slip_net=str(corrected_target_net)),
        fee_notionals=dict(source_net_fraction=source_pnl,actual_notionals_net=str(cash_net)),
        cost_override=dict(default_pnls=default.oos_trades.pnl_pct.tolist(),
            changed_pnls=changed.oos_trades.pnl_pct.tolist(),identical=bool(default.oos_trades.equals(changed.oos_trades))),
        percent_units=dict(source_total_return_pct_label=source_metrics['total_return_pct'],
            economic_noncompounded_percent=source_pnl*100),
        both_touch=dict(exit_reason=str(both.exit_reason)),
        safe_controls=dict(next_open_entry_time=str(pos.entry_time),expected=str(flat.index[1]),
            funding_mr_past_signals_changed=int((old!=full.iloc[:100]).sum()),
            past_signal_count=int((old!=0).sum()),
            note='signals are considered available only after current bar close; publication latency not established'))
    return results, defs


def audit(repo, output):
    repo, out = Path(repo), Path(output); out.mkdir(parents=True, exist_ok=True)
    start = datetime.now(timezone.utc).isoformat()
    results, defs = fixture_results(repo)
    tree = []
    listing = subprocess.check_output(['git','-C',str(repo),'ls-tree','-r','HEAD'],text=True)
    for line in listing.splitlines():
        meta, name = line.split('\t',1)
        tree.append(dict(path=name,git_blob_sha=meta.split()[2],sha256=sha(repo/name),size=(repo/name).stat().st_size))
    summaries = []
    for family in (1,2,3):
        path = next(repo.glob(f'strategy_{family}_*/results/metrics_per_symbol.csv'))
        with path.open() as f:
            rows = list(csv.DictReader(f))
        btc = next(r for r in rows if r['symbol']=='BTCUSDT')
        n = int(btc['n_trades']); wins = int((D(btc['win_rate'])*n).to_integral_value())
        gross = D(btc['avg_win_pct'])*wins
        loss = -D(btc['avg_loss_pct'])*(n-wins)
        original_copy = out/f'source_strategy_{family}_metrics.csv'
        original_copy.write_bytes(path.read_bytes())
        summaries.append(dict(family=family,path=str(path.relative_to(repo)),source_row=btc,
            source_csv_rows=len(rows),closed_trades_claimed=n,
            rounded_summary_implied_wins=wins,rounded_summary_net_fraction=str(gross-loss),
            rounded_summary_pf=str(gross/loss),reported_total_raw_fraction=btc['total_return_pct'],
            unit_consistent_noncompounded_percent=str(D(btc['total_return_pct'])*100),
            actual_trade_ledger_reconciled=False,actual_frequency_per_day=None))
    report = dict(protocol='gotodataru_source_contract_v1',source_repository='Gotodataru/binance-futures-backtest',
        source_commit=PIN,started_utc=start,completed_utc=datetime.now(timezone.utc).isoformat(),
        script_sha256=sha(__file__),protocol_sha256=sha(Path(__file__).with_name('gotodataru_source_protocol_20261020.json')),
        source_tree=tree,reviewed_definitions=defs,fixtures=results,
        public_raw_parquet_paths=[r['path'] for r in tree if r['path'].endswith('.parquet')],
        public_csv_paths=[r['path'] for r in tree if r['path'].endswith('.csv')],
        public_trade_ledger_paths=[],public_original_equity_csv_paths=[],
        btc_source_summary_arithmetic=summaries,
        decision='exclude_this_release_as_replacement_evidence_not_all_funding_or_flow_strategies',
        market_profitability_backtest_performed=False,source_claims_reproduced=False,
        measured_source_profit_bias=None,market_trades_executed=0,
        net_expectancy_95_lower=None,fullcost_double_profit=None,drawdown_R=None,tp1_wilson_lower=None,
        urgent_warning_rate=None,opportunity_days=None,native_execution_validated=False,
        rule_frozen_forward_start=None,replacement_certified=False,production_changed=False,
        limitations=['Summary rows are author outputs, not original closed trades.',
            'Unit correction is conditional on shown source path; no matching data/params/hash establish provenance.',
            'Positive and negative funding must both be accounted; omission bias is not universally favorable.',
            'Synthetic probes demonstrate contracts only, not market frequency or profitability.',
            'No future-data dependence found in this bounded fixed-parameter completed-bar signal control.',
            'SL/TP diagnostics target shared engine; funding MR default has neither SL nor TP.',
            'Reported PF is scale invariant, not fixed by percentage-label correction.'])
    (out/'report.json').write_text(json.dumps(report,indent=2,allow_nan=False)+'\n')
    print(json.dumps(dict(source_files=len(tree),reviewed_definitions=len(defs),fixtures=results,
        source_BTC_summaries=summaries),indent=2,allow_nan=False))


if __name__=='__main__':
    p=argparse.ArgumentParser();p.add_argument('--repo',required=True);p.add_argument('--output',required=True)
    a=p.parse_args();audit(a.repo,a.output)
