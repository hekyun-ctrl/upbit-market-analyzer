"""Audit a self-published public archive; never a strategy/profit certification.

Raw files remain outside the repository. No personal/account identifiers are
exported. Wallet times are truncated in the source: no intraday equity,
effective leverage, or position episodes are reconstructed. Canceled wallet
transactions are counted for quality control but excluded from cash flows.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import zipfile
from collections import Counter, defaultdict
from decimal import Decimal


def audit(path):
    rows = Counter(); types = Counter(); symbols = Counter(); maker = defaultdict(Counter)
    wallet_types = Counter(); wallet_sums = Counter(); wallet_statuses = Counter()
    duplicate = 0; seen = set(); trade_orders = set(); previous = None; out_of_order = 0
    wallet_previous_date = None; wallet_date_out_of_order = 0
    first = last = None; wallet_first = wallet_last = None; scientific_wallet = 0; empty_wallet = 0
    with zipfile.ZipFile(path) as archive:
        for info in archive.infolist():
            if not info.filename.endswith('.csv'):
                continue
            with archive.open(info) as handle:
                reader = csv.DictReader(io.TextIOWrapper(handle, encoding='utf-8-sig'))
                for row in reader:
                    rows[info.filename] += 1
                    if 'wallet' in info.filename:
                        if not any((value or '').strip() for value in row.values()):
                            empty_wallet += 1
                            continue
                        scientific_wallet += int('e' in row['walletbalance'].lower() or 'e' in row['amount'].lower())
                        wallet_statuses[row['transactstatus']] += 1
                        if row['transactstatus'] != 'Completed':
                            continue
                        kind = row['transacttype']; amount = int(Decimal(row['amount']))
                        wallet_types[kind] += 1; wallet_sums[kind] += amount
                        balance = int(Decimal(row['walletbalance']))
                        if wallet_previous_date is not None and row['date'] < wallet_previous_date:
                            wallet_date_out_of_order += 1
                        wallet_previous_date = row['date']
                        if wallet_first is None: wallet_first = balance - amount
                        wallet_last = balance
                        continue
                    kind, symbol = row['exectype'], row['symbol']
                    types[kind] += 1; symbols[symbol] += 1
                    ts = row['timestamp']; first = ts if first is None else min(first,ts)
                    last = ts if last is None else max(last,ts)
                    if previous is not None and ts < previous: out_of_order += 1
                    previous = ts
                    identity = row['execid']
                    if identity in seen:
                        duplicate += 1; continue
                    seen.add(identity)
                    if kind != 'Trade': continue
                    trade_orders.add(row['orderid'])
                    if symbol != 'XBTUSD': continue
                    size = int(row['lastqty'])
                    if size <= 0 or float(row['lastpx']) <= 0:
                        raise ValueError('invalid XBTUSD trade')
                    year = ts[:4]; maker[year]['total_usd_notional'] += size
                    maker[year]['maker_usd_notional'] += size if row['lastliquidityind']=='AddedLiquidity' else 0
                    maker[year]['trades'] += 1
                    if row['side'] not in ('Buy','Sell'): raise ValueError('invalid side')
    residual = (wallet_first+sum(wallet_sums.values())-wallet_last
                if wallet_first is not None and wallet_last is not None else None)
    with open(path,'rb') as handle:
        archive_sha256=hashlib.file_digest(handle,'sha256').hexdigest()
    return {
        'source_post':'https://gall.dcinside.com/mgallery/board/view/?id=chartanalysis&no=5051684',
        'archive_sha256':archive_sha256,
        'archive_bytes':os.path.getsize(path),
        'self_published_not_independently_authenticated':True,
        'rows_by_file':dict(rows),'execution_types':dict(types),'execution_symbols':dict(symbols),
        'execution_first':first,'execution_last':last,'duplicate_execution_ids':duplicate,
        'out_of_order_rows':out_of_order,'distinct_trade_order_ids':len(trade_orders),
        'xbtusd_by_year':{year:{**dict(values),'maker_notional_share':values['maker_usd_notional']/values['total_usd_notional']}
                           for year,values in sorted(maker.items())},
        'position_episodes_reconstructed':False,
        'wallet_transaction_statuses':dict(wallet_statuses),
        'wallet_completed_row_types':dict(wallet_types),
        'wallet_completed_amount_btc_by_type':{k:v/1e8 for k,v in wallet_sums.items()},
        'wallet_completed_cashflow_reconciliation_satoshis':residual,
        'wallet_completed_cashflow_reconciled':residual==0,
        'wallet_completed_date_out_of_order':wallet_date_out_of_order,
        'wallet_times_truncated':True,'intraday_equity_reconstructed':False,
        'wallet_scientific_notation_rows':scientific_wallet,
        'wallet_blank_rows_excluded':empty_wallet,
        'wallet_balance_may_be_rounded_in_source':bool(scientific_wallet),
        'win_rate_computed':False,'entry_rule_identified':False,
        'caveats':['Individual fills are not independent trades.',
                   'Unsorted execution rows and unknown starting positions prevent naive episode reconstruction.',
                   'Canceled wallet withdrawals are excluded; endpoint reconciliation does not authenticate the archive or reconstruct intraday equity.',
                   'No official trade-ID match or account ownership attestation was performed.',
                   'Historical BitMEX inverse-contract execution is not current LBank linear-contract performance.']}


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('archive');parser.add_argument('output')
    args=parser.parse_args(); result=audit(args.archive)
    with open(args.output,'w') as handle:json.dump(result,handle,ensure_ascii=False,indent=2)
    print(json.dumps(result,ensure_ascii=False,indent=2))
