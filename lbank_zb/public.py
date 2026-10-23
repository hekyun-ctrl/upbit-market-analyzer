"""Bounded, keyless GETs to the three documented LBank contract endpoints.

No account access, spot candles, invented historical endpoints or silent fee
zeros. Current funding is a forecast; it cannot certify settled funding PnL.
"""
import json
import time
import urllib.parse
import urllib.request
from .core import D, dec, Costs, planning_funding, asdict
from .live import PRODUCT

BASE = 'https://lbkperp.lbank.com/cfd/openApi/v1/pub/'
DOC = 'https://www.lbank.com/docs/contract.html'
FEE_SOURCE = 'https://www.lbank.com/support/articles/21425760291097'


class PublicClient:
    def __init__(self, opener=None, clock=time.time):
        self.opener = opener or urllib.request.urlopen
        self.clock = clock

    def get(self, name, parameters):
        if name not in ('instrument', 'marketData', 'marketOrder'):
            raise ValueError('UNDOCUMENTED_ENDPOINT')
        url = BASE + name + '?' + urllib.parse.urlencode(parameters)
        request = urllib.request.Request(url, headers={'User-Agent': 'LBankZBPublic/1.0', 'Accept': 'application/json'})
        with self.opener(request, timeout=10) as response:
            raw = response.read(1500001)
        if len(raw) > 1500000:
            raise ValueError('PUBLIC_RESPONSE_TOO_LARGE')
        data = json.loads(raw, parse_float=str)
        if isinstance(data, dict):
            if data.get('success') is False or data.get('result') is False or data.get('error_code', 0) not in (0, '0'):
                raise ValueError('PUBLIC_API_ERROR')
            data = data.get('data', data)
        return data, {'url': url, 'received': int(self.clock()), 'raw': json.loads(raw)}

    @staticmethod
    def select(rows):
        if not isinstance(rows, list):
            raise ValueError('PUBLIC_LIST')
        selected = [r for r in rows if r.get('symbol') == 'BTCUSDT']
        if len(selected) != 1:
            raise ValueError('PRODUCT_AMBIGUOUS')
        return selected[0]

    def evidence(self):
        instruments, instrument_record = self.get('instrument', {'productGroup': 'SwapU'})
        markets, market_record = self.get('marketData', {'productGroup': 'SwapU'})
        i, m = self.select(instruments), self.select(markets)
        now = int(self.clock())
        if (i['baseCurrency'], i['priceCurrency'], i['clearCurrency']) != ('BTC', 'USDT', 'USDT'):
            raise ValueError('WRONG_INSTRUMENT')
        if i.get('needSuspend') not in (0, '0') or str(m.get('instrumentStatus')) != '2':
            raise ValueError('INSTRUMENT_NOT_ACTIVE')
        if not 0 <= now - int(m['lastTime']) <= 120:
            raise ValueError('STALE_MARKET')
        multiplier, tick = dec(i['volumeMultiple']), dec(i['priceTick'])
        step = dec(i['volumeTick']) * multiplier
        minimum_quantity = dec(i['minOrderVolume']) * multiplier
        minimum_cost = dec(i['minOrderCost'])
        price = dec(m['lastPrice'])
        if min(multiplier, tick, step, minimum_quantity, minimum_cost, price) <= 0:
            raise ValueError('INSTRUMENT_VALUES')
        next_time = int(m['nextFeeTime']) // 1000
        interval = int(m['positionFeeTime'])
        if not (now < next_time <= now + 86400 and 0 < interval <= 86400):
            raise ValueError('FUNDING_SCHEDULE_UNAVAILABLE')
        schedule = list(range(next_time, now + 6 * 3600 + 1, interval))
        rate = dec(m['fundingRate'])
        if abs(rate) > 1:
            raise ValueError('FUNDING_RATE')
        # API-provided current interval; never a hardcoded 8-hour interval.
        reserve = planning_funding(now, schedule, rate, [], dec(m['markedPrice']))
        costs = Costs(D('.0006'), D('.0006'), D(2), tick, step,
                      max(minimum_cost, minimum_quantity * price), reserve,
                      'ASSUMED', 'public_taker_6bp_forecast_funding_2bp_exit_slip_v1')
        metadata = {'exchange': 'lbank', 'market_type': 'linear_usdt_perpetual',
                    'symbol': 'BTCUSDT', 'settlement_asset': 'USDT', 'price_basis': 'LAST',
                    'volume_unit': 'contracts', 'contract_multiplier': str(multiplier),
                    'source': 'lbank_native_stream_with_public_instrument'}
        # Keep only selected public product records; the full market list is large.
        record = {'instrument': i, 'market': m, 'received': now,
                  'instrument_url': instrument_record['url'], 'market_url': market_record['url'],
                  'contract_documentation': DOC, 'fee_source': FEE_SOURCE,
                  'fee_policy': 'public base taker; personal tier not accessed',
                  'funding_history_days': 0, 'funding_settlements_verified': False,
                  'candle_volume_unit': 'UNVERIFIED',
                  'depth_volume_unit': 'contract_units_from_instrument',
                  'schedule_basis': 'nextFeeTime + current positionFeeTime; future changes possible'}
        return {'costs': asdict(costs), 'metadata': metadata,
                'settlement_schedule': schedule,
                'qualification': {'product': True, 'fees': True, 'funding_plan': True,
                                  'independent_5m': False, 'candle_units':False}, 'record': record}

    def quote(self, evidence):
        # Refresh last price together with book; no midpoint relabelled LAST.
        markets, market_record = self.get('marketData', {'productGroup': 'SwapU'})
        market = self.select(markets)
        book, record = self.get('marketOrder', {'symbol': 'BTCUSDT', 'depth': 5})
        now = int(self.clock())
        if not 0 <= now - int(market['lastTime']) <= 5:
            raise ValueError('STALE_LAST_PRICE')
        if now - market_record['received'] > 5 or now - record['received'] > 5:
            raise ValueError('STALE_PUBLIC_BOOK')
        if book.get('symbol') != 'BTCUSDT':
            raise ValueError('WRONG_BOOK')
        return {'product': PRODUCT, 'received': record['received'],
                'last': market['lastPrice'], 'book': book,
                'volume_unit': 'contracts',
                'contract_multiplier': evidence['metadata']['contract_multiplier'],
                'source': record['url']}
