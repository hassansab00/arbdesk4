"""Execution cost model (plan v2 P5.4): what a quantity really costs, both ways.

TAKER. Buying q shares walks the ask ladder, cheapest first, and pays the
venue's taker fee on every share: rate * price * (1 - price), the curve
cost_model.taker_fee() already carries (weather markets, rate 0.05). The
answer is a CURVE, not the touch: the holdings solver (P5.5) needs the cost of
the next share, which rises as the order eats into the book.

EXIT. Selling q shares walks the bid ladder, dearest first, and pays the same
fee on the way out.

MAKER. A resting bid pays no fee, but it may not fill, and when it does fill
it is disproportionately because the price was about to move against it.
Both are learned in P5.8 from our own simulated maker orders and from trade
prints. Until then they are the plan's priors:

    P_fill = 0.2 for a bid at the touch resting one hour
    adverse = 0.5 x the spread, the expected mid move against a filled bid

The fill prior is a constant hazard, so a bid resting t hours fills with
probability 1 - 0.8^t, and it stops at the market's close. A bid BELOW the
touch gets P_fill = 0 under the prior: there is no evidence yet of how often
such a bid fills, and zero is the direction that never invents an edge. A
bid at or above the best ask is not a maker order at all - it crosses.

RULE 11. P_fill and adverse are learned parameters, so each has a prior (the
plan's), hard bounds (this module's priors, below), and a version that every
answer carries. load() reads them from strategy_params (P5.8) and falls back
to the priors while that table does not exist.
"""
import math
import sys

from cost_model import DEFAULT_TAKER_RATE, taker_fee

P_FILL_TOUCH_1H = 0.2
ADVERSE_SPREAD_FRACTION = 0.5
# Hard bounds on what P5.8 may learn. This module's priors, not measurements.
P_FILL_TOUCH_1H_BOUNDS = (0.01, 0.9)
ADVERSE_BOUNDS = (0.0, 1.0)
PRIOR_VERSION = "prior"

_EPS = 1e-12


def _levels(book_side, ascending):
    out = []
    for lvl in book_side or []:
        p, s = float(lvl["price"]), float(lvl["size"])
        if s > 0 and 0.0 < p < 1.0:
            out.append((p, s))
    return sorted(out, key=lambda x: x[0], reverse=not ascending)


def fee_per_share(price, rate=DEFAULT_TAKER_RATE):
    return taker_fee(1.0, price, rate)


# --------------------------------------------------------------------------
# Taker: the ask ladder
# --------------------------------------------------------------------------

def taker_curve(asks, rate=DEFAULT_TAKER_RATE):
    """The marginal cost curve of buying: one segment per ask level.

    Returns [(from_shares, to_shares, price, cost_per_share)], cheapest first,
    where cost_per_share = price + fee. The cost of the k-th share is the
    cost_per_share of the segment holding it.
    """
    curve, held = [], 0.0
    for price, size in _levels(asks, ascending=True):
        curve.append((held, held + size, price, price + fee_per_share(price, rate)))
        held += size
    return curve


def taker_cost(asks, shares, rate=DEFAULT_TAKER_RATE):
    """What taking `shares` off the ask ladder costs, fee included.

    fully_filled is False when the ladder holds fewer shares than asked; the
    other numbers then describe what it does hold. limit_price is the last
    level reached, the price an order must be sent at to fill all of it.
    """
    want = float(shares)
    got = paid = fees = 0.0
    limit = None
    marginal = None
    for price, size in _levels(asks, ascending=True):
        if got >= want - _EPS:
            break
        take = min(size, want - got)
        got += take
        paid += take * price
        fees += take * fee_per_share(price, rate)
        limit = price
        marginal = price + fee_per_share(price, rate)
    total = paid + fees
    return {
        "shares": got, "fully_filled": got >= want - 1e-9,
        "price_usd": paid, "fee_usd": fees, "total_usd": total,
        "avg_cost_per_share": (total / got) if got > 0 else None,
        "marginal_cost_per_share": marginal, "limit_price": limit,
    }


# --------------------------------------------------------------------------
# Exit: the bid ladder
# --------------------------------------------------------------------------

def exit_proceeds(bids, shares, rate=DEFAULT_TAKER_RATE):
    """What selling `shares` into the bid ladder returns, fee deducted."""
    want = float(shares)
    got = gross = fees = 0.0
    limit = None
    for price, size in _levels(bids, ascending=False):
        if got >= want - _EPS:
            break
        take = min(size, want - got)
        got += take
        gross += take * price
        fees += take * fee_per_share(price, rate)
        limit = price
    net = gross - fees
    return {
        "shares": got, "fully_filled": got >= want - 1e-9,
        "gross_usd": gross, "fee_usd": fees, "net_usd": net,
        "avg_net_per_share": (net / got) if got > 0 else None, "limit_price": limit,
    }


# --------------------------------------------------------------------------
# Maker: fill probability and adverse selection
# --------------------------------------------------------------------------

def priors():
    return {"p_fill_touch_1h": P_FILL_TOUCH_1H, "adverse_spread_fraction": ADVERSE_SPREAD_FRACTION,
            "version": PRIOR_VERSION}


def _bounded(value, bounds):
    lo, hi = bounds
    return min(max(float(value), lo), hi)


def p_fill(bid, best_bid, best_ask, ttl_h, hours_to_close, band_liquidity=None, params=None):
    """Probability a resting bid at `bid` fills within its life.

    None when the bid is not a maker order (it would cross the best ask, or
    the book has no ask to rest under). band_liquidity is accepted for the
    learned model (P5.8); the prior does not use it.
    """
    params = params or priors()
    if best_ask is None or bid >= best_ask - _EPS:
        return None
    if best_bid is not None and bid < best_bid - _EPS:
        return 0.0
    hours = max(0.0, min(float(ttl_h), float(hours_to_close)))
    touch = _bounded(params.get("p_fill_touch_1h", P_FILL_TOUCH_1H), P_FILL_TOUCH_1H_BOUNDS)
    hazard = -math.log(1.0 - touch)          # per hour
    return 1.0 - math.exp(-hazard * hours)


def adverse(best_bid, best_ask, params=None):
    """Expected mid move against a filled bid, in price units."""
    params = params or priors()
    if best_bid is None or best_ask is None or best_ask <= best_bid:
        return 0.0
    frac = _bounded(params.get("adverse_spread_fraction", ADVERSE_SPREAD_FRACTION), ADVERSE_BOUNDS)
    return frac * (best_ask - best_bid)


def maker_quote(bid, shares, best_bid, best_ask, ttl_h, hours_to_close,
                band_liquidity=None, params=None):
    """The maker option at `bid`, next to what the same shares cost as a taker.

    effective_cost_per_share is the price plus the adverse selection a filled
    bid should expect; makers pay no fee. expected_shares is what the bid is
    expected to buy before it expires.
    """
    params = params or priors()
    pf = p_fill(bid, best_bid, best_ask, ttl_h, hours_to_close, band_liquidity, params)
    if pf is None:
        return None
    adv = adverse(best_bid, best_ask, params)
    return {"p_fill": pf, "expected_shares": pf * float(shares),
            "adverse_per_share": adv, "effective_cost_per_share": float(bid) + adv,
            "version": params.get("version", PRIOR_VERSION)}


def load(rest=None):
    """The latest learned maker parameters from strategy_params, or the priors."""
    if rest is None:
        from common import rest as rest
    params = priors()
    try:
        rows = rest("strategy_params", [("select", "param,value,version"),
                                        ("param", "in.(p_fill_touch_1h,adverse_spread_fraction)"),
                                        ("order", "fitted_at.desc"), ("limit", "10")])
    except Exception as e:
        print(f"  note: no learned maker parameters ({e}); using the priors", file=sys.stderr)
        return params
    seen, versions = set(), []
    for r in rows or []:
        name = r.get("param")
        if name in seen or name not in ("p_fill_touch_1h", "adverse_spread_fraction"):
            continue
        seen.add(name)
        value = (r.get("value") or {}).get("v")
        if value is None:
            continue
        bounds = P_FILL_TOUCH_1H_BOUNDS if name == "p_fill_touch_1h" else ADVERSE_BOUNDS
        params[name] = _bounded(value, bounds)
        versions.append(f"{name}:{r.get('version')}")
    if versions:
        params["version"] = ",".join(sorted(versions))
    return params
