"""Build account-scoped proposals from persisted decisions and direct token books.

Limits use fee-inclusive cost. Baskets keep equal shares and one command key;
actual legs may still fill unevenly and their results remain individually visible.
"""
import datetime as dt
import os
import time
import uuid
import requests
import re
from decimal import Decimal, ROUND_DOWN, ROUND_UP
from common import rest, rest_all, rpc, log_run
from paper_execution import instant, number, simulate


def command_key(account, signal):
    payload = signal.get('payload') or {}
    group = payload.get('basket_group')
    identity = f"{payload.get('cycle_id')}:{group}:{signal['side']}" if group and payload.get('cycle_id') else str(signal['signal_id'])
    return str(uuid.uuid5(uuid.NAMESPACE_URL, f"arbdesk:paper:{account['account_id']}:{identity}"))


def prepare(account, signal, capture, *, now=None):
    fixed_now = now
    now = now or dt.datetime.now(dt.timezone.utc)
    policy, payload = account['policy'], signal.get('payload') or {}
    inputs = payload.get('decision_inputs') or {}
    if not 0 <= (now-instant(payload['decision_at'])).total_seconds() <= 900:
        raise ValueError('Decision is stale or future dated')
    ids = payload.get('band_ids') or [signal['band_id']]
    if not ids or len(ids)!=len(set(ids)) or len(ids)>50:
        raise ValueError('Invalid basket membership')
    side = signal['side']
    if side not in ('YES','NO'):
        raise ValueError('Contract side required')
    quoted, expected, contexts = [], Decimal(0), []
    for bid in ids:
        view = inputs.get(str(bid)) or {}
        source = view.get('decision_evidence') or {}
        band, market, forecast = source.get('band') or {}, source.get('market') or {}, source.get('forecast') or {}
        if str(band.get('band_id'))!=str(bid) or market.get('unit') not in ('C','F') or view.get('unit')!=market.get('unit'):
            raise ValueError('Contract identity or temperature unit unverified')
        label_units=set(re.findall(r'°\s*([CF])\b',band.get('band_label',''),re.I))
        if {u.upper() for u in label_units}!={market['unit']}:
            raise ValueError('Contract label and market temperature unit disagree or are unverified')
        if not band.get('open_low') and not band.get('open_high') and number(band['band_hi'])<=number(band['band_lo']):
            raise ValueError('Invalid contract bounds')
        contexts.append((market.get('city_key'),market.get('resolution_date'),market.get('unit')))
        if market.get('city_key') not in policy.get('cities',[]) and 'ALL' not in policy.get('cities',[]):
            raise ValueError('City outside account policy')
        if forecast.get('for_date')!=market.get('resolution_date') or not 0 <= (now-instant(forecast['run_at'])).total_seconds()<=86400:
            raise ValueError('Matching forecast unavailable, stale or future dated')
        p = number(view['model_prob_yes'])
        if not 0 <= p <= 1:
            raise ValueError('Invalid model probability')
        expected += p if side=='YES' else 1-p
        token = band.get('token_yes' if side=='YES' else 'token_no')
        if not token:
            raise ValueError('Direct token identity missing')
        book = capture({'band_id':bid,'token_id':token,'side':side})
        price = min(number(x['price']) for x in book['asks'])
        rate = number(book['fee_rate'])
        quoted.append((bid,token,price,rate,book))
    if len(set(contexts))!=1:
        raise ValueError('Basket spans incompatible city, date or unit')
    unit_cost = sum((price+rate*price*(1-price) for _,_,price,rate,_ in quoted),Decimal(0))
    net_edge = expected-unit_cost
    if net_edge < number(policy['min_edge']):
        raise ValueError('Fresh fee-inclusive basket edge is below policy')
    # Reserve rounded per-leg fees plus a cent of rounding headroom per leg.
    budget = min(number(policy['max_plan_usd']),number(account['cash'])-number(account['reserved_cash']))
    quantity = ((budget-Decimal('.01')*len(quoted))/unit_cost).quantize(Decimal('.01'),rounding=ROUND_DOWN)
    if quantity<=0:
        raise ValueError('Insufficient available paper cash')
    legs, quotes = [], []
    for bid,token,price,rate,book in quoted:
        order={'action':'BUY','token_id':token,'shares':str(quantity),'limit_price':str(price),'share_step':'.01',
            'max_book_age_seconds':120,'expires_at':(now+dt.timedelta(minutes=5)).isoformat()}
        preview = simulate(order,book,now=fixed_now or dt.datetime.now(dt.timezone.utc))
        if preview['status']!='filled':
            raise ValueError('Full requested plan is not executable: '+str(preview['reason']))
        ceiling=(number(preview['notional'])+number(preview['fee'])).quantize(Decimal('.01'),rounding=ROUND_UP)
        legs.append({'band_id':bid,'side':side,'shares':str(quantity),'limit_price':str(price),'cash_ceiling':str(ceiling)})
        quotes.append({'snapshot_id':book['snapshot_id'],'preview':preview,'venue_metadata':book.get('market_metadata')})
    return legs, {'signal':signal,'quotes':quotes,'net_edge_per_share':str(net_edge),'expected_payout_per_basket':str(expected),
        'quoted_cost_per_basket':str(unit_cost),'prepared_at':now.isoformat(),
        'execution_assumption':'Independent IOC legs; no guaranteed basket completion',
        'engine_version':os.environ.get('GITHUB_SHA') or os.environ.get('ARBDESK_ENGINE_VERSION','unversioned')}


def cycle(max_plans=10, budget_seconds=90):
    from paper_worker import capture_book
    now, started, published = dt.datetime.now(dt.timezone.utc), time.monotonic(), 0
    accounts = rest_all('paper_accounts',{'mode':'in.(assisted,automatic)'},order='account_id')
    if not accounts:
        return {'proposals':0}
    signals = rest_all('signals',[('action','eq.ENTER'),('fired_at','gte.'+(now-dt.timedelta(minutes=15)).isoformat()),
        ('fired_at','lte.'+now.isoformat())],order='fired_at.desc,signal_id')
    enabled = {s['strategy_id'] for s in rest('strategies',{'enabled':'eq.true','select':'strategy_id'})}
    seen = set()
    def capture(order):
        if time.monotonic()-started>budget_seconds:
            raise ValueError('Proposal cycle budget reached; await the next fresh decision')
        return capture_book(order)
    for account in accounts:
        for signal in signals:
            if time.monotonic()-started>budget_seconds or published>=max_plans:
                return {'proposals':published}
            if signal['strategy_id'] not in enabled or signal['strategy_id'] not in account['policy'].get('strategies',[]):
                continue
            command = command_key(account,signal)
            if command in seen:
                continue
            seen.add(command)
            if rest('paper_trade_plans',{'account_id':'eq.'+account['account_id'],'command_key':'eq.'+command,'select':'plan_id','limit':'1'}):
                continue
            reason, legs, evidence = None, [], {'signal':signal}
            try:
                legs,evidence=prepare(account,signal,capture)
            except (ValueError,KeyError,TypeError,ArithmeticError,requests.RequestException) as exc:
                reason=str(exc)[:400]
            rpc('publish_paper_plan',{'p_account':account['account_id'],'p_command':command,'p_signal':signal['signal_id'],
                'p_legs':legs,'p_evidence':evidence,'p_block_reason':reason})
            published+=1
    log_run('paper_plans','ok',published,{'proposals':published})
    return {'proposals':published}


if __name__=='__main__':
    print(cycle())
