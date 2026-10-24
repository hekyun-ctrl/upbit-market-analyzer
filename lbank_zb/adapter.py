"""Adapters require explicit product and volume provenance; never guess multiplier."""
from .core import *

def normalize_native(raw,received,metadata):
    required=('exchange','market_type','symbol','settlement_asset','price_basis','volume_unit','contract_multiplier','source')
    if any(metadata.get(k) is None for k in required):raise ValueError('PRODUCT_METADATA_UNKNOWN')
    if raw.get('quality')!='stream_closed':raise ValueError('CANDLE_NOT_STREAM_CLOSED')
    volume=dec(raw['volume']);unit=metadata['volume_unit']
    if unit=='contracts':volume*=dec(metadata['contract_multiplier'])
    elif unit!='BTC':raise ValueError('VOLUME_UNIT_UNKNOWN')
    b=Bar(int(raw['start']),int(raw['period']),*[dec(raw[k]) for k in ('open','high','low','close')],volume,int(received),volume_unit='BTC',source=metadata['source'],**{k:metadata[k] for k in ('exchange','market_type','symbol','settlement_asset','price_basis')})
    b.validate(int(received));return b

def depth_fill(book,side,quantity_btc,multiplier,volume_unit):
    if multiplier is None or volume_unit not in ('BTC','contracts'):raise ValueError('DEPTH_VOLUME_UNKNOWN')
    rows=book.get('asks' if side==1 else 'bids')
    if not rows:raise ValueError('MISSING_BOOK')
    bids=[dec(x['price']) for x in book.get('bids',[])];asks=[dec(x['price']) for x in book.get('asks',[])]
    if not bids or not asks or max(bids)>=min(asks):raise ValueError('CROSSED_BOOK')
    levels=sorted([(dec(x['price']),dec(x['volume'])) for x in rows],reverse=side<0);remaining=quantity_btc;cash=ZERO
    if quantity_btc<=0:raise ValueError('QUANTITY')
    for price,qty in levels:
        if min(price,qty)<=0:raise ValueError('BOOK_VALUE')
        if volume_unit=='contracts':qty*=dec(multiplier)
        take=min(remaining,qty);remaining-=take;cash+=take*price
        if remaining==0:return cash/quantity_btc
    raise ValueError('INSUFFICIENT_DEPTH')
