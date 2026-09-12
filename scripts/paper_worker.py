"""Bounded paper worker. Public market GETs only; never submits exchange orders."""
import datetime as dt
import hashlib
import json
import os
import time
import requests
from common import rest, rest_all, upsert, rpc, log_run
from paper_execution import simulate, number


def public_json(url, params):
    response = requests.get(url, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def capture_book(order):
    raw = public_json('https://clob.polymarket.com/book', {'token_id': order['token_id']})
    bands = rest('bands', {'band_id': 'eq.'+order['band_id'], 'select': 'condition_id'})
    condition = bands[0]['condition_id'] if bands else None
    if not condition:
        raise ValueError('missing_condition_identity')
    markets = public_json('https://gamma-api.polymarket.com/markets', {'condition_ids': condition})
    market = next((m for m in markets if m.get('conditionId') == condition), None)
    if not market:
        raise ValueError('market_identity_unverified')
    tokens = market.get('clobTokenIds') or []
    tokens = json.loads(tokens) if isinstance(tokens, str) else tokens
    outcomes = market.get('outcomes') or []
    outcomes = json.loads(outcomes) if isinstance(outcomes, str) else outcomes
    mapped = {str(label).upper(): str(token) for label, token in zip(outcomes, tokens)}
    if mapped.get(order['side']) != order['token_id'] or str(raw.get('asset_id')) != order['token_id']:
        raise ValueError('token_identity_mismatch')
    fee = market.get('feeSchedule') or {}
    if market.get('feesEnabled') is False:
        rate = 0
    elif market.get('feesEnabled') is True and fee.get('exponent') == 1 and fee.get('rate') is not None:
        rate = fee['rate']
    else:
        raise ValueError('fee_schedule_unverified')
    observed = dt.datetime.fromtimestamp(float(raw['timestamp'])/1000, dt.timezone.utc).isoformat()
    payload = {'token_id': order['token_id'], 'observed_at': observed,
        'bids': raw['bids'], 'asks': raw['asks'], 'tick_size': raw['tick_size'],
        'min_order_size': market['orderMinSize'], 'min_order_size_unit': 'USDC', 'fee_rate': rate,
        'tradeable': market.get('active') is True and market.get('closed') is False and market.get('acceptingOrders') is True,
        'raw_book': raw, 'market_metadata': market}
    # Fee/market metadata revisions must not replenish unchanged book depth.
    identity = hashlib.sha256(json.dumps({'token':order['token_id'],'book':raw},
        sort_keys=True,separators=(',', ':')).encode()).hexdigest()
    payload['snapshot_id'] = identity
    upsert('paper_book_evidence', [{'snapshot_id': identity, 'token_id': order['token_id'],
        'observed_at': observed, 'payload': payload}], 'snapshot_id')
    return payload


def consumed_depth(order, snapshot):
    rows = rest_all('paper_orders', {'account_id':'eq.'+order['account_id'], 'token_id':'eq.'+order['token_id'],
        'action':'eq.'+order['action'], 'result->>snapshot_id':'eq.'+snapshot, 'select':'result'}, order='order_id')
    used = {}
    for row in rows:
        for fill in (row.get('result') or {}).get('fills', []):
            p = str(number(fill['price']))
            used[p] = str(number(used.get(p, 0)) + number(fill['shares']))
    return used


def cycle(max_orders=10):
    rpc('expire_paper_commands')
    started, completed = time.monotonic(), 0
    for _ in range(max_orders):
        if time.monotonic()-started > 90:
            break
        order = rpc('claim_paper_order')
        if not order:
            break
        try:
            book = capture_book(order)
            result = simulate(order, book, consumed=consumed_depth(order, book['snapshot_id']))
            result['venue_metadata'] = book['market_metadata']
            if number(result['notional'])+number(result['fee']) > number(order['cash_ceiling']) and order['action']=='BUY':
                result = {'status':'rejected','reason':'fee_inclusive_cash_ceiling','fills':[], 'shares':'0','notional':'0','fee':'0'}
        except (ValueError, KeyError, TypeError, requests.RequestException) as exc:
            result = {'status':'rejected','reason':str(exc)[:400],'fills':[], 'shares':'0','notional':'0','fee':'0'}
        result['engine_version'] = os.environ.get('GITHUB_SHA') or os.environ.get('ARBDESK_ENGINE_VERSION','unversioned')
        rpc('complete_paper_order', {'p_order':order['order_id'],'p_lease':order['lease_token'],'p_result':result})
        completed += 1
    log_run('paper_worker','ok',completed,{'orders_completed':completed,'seconds':round(time.monotonic()-started,2)})
    return {'orders_completed':completed}


if __name__ == '__main__':
    print(cycle())
