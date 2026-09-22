"""How much to stake, solved across the ladder instead of guessed per band.

WHAT WAS THERE BEFORE. paper_engine.kelly_fraction() computes the textbook
single-bet Kelly fraction and is used for ONE thing: a warning that a
requested size exceeds what the maths supports. Nothing sizes anything with
it, and it answers the wrong question anyway - it treats one band as an
isolated coin flip when a daily-high ladder is eleven MUTUALLY EXCLUSIVE
outcomes that between them are certain. Exactly one band wins. Sizing each
independently double-counts the bankroll, because the money behind band 4 is
the same money as the money behind band 5 and only one of them can pay.

THE RIGHT PROBLEM. Choose stakes f_i (fractions of bankroll) on a set S of
bands to maximise the expected log of wealth:

    E[log W] = sum_{i in S} p_i log(1 - F + f_i / c_i)
             + (1 - sum_{i in S} p_i) log(1 - F),        F = sum f_i

where p_i is the probability band i wins and c_i is what one dollar of payout
costs there. This is the horse-race Kelly problem and it has a closed form
(Kelly 1956; Smoczynski & Tomkins 2010). With the optimal set S,

    sigma = (1 - sum_{i in S} p_i) / (1 - sum_{i in S} c_i)
    f_i   = p_i - sigma * c_i

and S is found by sorting on p_i / c_i and dropping the worst band until every
survivor clears sigma. For a single band that reduces to (p - c) / (1 - c),
the textbook answer, which is the test that keeps this honest.

WHY IT MATTERS HERE RATHER THAN IN GENERAL. The venue quotes a median 5.7 of
a market's ~11 bands, so sum(p) over what is quoted is well under 1 and the
unquoted remainder is a real probability of losing every stake. The formula
carries that term explicitly - it is the (1 - sum p_i) log(1 - F) piece - and
per-band sizing has nowhere to put it.

FOUR THINGS THE MATHS DOES NOT KNOW, applied after it:

  FEES.       The venue charges 0.05 * q * (1 - q) and you pay it to get in,
              so the cost of a dollar of payout is q + fee, not q. Passed in
              as the effective cost, because cost_model owns that curve.
  ESTIMATION. Full Kelly is optimal only if p is right. Ours is not: the
              market's top two bands hold the winner 26.1% of the time and the
              model's top two 15.3%. Fractional Kelly - a fixed lambda, 0.25
              by default - is the standard discount for that and it is the
              difference between a growth-optimal bet and a ruinous one.
  LIQUIDITY.  A fraction that cannot be filled is not a position. Each stake
              is clipped to the depth actually resting on that band.
  THE FLOOR.  The venue will not accept under $5 notional, so a stake that
              rounds below it is dropped rather than quietly shrunk to zero
              and reported as taken.

NOTHING HERE DECIDES WHETHER TO TRADE. It answers "given these probabilities
and these prices, how should the money be spread" - the strategies still say
which bands are candidates, and the gates still say whether the day is
tradeable at all.
"""

from dataclasses import dataclass, field


VENUE_FEE_RATE = 0.05          # cost_model's curve: fee = rate * q * (1 - q)
VENUE_MIN_NOTIONAL_USD = 5.0
DEFAULT_KELLY_FRACTION = 0.25


def effective_cost(price, fee_rate=VENUE_FEE_RATE):
    """What one dollar of payout costs, fee included.

    The fee is charged on the way in, so it is part of the price for sizing
    purposes even though the venue reports the two separately.
    """
    if price is None or not (0.0 < price < 1.0):
        return None
    return price + fee_rate * price * (1.0 - price)


def blend_probability(model_prob, market_prob, weight):
    """Shrink the model toward the price by how much the model has earned.

    weight is how much of the model to keep, 0 to 1. At 0 this returns the
    market's own implied probability, which is the correct default for a city
    or a lead where the model has never beaten it - and it is the honest
    answer to a measurement that says the market's ranking is better than
    ours. Nothing is gained by pretending otherwise and then sizing on it.
    """
    if model_prob is None:
        return market_prob
    if market_prob is None:
        return model_prob
    w = min(1.0, max(0.0, float(weight if weight is not None else 0.0)))
    return w * float(model_prob) + (1.0 - w) * float(market_prob)


@dataclass
class Leg:
    """One band considered for a stake."""
    band_id: str
    prob: float                     # probability this band wins
    price: float                    # quoted cost per contract, fee EXCLUSIVE
    depth_usd: float = None         # what can actually be filled here
    label: str = None

    cost: float = field(init=False, default=None)

    def __post_init__(self):
        self.cost = effective_cost(self.price)


@dataclass
class Stake:
    band_id: str
    label: str
    fraction: float                 # of bankroll, AFTER the Kelly fraction
    usd: float
    prob: float
    price: float
    cost: float
    edge_per_dollar: float          # (prob / cost) - 1, before sizing
    capped_by: str = None           # 'liquidity' | None


def _optimal_set(legs):
    """The bands worth any stake at all, and their reservation value.

    Sort by prob/cost descending and take the longest prefix in which every
    member still clears the reservation value computed FROM that prefix. The
    loop shortens rather than grows because adding a band changes sigma for
    the ones already in, so a band that qualified against a shorter prefix can
    stop qualifying against a longer one.
    """
    usable = [l for l in legs
              if l.cost is not None and l.prob is not None and l.cost < 1.0]
    usable.sort(key=lambda l: l.prob / l.cost, reverse=True)

    k = len(usable)
    while k > 0:
        chosen = usable[:k]
        sum_p = sum(l.prob for l in chosen)
        sum_c = sum(l.cost for l in chosen)
        if sum_c >= 1.0:
            # Staking every band would cost more than the dollar it can pay:
            # that is the venue's overround, and the answer is to take fewer.
            k -= 1
            continue
        sigma = (1.0 - sum_p) / (1.0 - sum_c)
        if all(l.prob / l.cost > sigma for l in chosen):
            return chosen, sigma
        k -= 1
    return [], None


def allocate(legs, bankroll, kelly_fraction=DEFAULT_KELLY_FRACTION,
             min_notional_usd=VENUE_MIN_NOTIONAL_USD, max_total_fraction=1.0):
    """Stakes across one ladder. Returns (stakes, detail).

    `legs` are the candidate bands of ONE market-day. Passing bands from two
    different days through one call is a modelling error: they are not
    mutually exclusive, and the formula's (1 - sum p) term would be nonsense.
    """
    detail = {"considered": len(legs), "chosen": 0, "sigma": None,
              "gross_fraction": 0.0, "reason": None}

    if not legs or not bankroll or bankroll <= 0:
        detail["reason"] = "no bankroll or no candidate bands"
        return [], detail

    chosen, sigma = _optimal_set(legs)
    detail["sigma"] = sigma
    if not chosen:
        detail["reason"] = ("no band's probability beats its cost by enough to "
                            "be worth bankroll - the ladder is priced above our view")
        return [], detail

    lam = min(1.0, max(0.0, float(kelly_fraction)))
    stakes = []
    for l in chosen:
        full = l.prob - sigma * l.cost          # full-Kelly fraction
        if full <= 0:
            continue
        frac = full * lam
        usd = frac * bankroll
        capped = None
        if l.depth_usd is not None and usd > l.depth_usd:
            usd = float(l.depth_usd)
            frac = usd / bankroll
            capped = "liquidity"
        if usd < min_notional_usd:
            continue                             # the venue will not take it
        stakes.append(Stake(
            band_id=l.band_id, label=l.label, fraction=frac, usd=usd,
            prob=l.prob, price=l.price, cost=l.cost,
            edge_per_dollar=(l.prob / l.cost) - 1.0, capped_by=capped))

    # The whole-ladder cap. Fractional Kelly already keeps the gross well
    # under 1, but a bankroll small enough that the minimum notional dominates
    # can push it up, and staking more than you have is not a rounding error.
    gross = sum(s.fraction for s in stakes)
    if gross > max_total_fraction and gross > 0:
        scale = max_total_fraction / gross
        rescaled = []
        for s in stakes:
            s.fraction *= scale
            s.usd *= scale
            if s.usd >= min_notional_usd:
                rescaled.append(s)
        stakes = rescaled
        gross = sum(s.fraction for s in stakes)

    detail["chosen"] = len(stakes)
    detail["gross_fraction"] = gross
    if not stakes:
        detail["reason"] = (f"every qualifying band sized under the venue's "
                            f"${min_notional_usd:g} minimum")
    return stakes, detail


def expected_log_growth(stakes, legs):
    """E[log wealth] for a set of stakes, which is what allocate() maximises.

    Kept because a sizing rule nobody can score is a sizing rule nobody can
    challenge: a candidate allocation can be compared against this directly.
    The residual term is the probability that none of the staked bands wins,
    which on this venue is most of the ladder most of the time.
    """
    import math
    taken = {s.band_id: s for s in stakes}
    F = sum(s.fraction for s in stakes)
    if F >= 1.0:
        return float("-inf")
    total = 0.0
    staked_p = 0.0
    for l in legs:
        s = taken.get(l.band_id)
        if s is None or l.prob is None:
            continue
        staked_p += l.prob
        total += l.prob * math.log(1.0 - F + s.fraction / s.cost)
    residual = 1.0 - staked_p
    if residual > 0:
        total += residual * math.log(1.0 - F)
    return total
