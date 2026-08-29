"""
AD4 cost model.

SOURCED: Polymarket weather markets, fee schedule effective 30 March 2026.
  fee = shares * rate * p * (1 - p)
  weather rate = 0.05  ->  peak 1.25% at p = 0.50, falling to ~0 at both extremes
  TAKER ONLY. Makers pay zero and earn a rebate (~25%).
  Settlement is free: the formula returns 0 at p=0 and p=1.

Design consequence: AD4 defaults to resting LIMIT orders. Same trade, zero fee
instead of 1.25%, plus a rebate. This is the highest-leverage cost decision in
the system.

Every P&L figure in AD4 is net. This module is the single source of truth for
what a trade actually costs.
"""

DEFAULT_TAKER_RATE = 0.05
DEFAULT_MAKER_REBATE = 0.25
DEFAULT_GAS_USD = 0.01


def taker_fee(shares, price, rate=DEFAULT_TAKER_RATE):
    """Polymarket weather taker fee. Peaks at p=0.50."""
    return shares * rate * price * (1.0 - price)


def maker_fee(shares, price):
    """Makers pay nothing."""
    return 0.0


def maker_rebate(taker_fee_paid_by_counterparty, rebate_rate=DEFAULT_MAKER_REBATE):
    return taker_fee_paid_by_counterparty * rebate_rate


def walk_ladder(levels, usd_budget=None, shares_target=None, max_slippage=None):
    """
    Walk an order book ladder and return what is ACTUALLY obtainable.

    levels: list of {"price": float, "size": float} sorted best-first
    Returns dict:
      shares, usd_spent, avg_price, quoted_price, slippage,
      fully_filled (bool), levels_consumed

    max_slippage: stop filling once price moves this far from the touch.
    This is what prevents the calculator recommending size that cannot fill.

    Exactly one of usd_budget / shares_target must be set.
    """
    empty = dict(shares=0.0, usd_spent=0.0, avg_price=None, quoted_price=None,
                 slippage=None, fully_filled=False, levels_consumed=0)
    if not levels:
        return empty
    if (usd_budget is None) == (shares_target is None):
        raise ValueError("walk_ladder requires exactly one of usd_budget or shares_target")

    quoted_price = float(levels[0]["price"])
    shares = 0.0
    usd_spent = 0.0
    levels_consumed = 0

    for lvl in levels:
        price = float(lvl["price"])
        size = float(lvl["size"])
        if max_slippage is not None and (price - quoted_price) > max_slippage:
            break

        if shares_target is not None:
            remaining = shares_target - shares
            if remaining <= 1e-12:
                break
            take = min(size, remaining)
        else:
            remaining_usd = usd_budget - usd_spent
            if remaining_usd <= 1e-12:
                break
            take = size if price <= 0 else min(size, remaining_usd / price)

        if take <= 0:
            break
        shares += take
        usd_spent += take * price
        levels_consumed += 1

    if shares_target is not None:
        fully_filled = shares >= shares_target - 1e-9
    else:
        fully_filled = usd_spent >= usd_budget - 1e-9

    avg_price = (usd_spent / shares) if shares > 0 else None
    slippage = (avg_price - quoted_price) if avg_price is not None else 0.0

    return dict(shares=shares, usd_spent=usd_spent, avg_price=avg_price,
                quoted_price=quoted_price, slippage=slippage,
                fully_filled=fully_filled, levels_consumed=levels_consumed)


def round_trip_cost(shares, entry_price, exit_price, entry_is_taker=True,
                     exit_is_taker=True, spread=0.0, gas_usd=DEFAULT_GAS_USD):
    """
    Total cost of a position that is opened AND closed before resolution.

    CRITICAL for strategy s1_buy_low_sell_signal: it pays a fee and crosses
    the spread on entry AND exit. Observed live spreads reached 14-18c on
    Madrid and Milan. This function is why S1 must beat the market by more
    than two spreads plus two fees, not merely beat it.

    `spread` is the full quoted bid/ask spread crossed on a taker leg. A
    maker leg (entry_is_taker/exit_is_taker = False) pays no fee and crosses
    no spread - it rests instead, which carries fill risk handled elsewhere
    (paper_engine's legging-risk modelling), not a cost modelled here.
    """
    entry_fee = taker_fee(shares, entry_price) if entry_is_taker else maker_fee(shares, entry_price)
    exit_fee = taker_fee(shares, exit_price) if exit_is_taker else maker_fee(shares, exit_price)
    entry_spread_cost = shares * spread if entry_is_taker else 0.0
    exit_spread_cost = shares * spread if exit_is_taker else 0.0
    total_cost = entry_fee + exit_fee + entry_spread_cost + exit_spread_cost + gas_usd

    return dict(entry_fee=entry_fee, exit_fee=exit_fee,
                entry_spread_cost=entry_spread_cost, exit_spread_cost=exit_spread_cost,
                gas_usd=gas_usd, total_cost=total_cost)


def net_pnl(shares, entry_price, exit_price_or_settlement, costs):
    """
    Gross minus every cost. The ONLY P&L figure shown outside cost analysis.

    `costs` accepts either a total dollar amount, or a dict with a
    `total_cost` key (e.g. the return value of round_trip_cost).
    """
    gross_pnl = shares * (exit_price_or_settlement - entry_price)
    total_cost = costs["total_cost"] if isinstance(costs, dict) else costs
    return dict(gross_pnl=gross_pnl, total_cost=total_cost, net_pnl=gross_pnl - total_cost)
