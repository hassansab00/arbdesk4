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
import risk_rails
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


def plain(value):
    """A Decimal as a person reads it, never in scientific notation.

    Decimal keeps the exponent arithmetic gave it, and an f-string prints it:
    $5 divided by a $0.001 band is Decimal('5E+3'), so the desk told Hassan

        "Reaching the 5E+3-share venue minimum costs 0.4429 per share more
         than the touch"
        "Below the venue minimum order size: 15.39 shares against a
         1E+2-share floor"

    in the one sentence whose entire job is explaining why it would not
    trade. `{d:f}` is fixed-point and leaves 41.67 alone; Decimal.normalize()
    is the opposite of what is wanted here - it turns 100 INTO 1E+2.
    """
    return f"{value:f}" if isinstance(value, Decimal) else str(value)


def ladder_cost(book, shares):
    """What it costs to take `shares` off this leg's ask ladder.

    Returns (limit_price, depth_weighted_price), or None when the whole ladder
    does not hold that many. The limit is the last level the order reaches, so
    an order sent at it fills the whole quantity; the depth-weighted price is
    what those shares actually average, which is the number the edge has to be
    measured against. Top-of-book is neither, and edges.market_price already
    says why in its own comment: "NEVER top-of-book - that produces
    recommendations that cannot be filled."
    """
    taken, spend, limit = Decimal(0), Decimal(0), None
    for level in sorted((book.get('asks') or []), key=lambda x: number(x['price'])):
        price, size = number(level['price']), number(level['size'])
        take = min(size, shares - taken)
        if take <= 0:
            break
        taken, spend, limit = taken + take, spend + take * price, price
    if taken < shares or limit is None:
        return None
    return limit, spend / shares


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


def rail_room(account, quoted, context, rails, halted, halt_reason, *, rest_fn=None):
    """(dollars this plan may still spend on its city-day, refusal or None).

    The same arithmetic queue_plan enforces: equity at cost is cash plus the
    cost of open positions; the city-day holds positions plus live BUY
    reservations on the plan's market; the rail is city_day_frac of equity.
    """
    rest_fn = rest_fn or rest
    if halted:
        return Decimal(0), f"Trading halted ({halt_reason or 'no reason given'})"
    for _bid, _tok, price, _rate, _book in quoted:
        if price > Decimal(str(rails['max_price'])):
            return Decimal(0), f"Rail: {price} is above the {rails['max_price']} price bound"
    acct = account['account_id']
    positions = rest_fn('paper_positions', {'select': 'band_id,cost_basis', 'account_id': f'eq.{acct}',
                                            'shares': 'gt.0'})
    equity = number(account['cash']) + sum((number(p.get('cost_basis') or 0) for p in positions), Decimal(0))
    city, date, _unit = context
    market = rest_fn('markets', {'select': 'market_id', 'city_key': f'eq.{city}',
                                 'resolution_date': f'eq.{date}', 'limit': '1'})
    held = Decimal(0)
    if market:
        mid = market[0]['market_id']
        bands = {b['band_id'] for b in rest_fn('bands', {'select': 'band_id', 'market_id': f'eq.{mid}'})}
        held += sum((number(p.get('cost_basis') or 0) for p in positions if p['band_id'] in bands), Decimal(0))
        live = rest_fn('paper_orders', {'select': 'band_id,cash_ceiling', 'account_id': f'eq.{acct}',
                                        'status': 'in.(queued,working)', 'action': 'eq.BUY'})
        held += sum((number(o.get('cash_ceiling') or 0) for o in live if o['band_id'] in bands), Decimal(0))
    room = Decimal(str(rails['city_day_frac'])) * equity - held
    if room <= 0:
        return Decimal(0), f"Rail: {city} {date} already holds {held} of the {rails['city_day_frac']} city-day limit"
    return room, None


def allocation_room(account, signal, *, rest_fn=None):
    """(dollars this strategy may still spend on the portfolio account, the
    factor its engine size scales by, the record for the plan's evidence).

    THE PORTFOLIO SPLITS ONE POT BY THE NIGHTLY ALLOCATION (plan v2 P5.10).
    meta_allocator.py writes {strategy: weight} into the portfolio policy. A
    strategy may hold at most weight x equity (cash plus open cost basis): its
    open trades on the account at cost, plus its live BUY reservations, count
    against that. queue_plan already refuses a plan whose strategy has no
    weight, since only weighted strategies are in policy.strategies.

    The engine sized the signal on the strategy's shadow ledger, whose bankroll
    the signal records as sized_on_usd. The same Kelly fraction of the
    strategy's allocated capital is that size x capital / sized_on_usd. A
    signal that does not say what it was sized on is refused here rather than
    guessed at: scaling the wrong base mis-sizes by the ratio of the two.
    """
    rest_fn = rest_fn or rest
    policy = account.get('policy') or {}
    sid = signal['strategy_id']
    weight = number((policy.get('allocation') or {}).get(sid) or 0)
    if weight <= 0:
        raise ValueError(f'{sid} has no portfolio allocation')
    sized_on = number((signal.get('payload') or {}).get('sized_on_usd') or 0)
    if sized_on <= 0:
        raise ValueError('The signal does not say what bankroll it was sized on (sized_on_usd missing)')
    acct = account['account_id']
    positions = rest_fn('paper_positions', {'select': 'cost_basis', 'account_id': f'eq.{acct}', 'shares': 'gt.0'})
    equity = number(account['cash']) + sum((number(p.get('cost_basis') or 0) for p in positions), Decimal(0))
    capital = weight * equity
    held = rest_fn('paper_trades', {'select': 'shares,avg_fill_price,fee_paid', 'account_id': f'eq.{acct}',
                                    'strategy_id': f'eq.{sid}', 'closed_at': 'is.null'})
    live = rest_fn('paper_orders', {'select': 'cash_ceiling', 'account_id': f'eq.{acct}', 'strategy_id': f'eq.{sid}',
                                    'status': 'in.(queued,working)', 'action': 'eq.BUY'})
    used = (sum((number(t.get('shares') or 0) * number(t.get('avg_fill_price') or 0) + number(t.get('fee_paid') or 0)
                 for t in held), Decimal(0))
            + sum((number(o.get('cash_ceiling') or 0) for o in live), Decimal(0)))
    room = capital - used
    if room <= 0:
        raise ValueError(f'{sid} already uses {used.quantize(Decimal(".01"))} of its '
                         f'{capital.quantize(Decimal(".01"))} portfolio allocation')
    record = {'weight': str(weight), 'version': policy.get('allocation_version'),
              'capital_usd': str(capital.quantize(Decimal('.01'))), 'used_usd': str(used.quantize(Decimal('.01'))),
              'sized_on_usd': str(sized_on)}
    return room, capital / sized_on, record


def prepare(account, signal, capture, *, now=None, rail_room=None, allocation=None):
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
    # INSIDE THE RAILS, NOT REFUSED BY THEM (plan v2 P5.9). queue_plan refuses
    # a plan that would put more than 3% of the account on one city-day, or
    # buy above 0.97, or run while halted; sizing the plan to fit here means
    # the decision still reaches the ledger at the size the rails allow.
    if rail_room is not None:
        room, why = rail_room(account, quoted, contexts[0])
        if why:
            raise ValueError(why)
        budget = min(budget, room)
    # THE PORTFOLIO'S SHARE FOR THIS STRATEGY (plan v2 P5.10).
    allocated = None
    if allocation is not None and account.get('kind') == 'portfolio':
        room, scale, allocated = allocation(account, signal)
        budget = min(budget, room)
    affordable_q = ((budget-Decimal('.01')*len(quoted))/unit_cost).quantize(Decimal('.01'),rounding=ROUND_DOWN)
    if affordable_q<=0:
        raise ValueError('Insufficient available paper cash')
    # THE ENGINE'S SIZE REACHES THE ORDER (plan v2 P5.0 item 3).
    #
    # signal_engine sizes every ENTER signal - Kelly on the edge, then the
    # ladder allocation and the per-city and gross risk caps - and writes it
    # as suggested_shares. This function used to ignore it and buy whatever
    # min(max_plan_usd, free cash) afforded, so every plan spent the desk's
    # whole plan limit whatever the engine had decided the edge was worth.
    # The engine's size is now a ceiling like the budget and the book, and the
    # smallest of the three is the order. A signal without a size is refused:
    # every ENTER signal written in the 7 days to 24 Sep carried one (2,610 of
    # 2,610), so a missing size is a fault to surface, not a default to fill.
    if signal.get('suggested_shares') is None:
        raise ValueError('The engine gave this signal no size (suggested_shares missing)')
    target = number(signal['suggested_shares'])
    if allocated is not None:
        target = target * scale
    target = target.quantize(Decimal('.01'),rounding=ROUND_DOWN)
    if target<=0:
        raise ValueError('The engine sized this signal to zero shares')
    quantity = min(affordable_q, target)
    limited_by = 'the engine\'s size' if target < affordable_q else 'account budget'
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
    if target < floor:
        # No walk can help: the walk buys exactly the floor, which is more
        # than the engine decided this edge is worth.
        raise ValueError(
            f'Below the venue minimum order size: the engine sized {plain(target)} shares '
            f'against a {plain(floor)}-share floor, limited by the engine\'s size')
    if quantity < floor and depth < floor:
        # THE TOUCH IS NOT THE BOOK, and treating it as one threw away 219 of
        # the desk's 360 plans - 61% - every one of them "limited by book
        # depth" and not one by budget. The touch on a band trading a few
        # hundred dollars holds 5 or 10 shares; the venue wants $5 of notional,
        # which at 8c is 62.5 shares. There is usually size for it one or two
        # levels up.
        #
        # So pay for it, and let the desk's OWN min_edge decide whether it is
        # worth paying. No new threshold is introduced here on purpose: the
        # walk stops at the venue floor, which is the smallest order that can
        # exist, so the slippage is bounded by how deep the book is in its
        # first $5 - and if the edge does not survive even that, the refusal
        # below says so in those words instead of blaming depth.
        #
        # Only the case the touch cannot serve goes through here. A plan whose
        # touch already clears the floor is priced and sized exactly as before,
        # so this cannot change a trade that was already working.
        walked = []
        for bid, token, price, rate, book in quoted:
            reached = ladder_cost(book, floor)
            if reached is None:
                walked = None
                break
            limit, average = reached
            walked.append((bid, token, limit, average, rate, book))
        if walked is None:
            raise ValueError(
                f'Below the venue minimum order size: the whole book holds {plain(depth)} '
                f'shares against a {plain(floor)}-share floor at any price, limited by book depth')
        walked_cost = sum((avg + rate * avg * (1 - avg) for _, _, _, avg, rate, _ in walked),
                          Decimal(0))
        walked_edge = expected - walked_cost
        if walked_edge < number(policy['min_edge']):
            raise ValueError(
                f'Reaching the {plain(floor)}-share venue minimum costs '
                f'{plain((walked_cost-unit_cost).quantize(Decimal(".0001")))} per share more than '
                f'the touch, leaving {plain(walked_edge.quantize(Decimal(".0001")))} against a '
                f'{plain(number(policy["min_edge"]))} policy minimum')
        # SIZE AGAINST WHAT IS RESERVED, NOT AGAINST WHAT IT IS EXPECTED TO
        # COST. The reserve below covers the order filling entirely at its own
        # limit, because that is what a limit order can do and what the
        # database requires. Dividing the budget by the cheaper depth-weighted
        # cost would pass a plan here whose legs the engine then refuses.
        walked_reserve = sum((lim + rate * lim * (1 - lim)
                              for _, _, lim, _, rate, _ in walked), Decimal(0))
        affordable = ((budget - Decimal('.01') * len(quoted)) / walked_reserve).quantize(
            Decimal('.01'), rounding=ROUND_DOWN)
        if affordable < floor:
            raise ValueError(
                f'Below the venue minimum order size: {plain(affordable)} shares affordable at '
                f'the depth-weighted price against a {plain(floor)}-share floor, limited by '
                'account budget')
        quoted = [(bid, token, limit, rate, book) for bid, token, limit, _, rate, book in walked]
        quantity, unit_cost, net_edge = floor, walked_cost, walked_edge
    elif quantity < floor:
        raise ValueError(
            f'Below the venue minimum order size: {plain(quantity)} shares against a '
            f'{plain(floor)}-share floor, limited by {limited_by}')
    legs, quotes = [], []
    for bid,token,price,rate,book in quoted:
        order={'action':'BUY','token_id':token,'shares':str(quantity),'limit_price':str(price),'share_step':str(DEFAULT_SHARE_STEP),
            'max_book_age_seconds':120,'expires_at':(now+dt.timedelta(minutes=5)).isoformat()}
        preview = simulate(order,book,now=fixed_now or dt.datetime.now(dt.timezone.utc))
        if preview['status']!='filled':
            raise ValueError('Full requested plan is not executable: '+str(preview['reason']))
        # THE RESERVE IS THE WORST CASE, NOT THE QUOTE.
        #
        # This was the simulated fill - notional plus fee at today's book - and
        # that was only ever safe by accident. Both queue_plan and
        # submit_single_paper_order require
        #
        #     cash_ceiling >= shares * limit_price
        #
        # which is right: a limit order may fill anywhere up to its limit, so
        # the reserve has to cover the limit. While the limit WAS the touch,
        # the simulated fill was exactly shares * limit and the inequality held
        # by coincidence. The ladder walk sets the limit to the worst level it
        # reaches while the fill averages lower, so the quote fell BELOW the
        # limit and the engine refused every walked plan with "Invalid leg cash
        # reservation" - 8 of them in the first run after the walk went live,
        # each one a trade the desk had already decided to take.
        #
        # The fee is taken at the limit as well, and never below what the quote
        # says: rate*p*(1-p) rises with p up to a half, so the limit is the
        # worst case on a cheap contract, and max() covers the other side.
        worst_notional = quantity * price
        worst_fee = max(quantity * rate * price * (1 - price), number(preview['fee']))
        ceiling=max(number(preview['notional'])+number(preview['fee']),
                    worst_notional+worst_fee).quantize(Decimal('.01'),rounding=ROUND_UP)
        legs.append({'band_id':bid,'side':side,'shares':str(quantity),'limit_price':str(price),'cash_ceiling':str(ceiling)})
        quotes.append({'snapshot_id':book['snapshot_id'],'preview':preview,'venue_metadata':book.get('market_metadata')})
    return legs, {'signal':signal,'quotes':quotes,'net_edge_per_share':str(net_edge),'expected_payout_per_basket':str(expected),
        'quoted_cost_per_basket':str(unit_cost),'prepared_at':now.isoformat(),
        'engine_shares':str(target),'budget_shares':str(affordable_q),
        **({'allocation':allocated} if allocated is not None else {}),
        'execution_assumption':'Independent IOC legs; no guaranteed basket completion',
        'engine_version':os.environ.get('GITHUB_SHA') or os.environ.get('ARBDESK_ENGINE_VERSION','unversioned')}


def interleave_by_strategy(signals):
    """Round-robin across strategies inside each fired_at batch.

    THE SAME STARVATION AS THE DESK LOOP, ONE LEVEL DOWN. The comment in
    cycle() describes how one desk used to eat a global plan budget and leave
    every later desk with nothing. That was fixed. The identical thing was
    still happening to STRATEGIES, and it is worse because it is invisible:
    the desk that starved looked switched off, but a starved strategy looks
    like a strategy whose conditions are never met.

    signal_engine writes every strategy in ONE batch with ONE fired_at, so
    `order by fired_at desc, signal_id` is really `order by whatever order the
    engine inserted them`. Measured on the 15:09:53.937872 batch - a single
    microsecond shared by four strategies:

        s1_buy_low_sell_signal   27 signals, ids 6308-6334   offered 1st
        s3_concentration         31 signals, ids 6335-6365   offered 2nd
        s4_tail_fade              7 signals, ids 6366-6372   offered 3rd
        s6_anchor_insurance      29 signals, ids 6373-6401   offered 4th

    The cap is ten plans per desk per cycle. s1 has twenty-seven signals ahead
    of s3's first one, so s1 takes the whole budget before s3 is looked at -
    every cycle, for as long as s1 keeps firing. Over thirty days:

        s1   912 signals -> 395 plans
        s3   978 signals ->   1 plan
        s6 1,338 signals ->   1 plan
        s5    37 signals ->   0 plans

    Not because those strategies are worse. Because they are written second.

    So each strategy is offered its best signal before any strategy is offered
    its second - the same rule the desk loop already follows, for the same
    reason. Order ACROSS batches is untouched: fresher decisions still come
    first, because staleness is a real disqualifier and insert order is not.
    """
    out, batch, key = [], [], object()
    def flush(rows):
        queues, order = {}, []
        for row in rows:
            sid = row.get('strategy_id')
            if sid not in queues:
                queues[sid] = []
                order.append(sid)
            queues[sid].append(row)
        while any(queues[s] for s in order):
            for sid in order:
                if queues[sid]:
                    out.append(queues[sid].pop(0))
    for row in signals:
        if row.get('fired_at') != key:
            flush(batch)
            batch, key = [], row.get('fired_at')
        batch.append(row)
    flush(batch)
    return out


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

    # ACTIVE ONLY. A retired desk refuses every plan in queue_plan, and a
    # suspended one refuses every entry (plan v2 P5.1), so offering them a
    # signal only publishes a blocked plan and spends one of their plan slots
    # on a refusal nobody needs to read.
    accounts = rest_all('paper_accounts',{'mode':'in.(assisted,automatic)','status':'eq.active'},order='account_id')
    if not accounts:
        return done(0, 'no active assisted or automatic account')
    signals = interleave_by_strategy(rest_all('signals',
        [('action','eq.ENTER'),('fired_at','gte.'+(now-dt.timedelta(minutes=15)).isoformat()),
         ('fired_at','lte.'+now.isoformat())],order='fired_at.desc,signal_id'))
    enabled = {s['strategy_id'] for s in rest('strategies',{'enabled':'eq.true','select':'strategy_id'})}
    seen = set()
    rails, halted, halt_reason = risk_rails.load()
    def rails_room(account, quoted, context):
        return rail_room(account, quoted, context, rails, halted, halt_reason)
    def capture(order):
        if time.monotonic()-started>budget_seconds:
            raise ValueError('Proposal cycle budget reached; await the next fresh decision')
        return capture_book(order)
    # THE CAP IS PER DESK, AND SIGNALS ARE THE OUTER LOOP. Both halves of that
    # sentence are a bug fix, and it is the same bug twice.
    #
    # It was `for account: for signal:` against ONE global cap of ten. The
    # first desk in account_id order that could act on the board consumed the
    # whole budget and returned, so every desk after it got nothing - not
    # fewer plans, none, on every cycle, for as long as the first desk kept
    # finding signals. Measured on 16 Sep: 'Wide edge, all US' took 39 plans in
    # three hours and 'All Cities', created eleven minutes before a run that
    # published ten, got zero. A desk you have just switched on sits at zero
    # positions looking exactly like a desk with nothing to trade.
    #
    # PER DESK, because ten plans a cycle is a statement about what a desk may
    # propose - it is not what bounds this job. budget_seconds does that, and
    # it is the guard that matters: the work here is venue calls, and those
    # cost time, not plan slots.
    #
    # SIGNALS OUTSIDE, so time exhaustion cannot always fall on the same desk.
    # Signals arrive fired_at.desc, freshest first, so every desk is offered
    # the best signal on the board before any desk is offered the second-best.
    # With the loops the other way round, the last desk in id order reaches the
    # 90-second wall first, every single cycle - starvation again, wearing a
    # different hat.
    taken = {a['account_id']: 0 for a in accounts}
    for signal in signals:
        if all(n >= max_plans for n in taken.values()):
            return done(published, f'every desk reached its {max_plans}-plan cap')
        for account in accounts:
            if time.monotonic()-started>budget_seconds:
                return done(published, f'reached the {budget_seconds}s budget')
            if taken[account['account_id']] >= max_plans:
                continue
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
                legs,evidence=prepare(account,signal,capture,rail_room=rails_room,allocation=allocation_room)
            except (ValueError,KeyError,TypeError,ArithmeticError,requests.RequestException) as exc:
                reason=str(exc)[:400]
            rpc('publish_paper_plan',{'p_account':account['account_id'],'p_command':command,'p_signal':signal['signal_id'],
                'p_legs':legs,'p_evidence':evidence,'p_block_reason':reason})
            published+=1
            taken[account['account_id']]+=1
    return done(published, 'considered every signal')


if __name__=='__main__':
    print(cycle())
