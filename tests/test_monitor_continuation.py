import monitor
import pytest
from test_monitor import _flow_telegram_candidate


def _candidate(now):
    candidate = _flow_telegram_candidate(now)
    price = candidate['current_price']
    candidate.update(flow_continuation_entry=True,
        survival_continuation_context={'confirmed': True, 'close': price,
            'low': price * .995, 'volume_5m': 2, 'volume_15m': 1.5})
    for w in candidate['survival_trade_flow']['windows']:
        w.update(close=price, low=price)
    return candidate


def test_telegram_continuation_requires_fresh_structural_proof_and_reports_route(monkeypatch):
    now = 1800000100
    monkeypatch.setattr('monitor.time.time', lambda: now)
    candidate = _candidate(now)
    assert monitor.AlertDispatcher().candidate_delivery_reasons(candidate) == []
    text = monitor._candidate_text(candidate)
    assert '완료봉 상승 지속형' in text and '실제 체결:' in text
    candidate['flow_continuation_entry'] = False
    assert monitor.AlertDispatcher().candidate_delivery_reasons(candidate)


@pytest.mark.parametrize('cause', ['missing', 'false', 'malformed', 'weak_volume',
    'support', 'chase', 'stale', 'selling', 'risk', 'short_wait'])
def test_telegram_cannot_dispatch_stale_or_invalid_continuation(monkeypatch, cause):
    now = 1800000100
    monkeypatch.setattr('monitor.time.time', lambda: now)
    candidate = _candidate(now)
    proof = candidate['survival_continuation_context']
    if cause == 'missing': candidate.pop('survival_continuation_context')
    elif cause == 'false': proof['confirmed'] = False
    elif cause == 'malformed': candidate['survival_continuation_context'] = {'confirmed': True}
    elif cause == 'weak_volume': proof['volume_5m'] = 1.49
    elif cause == 'support': proof['low'] = candidate['current_price'] + 1
    elif cause == 'chase': proof['close'] = candidate['current_price'] / 1.006
    elif cause == 'stale': candidate['survival_trade_flow']['as_of'] -= 11
    elif cause == 'selling':
        for w in candidate['survival_trade_flow']['windows']: w['buy_krw'] = 0
    elif cause == 'risk': candidate['stop_price'] = 95
    elif cause == 'short_wait': candidate['survival_seconds'] = 59
    assert monitor.AlertDispatcher().candidate_delivery_reasons(candidate)
