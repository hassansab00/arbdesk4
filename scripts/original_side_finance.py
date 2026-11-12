"""Direct-token, fee-inclusive paper comparisons. No forecast or outcome fitting.

The existing execution simulator remains the fill engine. These helpers size an
IOC against each side's own depth and keep cash which did not fill as cash.
"""
import datetime as dt
from decimal import Decimal, ROUND_DOWN

from paper_execution import instant, number, simulate

VERSION = 'original-bucket-sides-v2.1'
STRATEGY = 's15_original_bucket_sides'
PRECISION_SOURCE = 'https://github.com/Polymarket/py-clob-client/blob/main/py_clob_client/order_builder/builder.py'
# The venue SDK's explicit size=2 profiles, checked at implementation time.
# Unknown ticks/protocols fail closed rather than inventing a new increment.
SIZE_PROFILES = {'0.1': '0.01', '0.01': '0.01', '0.001': '0.01', '0.0001': '0.01'}


def quote_metadata(raw, market):
    """Use the book's minimum *shares*, not Gamma's ambiguous dollar label."""
    tick = format(number(raw['tick_size']), 'f')
    if tick not in SIZE_PROFILES or 'min_order_size' not in raw:
        raise ValueError('unverified_minimum_or_precision')
    minimum = number(raw['min_order_size'])
    if minimum <= 0:
        raise ValueError('unverified_minimum_or_precision')
    fee = market.get('feeSchedule') or {}
    if market.get('feesEnabled') is False:
        rate = number(0)
    elif market.get('feesEnabled') is True and fee.get('exponent') == 1 and fee.get('rate') is not None:
        rate = number(fee['rate'])
    else:
        raise ValueError('fee_schedule_unverified')
    if not 0 <= rate <= 1:
        raise ValueError('fee_schedule_unverified')
    return dict(tick_size=tick, share_step=SIZE_PROFILES[tick], min_order_size=str(minimum),
        min_order_size_unit='shares', fee_rate=str(rate), fee_formula='shares*rate*p*(1-p)',
        fee_rounding='half_up_5dp', precision_source=PRECISION_SOURCE,
        precision_version='py-clob-client-size2-2026-11-11', fees_enabled=market['feesEnabled'])


def budget_order(book, stake, *, now=None):
    """Largest step-aligned IOC under an inclusive cash ceiling and direct depth.

    Binary search uses the simulator's independently rounded per-level fees;
    dividing cash by a best ask would overfill multi-price books and fees.
    """
    now = now or dt.datetime.now(dt.timezone.utc)
    stake, step, tick = number(stake), number(book['share_step']), number(book['tick_size'])
    if book.get('quote_kind', 'direct') != 'direct':
        raise ValueError('direct_token_book_required')
    if not 0 < stake <= 1000000 or step <= 0 or tick <= 0:
        raise ValueError('invalid_stake_or_precision')
    received = instant(book['received_at'])
    observed = instant(book['observed_at'])
    if not observed <= received <= now or (now-observed).total_seconds() > 120:
        raise ValueError('book_stale_or_future')
    levels = book.get('asks')
    if not isinstance(levels, list) or not levels:
        raise ValueError('missing_direct_token_depth')
    if not book.get('tradeable'):
        raise ValueError('market_not_tradeable')
    for level in levels:
        p, q = number(level['price']), number(level['size'])
        if not 0 < p < 1 or p % tick or q <= 0:
            raise ValueError('invalid_book_level')
    limit = max(number(x['price']) for x in levels)
    total = sum((number(x['size']) for x in levels), Decimal(0))
    upper = int((min(total, stake/min(number(x['price']) for x in levels))/step).to_integral_value(rounding=ROUND_DOWN))
    order = dict(action='BUY', token_id=book['token_id'], limit_price=str(limit),
        share_step=str(step), cash_ceiling=str(stake), max_book_age_seconds=120,
        expires_at=(now+dt.timedelta(minutes=5)).isoformat())
    low, high, best = 1, upper, None
    while low <= high:
        units = (low+high)//2
        result = simulate(dict(order, shares=str(step*units)), book, now=now)
        # Below venue minimum is not a malformed price/depth error.
        if result.get('reason') == 'quantity_or_price_increment' and step*units < number(book['min_order_size']):
            low = units+1
            continue
        if result['status'] not in ('filled', 'partial'):
            raise ValueError(result.get('reason') or 'unavailable_fill')
        cost = number(result['notional'])+number(result['fee'])
        if cost <= stake:
            best = result
            low = units+1
        else:
            high = units-1
    if best is None or number(best['shares']) <= 0:
        raise ValueError('allocation_below_venue_minimum')
    quantity = number(best['shares'])
    order.update(shares=str(quantity), limit_price=max((number(x['price']) for x in best['fills'])).__str__())
    cost = number(best['notional'])+number(best['fee'])
    return order, dict(best, calculation_version=VERSION, requested_stake=str(stake),
        filled_cost=str(cost), unspent_allocation=str(stake-cost),
        profit_if_win=str(quantity-cost), loss_if_lose=str(-cost),
        payout_if_win=str(quantity), quote_kind='direct')


def derived_no_scenario(saved_yes_bid, *, enabled=False):
    """Research price only. Never supplies execution depth or fee evidence."""
    if not enabled:
        return None
    if not saved_yes_bid or not saved_yes_bid.get('received_at') or not saved_yes_bid.get('observed_at'):
        return None
    try:
        bid = number(saved_yes_bid['best_bid'])
        if not 0 < bid < 1 or instant(saved_yes_bid['observed_at']) > instant(saved_yes_bid['received_at']):
            return None
    except (KeyError, ValueError, TypeError, ArithmeticError):
        return None
    return dict(quote_kind='derived_scenario', price=str(1-bid), source_yes_bid=str(bid),
        observed_at=saved_yes_bid['observed_at'], received_at=saved_yes_bid['received_at'],
        label='Derived NO-price scenario — not a recorded NO ask or verified fill',
        execution_eligible=False, net_pnl=None, reason='unavailable_depth_and_fee_evidence')


def settled_pnl(evaluation, payout_per_share):
    """A verified payout can be fractional; unresolved evidence stays null."""
    if payout_per_share is None:
        return None
    payout = number(payout_per_share)
    if not 0 <= payout <= 1:
        raise ValueError('invalid_token_payout')
    return str(number(evaluation['shares'])*payout-number(evaluation['filled_cost']))
