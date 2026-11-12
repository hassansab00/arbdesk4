"""Bounded original-call YES/NO observation and explicitly activated paper v2.

Run within the existing Tick, not a new schedule. Never prints private packets,
account balances, coefficients, orders or fills into public Actions logs.
"""
import datetime as dt
import time
import uuid
from concurrent.futures import ThreadPoolExecutor, wait
from collections import Counter

from common import rpc, log_run, request_deadline
from paper_worker import capture_book, capture_rule_receipt, fill_order, public_json
from original_side_finance import VERSION

BUDGET_S = 60


def observe(row):
    saved = rpc('register_original_side_call', {'p_call': row['checkpoint_id']})
    result = Counter()
    current_rules,rule_error=None,None
    if saved['eligibility']=='eligible':
        try:
            current_rules=capture_rule_receipt(saved['identity'])
        except Exception as exc:
            rule_error='current_rule_receipt_unavailable:'+type(exc).__name__
    for side in ('YES', 'NO'):
        if side in row.get('captured_sides', []) or int(row.get('attempts', {}).get(side, 0)) >= 3:
            continue
        requested = dt.datetime.now(dt.timezone.utc).isoformat()
        attempt = str(uuid.uuid4())
        book, reason = None, None
        try:
            if saved['eligibility'] != 'eligible':
                reason = saved['eligibility']
            else:
                if rule_error:
                    raise ValueError(rule_error)
                identity = saved['identity']
                book = capture_book(dict(band_id=identity['predicted_band_id'],
                    token_id=identity['token_yes' if side == 'YES' else 'token_no'],
                    side=side, private_side=True,original_identity=identity,current_rules_receipt=current_rules), persist=False)
            receipt = rpc('record_original_side_quote', dict(p_call=saved['call_id'], p_side=side,
                p_attempt=attempt, p_requested=requested, p_book=book, p_reason=reason))
            result[side+'_'+receipt['status']] += 1
        except Exception as exc:
            # Retain the failed side independently. A successful YES capture
            # never disguises a missing NO book or prevents its next retry.
            code = str(exc).split('\n')[0][:240]
            rpc('record_original_side_quote', dict(p_call=saved['call_id'], p_side=side,
                p_attempt=attempt, p_requested=requested, p_book=None, p_reason=code))
            result[side+'_failed'] += 1
    return saved, result


def settlement_packet(row):
    """Capture current venue finality and the original dated event rule again."""
    from market_rules import event_rules
    requested=dt.datetime.now(dt.timezone.utc).isoformat()
    identity = row['identity']
    condition = identity['condition_id']
    gamma_rows = public_json('https://gamma-api.polymarket.com/markets',
        {'condition_ids': condition, 'closed': 'true'})
    gamma = next((x for x in gamma_rows if x.get('conditionId') == condition), None)
    if gamma is None:
        return None
    clob = public_json('https://clob.polymarket.com/markets/'+condition, {})
    events = gamma.get('events') or []
    # The event slug was frozen with the original rule receipt, not selected
    # from a later winning band. No matching original slug means no payout.
    slug = identity.get('event_slug')
    if not slug:
        raise ValueError('original_event_slug_unavailable')
    event = public_json('https://gamma-api.polymarket.com/events/slug/'+slug, {})
    rules = event_rules(event, slug)
    gamma={k:gamma[k] for k in ('conditionId','closed','umaResolutionStatus','clobTokenIds','outcomes','outcomePrices','voided') if k in gamma}
    clob={k:clob[k] for k in ('condition_id','closed','accepting_orders','tokens') if k in clob}
    extra={}
    if gamma.get('umaResolutionStatus')=='resolved':
        import json
        from decimal import Decimal
        prices=gamma.get('outcomePrices') or []
        prices=json.loads(prices) if isinstance(prices,str) else prices
        if len(prices)==2 and any(Decimal(str(p)) not in (0,1) for p in prices):
            try:
                from original_side_payouts import capture
                extra['ctf_payout']=capture(identity)
            except Exception as exc:extra['ctf_payout_error']=type(exc).__name__
    return dict(gamma=gamma, clob=clob, rules_text=rules, requested_at=requested,**extra,
        response_received_at=dt.datetime.now(dt.timezone.utc).isoformat(),
        gamma_source='https://gamma-api.polymarket.com/markets?condition_ids='+condition+'&closed=true',
        clob_source='https://clob.polymarket.com/markets/'+condition,
        rules_source='https://gamma-api.polymarket.com/events/slug/'+slug)


def settle(row):
    packet=settlement_packet(row)
    if packet is None:return 'venue_not_final'
    receipt=rpc('record_original_side_settlement',{'p_call':row['call_id'],'p_evidence':packet})
    return receipt.get('state', 'already_recorded')


def _bounded_observe(row,deadline):
    with request_deadline(deadline):return observe(row)


def _bounded_settle(rows,deadline):
    with request_deadline(deadline):
        packet=settlement_packet(rows[0]);counts=Counter()
        for row in rows:
            try:
                reason='venue_not_final' if packet is None else rpc('record_original_side_settlement',
                    {'p_call':row['call_id'],'p_evidence':packet}).get('state','already_recorded')
            except Exception as exc:reason=type(exc).__name__
            rpc('record_original_side_stage',dict(p_call=row['call_id'],p_account=None,p_stage='settlement_attempt',p_reason=reason))
            counts['settlement_'+reason]+=1
        return counts


def run(*, budget_s=BUDGET_S):
    started = time.monotonic()
    counts, failures = Counter(), Counter()
    with request_deadline(started+budget_s):
        try:rpc('expire_original_side_allocations')
        except Exception as exc:failures['allocation_expiry_'+type(exc).__name__]+=1
        try:runtime = rpc('original_side_runtime', {'p_limit': 100})
        except Exception as exc:
            failures['call_read_'+type(exc).__name__]+=1
            runtime={'calls':[],'accounts':[]}  # settlement recovery remains independent
        observation_deadline=min(started+35,started+budget_s-12)
        for row in runtime.get('historical_calls',[]):
            if time.monotonic()+2>=observation_deadline:break
            try:
                rpc('register_original_side_call',{'p_call':row['checkpoint_id']})
                counts['historical_identity_linked']+=1
            except Exception as exc:failures['historical_identity_'+type(exc).__name__]+=1
        pool=ThreadPoolExecutor(max_workers=6)
        futures={pool.submit(_bounded_observe,row,observation_deadline):row for row in runtime.get('calls',[])}
        done,pending=wait(futures,timeout=max(0,observation_deadline-time.monotonic()))
        for future in pending:future.cancel()
        pool.shutdown(wait=False,cancel_futures=True)
        counts['deferred_calls']+=len(pending)
        for future in done:
            row=futures[future]
            try:
                saved, observed = future.result()
                counts.update(observed)
                if saved['eligibility'] != 'eligible':
                    continue
                for account in runtime.get('accounts', []):
                    slot = next((x for x in account['config']['slots'] if x['city_key'] == row['city_key']
                        and x['checkpoint'] == row['checkpoint']), None)
                    if slot is None:
                        continue
                    try:
                        # Commit the dated allocation before any execution
                        # failure; later city slots keep their own reserves.
                        rpc('reserve_original_side_date',dict(p_account=account['account_id'],p_call=saved['call_id']))
                        quote = rpc('original_side_quote_for_order', {'p_call': saved['call_id'], 'p_side': slot['side']})
                        if quote is None:
                            raise ValueError('direct_selected_side_quote_unavailable')
                        queued = rpc('publish_original_side_order', dict(p_account=account['account_id'],
                            p_call=saved['call_id'], p_quote=quote))
                        counts['orders_already_recorded' if queued.get('duplicate') else 'orders_queued'] += 1
                    except Exception as exc:
                        rpc('record_original_side_stage', dict(p_call=saved['call_id'], p_account=account['account_id'],
                            p_stage='execution_blocked', p_reason=str(exc).split('\n')[0][:300]))
                        counts['execution_blocked'] += 1
            except Exception as exc:
                failures[type(exc).__name__] += 1
        for _ in range(4):
            if time.monotonic()+5 >= started+budget_s:
                break
            order = rpc('claim_original_side_order')
            if not order:
                break
            try:
                result = fill_order(order)
                counts['paper_'+result['status']] += 1
            except Exception as exc:
                failures['fill_completion_'+type(exc).__name__]+=1
                rpc('record_original_side_stage',dict(p_call=order['call_id'],p_account=order['account_id'],
                    p_stage='fill_completion_deferred',p_reason=type(exc).__name__))
        if time.monotonic()+5 < started+budget_s:
            groups={}
            for row in rpc('original_side_settlement_candidates', {'p_limit': 50}):
                i=row['identity'];groups.setdefault((i['condition_id'],i.get('event_slug'),i['rules_sha256']),[]).append(row)
            pool=ThreadPoolExecutor(max_workers=6)
            futures=[pool.submit(_bounded_settle,rows,started+budget_s-1) for rows in groups.values()]
            done,pending=wait(futures,timeout=max(0,started+budget_s-1-time.monotonic()))
            for future in pending:future.cancel()
            pool.shutdown(wait=False,cancel_futures=True)
            counts['settlement_deferred']+=len(pending)
            for future in done:
                try:counts.update(future.result())
                except Exception as exc:failures[type(exc).__name__]+=1
    detail = dict(version=VERSION, counts=dict(counts), failures=dict(failures),
        seconds=round(time.monotonic()-started, 2), paper_only=True)
    log_run('original_side_paper', 'attention' if failures or counts.get('deferred_calls') else 'ok',
        counts.get('YES_captured', 0)+counts.get('NO_captured', 0), detail)
    return detail


if __name__ == '__main__':
    print(run())
