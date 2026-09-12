"""Deterministic immediate-or-cancel paper fills against captured token books.

No live order endpoint, maker-fill assumption, synthetic depth, or historical
signal-price execution. Decimal amounts and individual levels are retained.
"""
from datetime import datetime, timezone
from decimal import Decimal, ROUND_DOWN, ROUND_HALF_UP


def number(value):
    result = Decimal(str(value))
    if not result.is_finite():
        raise ValueError("non-finite amount")
    return result


def instant(value):
    result = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    if result.tzinfo is None:
        raise ValueError("timestamp must include timezone")
    return result


def simulate(order, book, *, now=None, consumed=None):
    """Return level fills or a named rejection, leaving persistence to SQL.

    book contains a directly captured token_id, observed_at, bids/asks,
    min_order_size, tick_size, fee_rate, and snapshot_id. Consumption is the
    account's already simulated use of this snapshot keyed by price/action.
    """
    now = now or datetime.now(timezone.utc)
    empty = {'status': 'rejected', 'fills': [], 'shares': '0', 'notional': '0', 'fee': '0'}
    try:
        if order['action'] not in ('BUY', 'SELL'):
            raise ValueError("invalid action")
        if order['token_id'] != book['token_id']:
            raise ValueError("token mismatch")
        if now >= instant(order['expires_at']):
            return {**empty, 'status': 'expired', 'reason': 'order_expired'}
        age = (now - instant(book['observed_at'])).total_seconds()
        if age < 0 or age > int(order['max_book_age_seconds']):
            raise ValueError("book_stale_or_future")
        if not book.get('tradeable'):
            raise ValueError("market_not_tradeable")
        quantity, limit = number(order['shares']), number(order['limit_price'])
        step, tick = number(order['share_step']), number(book['tick_size'])
        rate, minimum = number(book['fee_rate']), number(book['min_order_size'])
        if quantity <= 0 or step <= 0 or tick <= 0 or not 0 < limit < 1 or not 0 <= rate <= 1:
            raise ValueError("invalid order or venue metadata")
        minimum_unit = book['min_order_size_unit']
        if minimum_unit not in ('shares', 'USDC'):
            raise ValueError('unknown minimum order size unit')
        order_size = quantity if minimum_unit == 'shares' else quantity * limit
        if quantity % step or limit % tick or order_size < minimum:
            raise ValueError("quantity_or_price_increment")
        buy = order['action'] == 'BUY'
        levels = book.get('asks' if buy else 'bids')
        if not isinstance(levels, list):
            raise ValueError("missing_direct_token_depth")
        grouped = {}
        for level in levels:
            p, q = number(level['price']), number(level['size'])
            if not 0 < p < 1 or q <= 0 or p % tick:
                raise ValueError("invalid_book_level")
            grouped[p] = grouped.get(p, Decimal(0)) + q
        remaining, fills = quantity, []
        for p in sorted(grouped, reverse=not buy):
            if (buy and p > limit) or (not buy and p < limit):
                break
            available = max(Decimal(0), grouped[p] - number((consumed or {}).get(str(p), 0)))
            q = (min(remaining, available) / step).to_integral_value(rounding=ROUND_DOWN) * step
            if q <= 0:
                continue
            fee = (q * rate * p * (1-p)).quantize(Decimal('.00001'), rounding=ROUND_HALF_UP)
            fills.append({'price': str(p), 'shares': str(q), 'notional': str(q*p), 'fee': str(fee)})
            remaining -= q
            if not remaining:
                break
        taken = quantity - remaining
        return {'status': 'filled' if remaining == 0 else ('partial' if taken else 'rejected'),
                'reason': None if remaining == 0 else 'insufficient_depth_within_limit',
                'snapshot_id': book['snapshot_id'], 'fills': fills, 'shares': str(taken),
                'notional': str(sum((number(f['notional']) for f in fills), Decimal(0))),
                'fee': str(sum((number(f['fee']) for f in fills), Decimal(0))),
                'simulated_at': now.isoformat(), 'book_age_seconds': age}
    except (ValueError, KeyError, TypeError, ArithmeticError) as exc:
        return {**empty, 'reason': str(exc)}
