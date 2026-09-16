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


# Every way a venue outcome label states its temperature unit, in the same
# forms P0.2's parser accepts when it INFERS markets.unit from those labels:
# a degree sign, the single-character glyphs, the spelled-out word, or a bare
# C/F immediately after the number ("85-86F", which is the shape the strategy
# fixtures and the live Polymarket labels use).
_LABEL_UNIT = re.compile(r'(?:°\s*([CF])|([℃℉])|\b(celsius|fahrenheit)\b|\d\s*([CF])\b)', re.I)
_GLYPH = {'℃': 'C', '℉': 'F'}


def label_unit_markers(label):
    """The temperature units a contract label states, as a set.

    AN ABSENT UNIT IS NOT A CONTRADICTION, and conflating the two is what this
    exists to prevent. The check used to demand that every band's own label
    carry `°C` or `°F` matching its market, and treated a label that simply
    did not say as a disagreement - the same error as a label that said the
    opposite. Those are different facts and only one of them is dangerous.

    markets.unit is itself derived by P0.2 from the labels of the market as a
    whole, so a single band whose label omits the unit is normal and carries
    no contradiction: the market already answered, from the same source. A
    label reading 20C under a market marked F is the real hazard - it would
    size a trade against a band whose bounds mean something else - and that
    still raises.

    Returning a SET rather than a unit keeps the caller's two questions
    separate: did the label say anything, and if so did it agree.
    """
    out = set()
    for degree, glyph, word, bare in _LABEL_UNIT.findall(label or ''):
        token = degree or glyph or word or bare
        if not token:
            continue
        token = _GLYPH.get(token, token)
        out.add(token[0].upper())
    return out


# The share increment every paper order is quoted in. It lives here rather
# than inline so the depth cap and the order it sizes cannot disagree.
DEFAULT_SHARE_STEP = Decimal('.01')


def basket_depth(quoted, step):
    """Shares available on the THINNEST leg, at its own quoted limit.

    Every leg of a basket takes the same share count, so the basket can only
    be as large as its least liquid leg. The limit on each leg is that leg's
    best ask, which means the depth inside the limit is the size resting at
    the touch - not the whole ladder.
    """
    smallest = None
    for _, _, price, _, book in quoted:
        available = Decimal(0)
        for level in (book.get('asks') or []):
            if number(level['price']) <= price:
                available += number(level['size'])
        available = (available / step).to_integral_value(rounding=ROUND_DOWN) * step
        smallest = available if smallest is None else min(smallest, available)
    return smallest if smallest is not None else Decimal(0)


def venue_minimum_shares(quoted, step):
    """The smallest share count EVERY leg's venue would accept, on the step.

    Polymarket states a minimum per market as `orderMinSize`, quoted in USDC,
    so the share count it implies is different on every leg: a leg at $0.05
    needs twenty times the shares of a leg at $1.00 to clear the same dollar
    floor. A basket buys equal shares on all legs, so the basket's floor is
    the LARGEST of those - the leg that needs the most shares decides.

    Rounded UP to the share step, because a quantity below the step is not an
    order the venue can be sent.
    """
    floor = Decimal(0)
    for _, _, price, _, book in quoted:
        minimum = number(book['min_order_size'])
        needed = minimum if book['min_order_size_unit'] == 'shares' else minimum / price
        needed = (needed / step).to_integral_value(rounding=ROUND_UP) * step
        floor = max(floor, needed)
    return floor


def signal_cities(signal):
    """Every city the signal's own decision covers, or an empty set if it did
    not carry one. Used to drop a proposal before it costs anything, so an
    out-of-policy city cannot spend a run's plan budget."""
    payload = signal.get('payload') or {}
    inputs = payload.get('decision_inputs') or {}
    cities = set()
    for view in inputs.values():
        if not isinstance(view, dict):
            continue
        city = view.get('city_key')
        if not city:
            city = ((view.get('decision_evidence') or {}).get('market') or {}).get('city_key')
        if city:
            cities.add(str(city))
    return cities


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
        label_units=label_unit_markers(band.get('band_label',''))
        if label_units and label_units!={market['unit']}:
            raise ValueError('Contract label and market temperature unit disagree')
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
    # BUY WHAT IS THERE, RATHER THAN REFUSING WHAT IS NOT.
    #
    # The size above is what the BUDGET affords. What the book offers at the
    # limit is a separate quantity and is usually the smaller of the two: the
    # limit is the best ask, so only the touch level is inside it. Sizing off
    # the budget alone and then demanding a complete fill threw the whole plan
    # away whenever the touch was thinner than the budget - which is the
    # ordinary case on a band trading a few hundred dollars. Every proposal
    # the Texas desks produced on 16 Sep died here as
    # "insufficient_depth_within_limit" while the edge itself was sound.
    #
    # A basket fills equal shares on every leg, so the whole plan is bounded
    # by its THINNEST leg. Below the venue minimum there is no order to place
    # and the plan is genuinely refused, which is a different sentence.
    depth = basket_depth(quoted, DEFAULT_SHARE_STEP)
    quantity = min(quantity, depth)
    if quantity<=0:
        raise ValueError('No depth at the quoted price on at least one leg')
    # A SIZE THE VENUE WILL NOT ACCEPT IS NOT A PLAN, AND SAYING SO IN ITS OWN
    # WORDS IS THE DIFFERENCE BETWEEN A FIXABLE BLOCK AND A MYSTERY.
    #
    # simulate() refuses below orderMinSize with the code
    # "quantity_or_price_increment", which also covers a bad tick and a bad
    # share step - three unrelated causes, one string. On 16 Sep every one of
    # the 19 blocked plans carried it, and the plan row records no quotes
    # (prepare raises before building them), so nothing on the desk said
    # whether the touch was thin or the budget was small.
    #
    # Neither can be rescued by resizing. The budget quantity is the largest
    # the cash allows and the depth is the largest the book allows, so if the
    # smaller of the two is under the floor, raising it would break the other
    # constraint. What CAN be fixed is which one to go and change.
    floor = venue_minimum_shares(quoted, DEFAULT_SHARE_STEP)
    if quantity < floor:
        short = 'book depth' if depth < floor else 'account budget'
        raise ValueError(
            f'Below the venue minimum order size: {quantity} shares against a '
            f'{floor}-share floor, limited by {short}')
    legs, quotes = [], []
    for bid,token,price,rate,book in quoted:
        order={'action':'BUY','token_id':token,'shares':str(quantity),'limit_price':str(price),'share_step':str(DEFAULT_SHARE_STEP),
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
    # A RUN THAT STOPS EARLY IS STILL A RUN, and has to say so.
    #
    # log_run sat at the bottom, after the loops, so the two ordinary early
    # exits - no accounts, and the budget or plan cap being reached - returned
    # without writing anything. Hitting the cap is the NORMAL outcome on a busy
    # board: 10 plans is the default and the desk reaches it in seconds.
    #
    # So ingest_log held one paper_plans row in twelve hours while the step ran
    # every cycle and wrote plans each time. The desk page reads that table for
    # its step tiles and showed "Proposals: 0 proposed, 9h ago" beside signals
    # fired twenty minutes earlier - which reads as a dead step, and sent me
    # looking for a stopped pipeline that had never stopped.
    def done(published, note):
        log_run('paper_plans', 'ok', published, {'proposals': published, 'stopped': note})
        return {'proposals': published}

    accounts = rest_all('paper_accounts',{'mode':'in.(assisted,automatic)'},order='account_id')
    if not accounts:
        return done(0, 'no assisted or automatic account')
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
            if published>=max_plans:
                return done(published, f'reached the {max_plans}-plan cap')
            if time.monotonic()-started>budget_seconds:
                return done(published, f'reached the {budget_seconds}s budget')
            if signal['strategy_id'] not in enabled or signal['strategy_id'] not in account['policy'].get('strategies',[]):
                continue
            # A CITY THE DESK CANNOT TRADE MUST NOT COST IT A PLAN SLOT.
            #
            # The signal runner prices every board it can see - 43 cities on
            # 16 Sep - while a desk is scoped to three. prepare() rejects the
            # rest with 'City outside account policy', but only AFTER the plan
            # has been published as blocked and counted against max_plans. The
            # first run to reach this point spent 18 of its 20 plans that way,
            # and the two Texas proposals that survived were the last to be
            # tried rather than the first.
            #
            # The city is already in the decision the signal carries, so this
            # costs no request. A signal with no city recorded still goes
            # through: absence is not grounds to drop a decision silently.
            cities = signal_cities(signal)
            allowed = account['policy'].get('cities') or []
            if cities and 'ALL' not in allowed and not (cities & set(allowed)):
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
    return done(published, 'considered every signal')


if __name__=='__main__':
    print(cycle())
