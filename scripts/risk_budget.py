#!/usr/bin/env python3
"""
Three limits on how much the desk may have at risk, and why each one exists.

WHAT WAS ALREADY THERE. allocator.allocate() solves ONE ladder: the bands of
one city-day are mutually exclusive, so they are a single allocation problem
and it sizes them together at a quarter of full Kelly. That is correct and it
is not a risk framework, because it has never seen a second ladder. Forty-eight
cities each sized to a quarter Kelly, independently, is not a quarter-Kelly
desk - it is forty-eight of them, and on a day when half the northern
hemisphere misses in the same direction they are not forty-eight bets.

Nothing above the ladder existed. No drawdown response, no correlation
accounting, no limit on what one day could commit.

--------------------------------------------------------------------------
1. DRAWDOWN-SCALED RISK, derived rather than chosen
--------------------------------------------------------------------------
Bet a fraction lambda of the full Kelly stake. In the diffusion limit, log
wealth is a Brownian motion with

    drift     a   = (mu^2/sigma^2) * (lambda - lambda^2/2)
    variance  v^2 = lambda^2 * mu^2/sigma^2

and the probability that a Brownian motion with positive drift ever falls by
L is exp(-2aL/v^2). Putting L = ln(1/b) - the loss that takes wealth to a
fraction b of where it stands:

    P(wealth ever reaches b * current) = b^(2/lambda - 1)

SANITY CHECK, because a formula nobody checks is a formula nobody should use:
at lambda = 1 the exponent is 1 and P = b. That is the classical Kelly result
- a full-Kelly bettor's chance of ever halving their bankroll is one half. At
lambda = 0.25 the exponent is 7 and the chance of ever halving is 0.5^7, or
0.78%.

MU AND SIGMA CANCEL. The control needs no estimate of the desk's edge or its
volatility, which is exactly why it can be trusted here: every attempt to
scale risk by a measured Sharpe on 294 settled days would be scaling risk by
noise.

Inverting it gives the lambda a stated risk appetite allows:

    lambda = 2 / (1 + ln(P_target) / ln(b))

where b is the distance still available - the floor as a fraction of CURRENT
equity, not of the high-water mark. That is what makes it respond to a
drawdown: as equity falls toward the floor, b rises toward 1, ln(b) rises
toward 0, and lambda falls to 0 exactly at the floor. No cliff, no override.

THE TWO KNOBS ARE A RISK APPETITE, NOT A FUDGE. DRAWDOWN_FLOOR says how far
below the high-water mark the desk may fall before it stops opening risk.
RUIN_TARGET says what chance of ever getting there is acceptable. At the
high-water mark the defaults give lambda = 0.268, just above the quarter Kelly
the allocator already uses - so at the top the existing behaviour binds and
this control does nothing, and it takes over as soon as the desk is down.

--------------------------------------------------------------------------
2. CORRELATION BUDGET, because forty-eight cities are not forty-eight bets
--------------------------------------------------------------------------
derived_city_correlation holds the correlation of two cities' forecast errors
over a mean 322 days: 38,250 pairs across 52 cities, 790 of them above 0.5,
the strongest 0.971. When the desk holds Chicago and Toronto on the same day
it is closer to holding one position than two.

The effective number of independent bets is the standard diversification
ratio:

    N_eff = (sum_i w_i)^2 / (sum_i sum_j rho_ij w_i w_j)

With no correlation this is the number of positions; with everything perfectly
correlated it is 1. The budget is then simply

    total gross <= per_city_day_cap * N_eff

so a diversified book may carry more than a concentrated one, and a day when
every city moves together is sized as the single bet it is.

A MISSING PAIR IS NOT AN UNCORRELATED PAIR. Two cities with no row are treated
as correlated at DEFAULT_CORRELATION rather than at 0, because "we have not
measured it" and "they are independent" are different statements and only one
of them is safe to size on.

--------------------------------------------------------------------------
3. DAILY GROSS ENTRY BUDGET, which is what makes (1) able to work
--------------------------------------------------------------------------
The drawdown control reads REALISED equity. Money committed to positions that
have not settled is invisible to it. Settlement on this desk is daily, so the
window in which the control is blind is exactly one day - and the daily budget
is what bounds how much can be committed inside that window.

That is the whole justification, and it sets the default: at
DAILY_ENTRY_BUDGET_PCT the book cannot be spent in fewer than five days, which
gives the drawdown control four settlements in which to see a loss and respond
before the desk is fully committed.

--------------------------------------------------------------------------
All three are pure functions over numbers the caller supplies. Nothing here
reads a database, so every limit can be tested against a worked example
rather than against a live desk.
"""

import math
from collections import namedtuple

# 1. Drawdown.
DRAWDOWN_FLOOR = 0.70        # stop opening risk 30% below the high-water mark
RUIN_TARGET = 0.10           # an acceptable chance of ever reaching that floor
KELLY_CEILING = 1.0          # never above full Kelly, whatever the arithmetic says

# 2. Correlation.
PER_CITY_DAY_CAP_PCT = 3.0   # one weather event may risk 3% of the book
DEFAULT_CORRELATION = 0.30   # what an UNMEASURED pair is assumed to be
CORRELATION_FLOOR = 0.0      # a negative measured correlation is not a licence

# 3. Daily.
DAILY_ENTRY_BUDGET_PCT = 20.0   # one day may commit a fifth of the book

Budget = namedtuple("Budget", "lambda_scale gross_cap_usd per_city_cap_usd "
                              "daily_remaining_usd n_eff reasons")


# ---------------------------------------------------------------------------
# 1. Drawdown-scaled risk.
# ---------------------------------------------------------------------------
def ruin_probability(lambda_fraction, b):
    """P(wealth ever reaches b times its current value) under fractional Kelly.

    b must be in (0, 1): it is a level BELOW where wealth stands now. Returns
    1.0 for a lambda at or above 2, where the drift term turns non-positive
    and the bettor is certain to get there.
    """
    if not (0.0 < b < 1.0):
        raise ValueError("b must be a fraction strictly between 0 and 1")
    if lambda_fraction <= 0:
        return 0.0
    if lambda_fraction >= 2.0:
        return 1.0
    return b ** (2.0 / lambda_fraction - 1.0)


def kelly_for_risk_appetite(b, ruin_target=RUIN_TARGET):
    """The largest fractional Kelly whose ruin probability is ruin_target.

    Inverse of the above. b >= 1 means the floor is at or above current equity
    and there is nothing left to risk, so the answer is 0.
    """
    if b >= 1.0:
        return 0.0
    if b <= 0.0:
        return KELLY_CEILING
    if not (0.0 < ruin_target < 1.0):
        raise ValueError("ruin_target must be strictly between 0 and 1")
    lam = 2.0 / (1.0 + math.log(ruin_target) / math.log(b))
    return max(0.0, min(KELLY_CEILING, lam))


def drawdown_scale(equity, high_water, lambda_base,
                   floor_fraction=DRAWDOWN_FLOOR, ruin_target=RUIN_TARGET):
    """(scale, reason) - the factor to multiply the base Kelly fraction by.

    Returns 1.0 while the base fraction is the tighter of the two, which is
    the state at and near the high-water mark. Never above 1.0: this control
    exists to take risk off, and a control that can add it is a different
    thing wearing the same name.
    """
    if not high_water or high_water <= 0 or equity is None:
        return 1.0, "no high-water mark yet: base fraction unchanged"
    floor = floor_fraction * high_water
    if equity <= floor:
        return 0.0, (f"equity {equity:,.0f} at or below the "
                     f"{(1 - floor_fraction) * 100:.0f}% drawdown floor "
                     f"{floor:,.0f}: no new risk")
    b = floor / equity                      # how far down the floor still is
    allowed = kelly_for_risk_appetite(b, ruin_target)
    if allowed >= lambda_base:
        return 1.0, (f"drawdown {1 - equity / high_water:+.1%} allows "
                     f"{allowed:.3f} Kelly; base {lambda_base:.3f} is tighter")
    scale = allowed / lambda_base
    return scale, (f"drawdown {1 - equity / high_water:.1%} from "
                   f"{high_water:,.0f} allows {allowed:.3f} Kelly against a "
                   f"base of {lambda_base:.3f}: risk scaled to {scale:.2f}x")


# ---------------------------------------------------------------------------
# 2. Correlation budget.
# ---------------------------------------------------------------------------
def correlation_of(a, b, corr, default=DEFAULT_CORRELATION):
    """rho for one pair, symmetric, 1.0 on the diagonal, default when absent."""
    if a == b:
        return 1.0
    for key in ((a, b), (b, a)):
        if key in corr:
            return max(CORRELATION_FLOOR, float(corr[key]))
    return default


def effective_independent_bets(weights, corr, default=DEFAULT_CORRELATION):
    """N_eff = (sum w)^2 / (w' R w) - the diversification ratio.

    `weights` is {key: exposure}, `corr` is {(a, b): rho}. Keys are whatever
    the caller considers one bet; on this desk that is a city, because two
    ladders for the same city on different days share a station and a season.

    Returns 0.0 for an empty or non-positive book. Clamped to [1, len] - the
    quadratic form is positive for any correlation matrix that is one, but a
    matrix assembled pairwise from measurements need not be positive definite,
    and a book cannot hold fewer than one or more than all of its bets.
    """
    keys = [k for k, w in weights.items() if w and w > 0]
    if not keys:
        return 0.0
    total = sum(weights[k] for k in keys)
    quad = 0.0
    for a in keys:
        for b in keys:
            quad += correlation_of(a, b, corr, default) * weights[a] * weights[b]
    if quad <= 0:
        return 1.0
    return max(1.0, min(float(len(keys)), (total * total) / quad))


# ---------------------------------------------------------------------------
# The three together.
# ---------------------------------------------------------------------------
def budget(bankroll, equity, high_water, proposed_by_city, corr,
           lambda_base, spent_today_usd=0.0,
           per_city_cap_pct=PER_CITY_DAY_CAP_PCT,
           daily_budget_pct=DAILY_ENTRY_BUDGET_PCT,
           floor_fraction=DRAWDOWN_FLOOR, ruin_target=RUIN_TARGET,
           default_corr=DEFAULT_CORRELATION):
    """Every limit, resolved into numbers the allocator can apply.

    `proposed_by_city` is {city_key: usd the desk wants on it today} - the
    ladders as first solved, before any of this. It decides N_eff only; the
    caller scales its own stakes with what comes back.
    """
    reasons = []
    scale, why = drawdown_scale(equity, high_water, lambda_base,
                                floor_fraction, ruin_target)
    reasons.append(why)

    n_eff = effective_independent_bets(proposed_by_city, corr, default_corr)
    per_city = bankroll * (per_city_cap_pct / 100.0)
    gross_cap = per_city * n_eff
    if n_eff:
        reasons.append(
            f"{len(proposed_by_city)} city-day(s) count as {n_eff:.2f} "
            f"independent bet(s): gross capped at ${gross_cap:,.0f}")

    daily_cap = bankroll * (daily_budget_pct / 100.0)
    remaining = max(0.0, daily_cap - max(0.0, spent_today_usd))
    reasons.append(
        f"${spent_today_usd:,.0f} of today's ${daily_cap:,.0f} entry budget "
        f"already committed, ${remaining:,.0f} left")

    # THE CAPS ARE CEILINGS AND DO NOT MOVE WITH THE DRAWDOWN. The scale
    # belongs to the Kelly fraction, which is a preference about how much of
    # an edge to take; a cap is a limit on concentration whatever the edge.
    # Shrinking both would apply the same drawdown twice, and a ceiling that
    # moves with the thing it is capping is not a ceiling - the same reason
    # base.size() treats capital_cap_pct as a ceiling rather than an answer.
    return Budget(lambda_scale=scale, gross_cap_usd=gross_cap,
                  per_city_cap_usd=per_city,
                  daily_remaining_usd=remaining, n_eff=n_eff, reasons=reasons)


def apply_budget(proposed, limits, min_notional_usd=5.0):
    """Scale {key: usd} down to fit every limit. Returns (final, detail).

    THE ORDER MATTERS AND IT IS NOT ARBITRARY. The per-city cap binds first
    because it is about one event; the gross and daily caps are about the
    book, and capping a single city first can only reduce what the book-wide
    caps then have to cut. Applying them the other way round would let one
    city keep an oversized share of a shrunken total.

    A position that falls below the venue minimum is dropped rather than
    rounded up, because the alternative is quietly breaching the limit that
    just cut it.
    """
    detail = {"per_city_cut": 0, "gross_scale": 1.0, "dropped_min": 0,
              "before_usd": sum(max(0.0, v) for v in proposed.values()),
              "after_usd": 0.0}
    out = {}
    for key, usd in proposed.items():
        usd = max(0.0, float(usd or 0.0))
        if usd <= 0:
            continue
        if usd > limits.per_city_cap_usd:
            usd = limits.per_city_cap_usd
            detail["per_city_cut"] += 1
        out[key] = usd

    ceiling = min(limits.gross_cap_usd, limits.daily_remaining_usd)
    gross = sum(out.values())
    if gross > ceiling and gross > 0:
        scale = ceiling / gross
        detail["gross_scale"] = scale
        out = {k: v * scale for k, v in out.items()}

    final = {}
    for k, v in out.items():
        if v < min_notional_usd:
            detail["dropped_min"] += 1
            continue
        final[k] = v
    detail["after_usd"] = sum(final.values())
    return final, detail
