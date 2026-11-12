"""Bounded paper worker. Public market GETs only; never submits exchange orders."""
import datetime as dt
import hashlib
import json
import os
import re
import time
import requests
from common import rest, rest_all, upsert, rpc, log_run
from paper_execution import simulate, number


def public_json(url, params):
    from common import _get
    response = _get(url, params=params, timeout=20)
    response.raise_for_status()
    return response.json()


def capture_current_rules(identity):
    return capture_rule_receipt(identity)['rules_text']


def capture_rule_receipt(identity):
    from market_rules import event_rules
    slug=identity.get('event_slug') or ''
    if not re.fullmatch('[a-z0-9-]+',slug):
        raise ValueError('original_event_slug_unavailable')
    source='https://gamma-api.polymarket.com/events/slug/'+slug
    rules=event_rules(public_json(source,{}),slug)
    return dict(rules_text=rules,received_at=dt.datetime.now(dt.timezone.utc).isoformat(),source=source)


def capture_book(order, *, persist=True):
    requested = dt.datetime.now(dt.timezone.utc).isoformat()
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
    from original_side_finance import quote_metadata
    execution_metadata = quote_metadata(raw, market)
    payload = {'token_id': order['token_id'], 'observed_at': observed,
        'bids': raw['bids'], 'asks': raw['asks'], 'tick_size': raw['tick_size'],
        **execution_metadata,
        'tradeable': market.get('active') is True and market.get('closed') is False and market.get('acceptingOrders') is True,
        'raw_book': raw, 'market_metadata': market}
    # Fee/market metadata revisions must not replenish unchanged book depth.
    identity = hashlib.sha256(json.dumps({'token':order['token_id'],'book':raw},
        sort_keys=True,separators=(',', ':')).encode()).hexdigest()
    payload['depth_hash'] = identity  # Also the unchanged legacy raw-book ID.
    identity = hashlib.sha256(json.dumps({'depth_hash':identity,'execution_metadata':execution_metadata},
        sort_keys=True,separators=(',', ':')).encode()).hexdigest()
    payload['snapshot_id'] = identity
    if order.get('private_side'):
        original = order.get('original_identity') or {}
        rule_receipt=order.get('current_rules_receipt')
        if rule_receipt is None:
            if order.get('current_rules_text') is not None:
                rule_receipt=dict(rules_text=order['current_rules_text'],received_at=dt.datetime.now(dt.timezone.utc).isoformat())
            else:rule_receipt=capture_rule_receipt(original)
        rules=rule_receipt['rules_text']
        rules_hash = hashlib.sha256(rules.encode()).hexdigest()
        if rules_hash != original.get('rules_sha256'):
            raise ValueError('current_contract_changed_since_original_freeze')
        payload.update(current_rules_text=rules,current_rules_received_at=rule_receipt['received_at'],
            rules_source='https://gamma-api.polymarket.com/events/slug/'+original['event_slug'])
        # Keep the exact fields which establish identity and executable terms;
        # unrelated Gamma event payloads do not belong in every token receipt.
        payload['market_metadata'] = {k: market[k] for k in ('conditionId', 'clobTokenIds',
            'outcomes', 'active', 'closed', 'acceptingOrders', 'feesEnabled', 'feeSchedule',
            'orderMinSize', 'orderPriceMinTickSize') if k in market}
        payload.update(quote_metadata(raw, market), quote_kind='direct',
            requested_at=requested, received_at=dt.datetime.now(dt.timezone.utc).isoformat())
        if persist:
            receipt = rpc('record_original_side_execution_book', {'p_order': order['order_id'], 'p_book': payload})
            payload.update(snapshot_id=receipt['snapshot_id'], received_at=receipt['received_at'],
                capture_attempt_id=receipt['capture_attempt_id'])
        return payload
    upsert('paper_book_evidence', [{'snapshot_id': identity, 'token_id': order['token_id'],
        'observed_at': observed, 'payload': payload}], 'snapshot_id')
    return payload


def consumed_depth(order, snapshot, depth_hash=None):
    if order.get('private_side'):
        return rpc('original_side_consumed_depth', {'p_order':order['order_id'],'p_snapshot':snapshot})
    filters={'account_id':'eq.'+order['account_id'], 'token_id':'eq.'+order['token_id'],
        'action':'eq.'+order['action'], 'select':'result', 'limit':501,'order':'requested_at.desc,order_id.desc'}
    if depth_hash:
        filters['or']=f'(result->>snapshot_id.eq.{snapshot},result->>snapshot_id.eq.{depth_hash},result->>depth_hash.eq.{depth_hash})'
    else:filters['result->>snapshot_id']='eq.'+snapshot
    rows = rest('paper_orders', filters)
    if len(rows)>500:
        raise ValueError('book_consumption_read_truncated')
    used = {}
    for row in rows:
        for fill in (row.get('result') or {}).get('fills', []):
            p = str(number(fill['price']))
            used[p] = str(number(used.get(p, 0)) + number(fill['shares']))
    return used


def fill_order(order):
    """Fill one claimed order against the book captured now, and record it.

    The one way an order is filled: this worker's cycle and the tick's engine
    orders (plan v2 P5.12 part 3b) both call it."""
    try:
        book = capture_book(order)
        result = simulate(order, book, consumed=consumed_depth(order, book['snapshot_id'], book.get('depth_hash')))
        result['depth_hash'] = book.get('depth_hash')
        if order.get('private_side'):
            result['capture_attempt_id'] = book['capture_attempt_id']
        result['venue_metadata'] = book['market_metadata']
        if number(result['notional'])+number(result['fee']) > number(order['cash_ceiling']) and order['action']=='BUY':
            result = {'status':'rejected','reason':'fee_inclusive_cash_ceiling','fills':[], 'shares':'0','notional':'0','fee':'0'}
    except (ValueError, KeyError, TypeError, requests.RequestException) as exc:
        result = {'status':'rejected','reason':str(exc)[:400],'fills':[], 'shares':'0','notional':'0','fee':'0'}
    result['engine_version'] = os.environ.get('GITHUB_SHA') or os.environ.get('ARBDESK_ENGINE_VERSION','unversioned')
    if order.get('private_side'):
        rpc('complete_original_side_order', {'p_order':order['order_id'],'p_lease':order['lease_token'],'p_result':result})
    else:
        rpc('complete_paper_order', {'p_order':order['order_id'],'p_lease':order['lease_token'],'p_result':result})
    return result


def cycle(max_orders=10):
    rpc('expire_paper_commands')
    started, completed = time.monotonic(), 0
    for _ in range(max_orders):
        if time.monotonic()-started > 90:
            break
        order = rpc('claim_paper_order')
        if not order:
            order = rpc('claim_original_side_order')
        if not order:
            break
        fill_order(order)
        completed += 1
    log_run('paper_worker','ok',completed,{'orders_completed':completed,'seconds':round(time.monotonic()-started,2)})
    return {'orders_completed':completed}


if __name__ == '__main__':
    print(cycle())
