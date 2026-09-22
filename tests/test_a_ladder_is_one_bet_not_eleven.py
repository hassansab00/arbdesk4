"""Sizing solved across the ladder, instead of guessed one band at a time.

A daily-high market is eleven MUTUALLY EXCLUSIVE outcomes that between them
are certain: exactly one band wins. paper_engine.kelly_fraction() answers the
single-bet question - and it is used for one thing, warning that a requested
size exceeds what the maths supports. Nothing sizes anything with it.

Sizing each band independently double-counts the bankroll. The money behind
band 4 is the same money as the money behind band 5, and only one of them can
pay. Summing eleven independent Kelly fractions can exceed the whole bankroll
while every individual number looks conservative.

THE CORRECT OBJECT is expected log wealth over the whole ladder,

    E[log W] = sum_{i in S} p_i log(1 - F + f_i / c_i)
             + (1 - sum_{i in S} p_i) log(1 - F)

which is the horse-race Kelly problem, solved by a reservation value
sigma = (1 - sum p) / (1 - sum c) over the optimal set. For one band it
collapses to (p - c) / (1 - c), the textbook fraction, and that equivalence is
tested below because it is the cheapest way to catch an algebra slip.

THE RESIDUAL TERM IS THE POINT ON THIS VENUE. Polymarket quotes a median 5.7
of a market's ~11 bands, so the probability that NONE of the quoted bands wins
is large and real. It appears in the formula as (1 - sum p) log(1 - F). Per-band
sizing has nowhere to put it, so it prices that risk at zero.

WHAT IS APPLIED ON TOP, and why each is outside the maths rather than inside:

  fees        0.05 * q * (1-q), charged to get in, so a dollar of payout costs
              q + fee. Sizing on q alone overstates every edge.
  lambda      Full Kelly is optimal only if p is right, and ours is not: the
              market's top two bands hold the winner 26.1% of the time against
              the model's 15.3%. Quarter-Kelly by default.
  depth       A fraction that cannot be filled is not a position.
  $5          The venue's minimum notional. A stake under it is dropped, not
              silently shrunk to zero and reported as taken.
"""

import math

import pytest

from allocator import (Leg, Stake, allocate, blend_probability, effective_cost,
                       expected_log_growth, DEFAULT_KELLY_FRACTION)
import paper_engine


BANK = 10_000.0


# --------------------------------------------------------------------------
# 1. It agrees with the textbook where the textbook applies.
# --------------------------------------------------------------------------

def test_one_band_is_the_single_bet_kelly_fraction():
    leg = Leg("b", prob=0.60, price=0.40)
    stakes, _ = allocate([leg], BANK, kelly_fraction=1.0)
    c = effective_cost(0.40)
    assert stakes[0].fraction == pytest.approx((0.60 - c) / (1.0 - c), rel=1e-9)


def test_one_band_matches_the_engines_own_kelly_once_the_fee_is_removed():
    # paper_engine.kelly_fraction is fee-blind. Feed the allocator a zero fee
    # and the two must agree, or one of them is wrong.
    import allocator
    leg = Leg("b", prob=0.7, price=0.5)
    leg.cost = allocator.effective_cost(0.5, fee_rate=0.0)
    stakes, _ = allocate([leg], BANK, kelly_fraction=1.0)
    assert stakes[0].fraction == pytest.approx(
        paper_engine.kelly_fraction(0.7, 0.5), rel=1e-9)


def test_a_band_priced_above_its_probability_gets_nothing():
    stakes, detail = allocate([Leg("b", prob=0.30, price=0.40)], BANK)
    assert stakes == []
    assert "priced above our view" in detail["reason"]


# --------------------------------------------------------------------------
# 2. It actually maximises what it claims to maximise.
# --------------------------------------------------------------------------

def _ladder():
    # Six quoted bands of an eleven-band day. sum(p) = 0.62: the missing 0.38
    # is the unquoted rest of the ladder, and it is a real way to lose.
    return [
        Leg("b1", prob=0.05, price=0.08),
        Leg("b2", prob=0.12, price=0.11),
        Leg("b3", prob=0.25, price=0.20),
        Leg("b4", prob=0.14, price=0.17),
        Leg("b5", prob=0.04, price=0.09),
        Leg("b6", prob=0.02, price=0.06),
    ]


def test_the_allocation_beats_every_perturbation_of_itself():
    legs = _ladder()
    stakes, _ = allocate(legs, BANK, kelly_fraction=1.0, min_notional_usd=0.0)
    assert stakes, "the ladder has bands priced under their probability"
    base = expected_log_growth(stakes, legs)

    for i in range(len(stakes)):
        for step in (0.8, 0.9, 1.1, 1.25):
            moved = [Stake(**{**s.__dict__}) for s in stakes]
            moved[i].fraction *= step
            assert expected_log_growth(moved, legs) <= base + 1e-12, (
                f"moving stake {i} by {step} improved expected log growth - "
                "the solved allocation is not the optimum"
            )


def test_dropping_a_chosen_band_does_not_improve_growth():
    legs = _ladder()
    stakes, _ = allocate(legs, BANK, kelly_fraction=1.0, min_notional_usd=0.0)
    base = expected_log_growth(stakes, legs)
    for i in range(len(stakes)):
        without = [s for j, s in enumerate(stakes) if j != i]
        assert expected_log_growth(without, legs) <= base + 1e-12


def test_adding_a_rejected_band_does_not_improve_growth():
    legs = _ladder()
    stakes, _ = allocate(legs, BANK, kelly_fraction=1.0, min_notional_usd=0.0)
    taken = {s.band_id for s in stakes}
    rejected = [l for l in legs if l.band_id not in taken]
    base = expected_log_growth(stakes, legs)
    for l in rejected:
        for f in (0.001, 0.01, 0.05):
            trial = list(stakes) + [Stake(l.band_id, l.label, f, f * BANK,
                                          l.prob, l.price, l.cost,
                                          (l.prob / l.cost) - 1.0)]
            assert expected_log_growth(trial, legs) <= base + 1e-12, (
                f"{l.band_id} was excluded but staking it improves growth"
            )


# --------------------------------------------------------------------------
# 3. The double-count it exists to remove.
# --------------------------------------------------------------------------

def test_the_ladder_answer_grows_faster_than_independent_kelly():
    """The invariant is growth, not size, and the direction is not obvious.

    The first version of this test asserted the ladder stakes LESS in total
    than summing per-band Kelly fractions - on the reasoning that mutually
    exclusive bands double-count the bankroll. It fails, and the premise is
    the thing that is wrong, not the allocator: because exactly one band can
    win, stakes on different bands HEDGE each other. Spreading across three
    bands bounds the downside in a way three isolated bets do not, so the
    growth-optimal total can legitimately be larger. Measured on the ladder
    below it is 0.0696 against 0.0588.

    So the comparison that means something is expected log growth, which is
    what either allocation is trying to maximise.
    """
    legs = _ladder()
    stakes, _ = allocate(legs, BANK, kelly_fraction=1.0, min_notional_usd=0.0)

    independent = []
    for l in legs:
        f = (l.prob - l.cost) / (1.0 - l.cost)
        if f > 0:
            independent.append(Stake(l.band_id, l.label, f, f * BANK, l.prob,
                                     l.price, l.cost, (l.prob / l.cost) - 1.0))

    assert expected_log_growth(stakes, legs) > expected_log_growth(independent, legs), (
        "solving the ladder must beat sizing its bands one at a time, or "
        "there is no reason for this module to exist"
    )


def test_independent_kelly_is_a_different_allocation_not_a_rescaling():
    legs = _ladder()
    stakes, _ = allocate(legs, BANK, kelly_fraction=1.0, min_notional_usd=0.0)
    by_id = {s.band_id: s.fraction for s in stakes}
    ratios = [by_id[l.band_id] / ((l.prob - l.cost) / (1.0 - l.cost))
              for l in legs
              if l.band_id in by_id and (l.prob - l.cost) / (1.0 - l.cost) > 0]
    assert len(set(round(r, 6) for r in ratios)) > 1, (
        "if every band's stake were the same multiple of its independent "
        "Kelly fraction, the ladder solution would be cosmetic"
    )


def test_the_unquoted_remainder_is_priced():
    # Same prices, two different views of how much probability the quoted
    # bands hold. The ladder that leaves more unquoted must stake less: the
    # chance that none of them wins is the risk per-band sizing cannot see.
    thin = [Leg("a", 0.20, 0.15), Leg("b", 0.15, 0.12)]
    fat  = [Leg("a", 0.45, 0.15), Leg("b", 0.40, 0.12)]
    s_thin, _ = allocate(thin, BANK, kelly_fraction=1.0, min_notional_usd=0.0)
    s_fat, _  = allocate(fat,  BANK, kelly_fraction=1.0, min_notional_usd=0.0)
    assert sum(s.fraction for s in s_thin) < sum(s.fraction for s in s_fat)


def test_an_overround_ladder_with_no_view_is_left_alone():
    # Every band priced at its probability, plus the venue's take. There is
    # nothing here and the allocator must say so rather than spread evenly.
    legs = [Leg(f"b{i}", prob=0.10, price=0.125) for i in range(8)]
    stakes, detail = allocate(legs, BANK, kelly_fraction=1.0)
    assert stakes == []
    assert detail["reason"]


# --------------------------------------------------------------------------
# 4. The three things the maths does not know.
# --------------------------------------------------------------------------

def test_the_fee_is_part_of_the_price():
    assert effective_cost(0.40) == pytest.approx(0.40 + 0.05 * 0.40 * 0.60)
    assert effective_cost(0.0) is None and effective_cost(1.0) is None
    assert effective_cost(None) is None


def test_a_band_whose_edge_is_only_the_fee_is_not_taken():
    # 0.40 costs 0.412 once the venue's 0.05 * q * (1-q) is paid, so a 0.405
    # probability is an edge on the screen and a loss on the ticket.
    q = 0.40
    p = 0.405
    assert q < p < effective_cost(q)
    stakes, detail = allocate([Leg("b", p, q)], BANK, kelly_fraction=1.0)
    assert stakes == [], "the fee turned this into a losing bet and it was taken anyway"
    assert "priced above our view" in detail["reason"]


def test_a_stake_is_clipped_to_what_can_be_filled():
    legs = [Leg("b", prob=0.60, price=0.40, depth_usd=120.0)]
    stakes, _ = allocate(legs, BANK, kelly_fraction=1.0)
    assert stakes[0].usd == pytest.approx(120.0)
    assert stakes[0].capped_by == "liquidity"
    assert stakes[0].fraction == pytest.approx(120.0 / BANK)


def test_a_stake_under_the_venue_minimum_is_dropped_not_shrunk():
    legs = [Leg("b", prob=0.60, price=0.40, depth_usd=2.0)]
    stakes, detail = allocate(legs, BANK, kelly_fraction=1.0)
    assert stakes == []
    assert "minimum" in detail["reason"]


def test_the_kelly_fraction_scales_the_whole_ladder():
    legs = _ladder()
    full, _ = allocate(legs, BANK, kelly_fraction=1.0, min_notional_usd=0.0)
    part, _ = allocate(legs, BANK, kelly_fraction=0.25, min_notional_usd=0.0)
    assert sum(s.fraction for s in part) == pytest.approx(
        0.25 * sum(s.fraction for s in full), rel=1e-9)


def test_the_default_is_fractional_not_full_kelly():
    assert 0 < DEFAULT_KELLY_FRACTION < 1, (
        "full Kelly is optimal only if p is right, and the market's top two "
        "bands beat the model's 26.1% to 15.3%"
    )


def test_the_gross_never_exceeds_the_bankroll():
    legs = [Leg(f"b{i}", prob=0.9, price=0.02) for i in range(10)]
    stakes, detail = allocate(legs, BANK, kelly_fraction=1.0)
    assert detail["gross_fraction"] <= 1.0 + 1e-9
    assert sum(s.usd for s in stakes) <= BANK + 1e-6


# --------------------------------------------------------------------------
# 5. Whose probability gets used.
# --------------------------------------------------------------------------

def test_no_trust_in_the_model_means_trade_the_price():
    assert blend_probability(0.80, 0.30, 0.0) == pytest.approx(0.30)


def test_full_trust_keeps_the_model():
    assert blend_probability(0.80, 0.30, 1.0) == pytest.approx(0.80)


def test_a_missing_model_falls_back_to_the_market_not_to_zero():
    assert blend_probability(None, 0.30, 0.9) == pytest.approx(0.30)
    assert blend_probability(0.30, None, 0.9) == pytest.approx(0.30)


def test_the_weight_is_clamped_rather_than_trusted():
    assert blend_probability(0.8, 0.2, 5.0) == pytest.approx(0.8)
    assert blend_probability(0.8, 0.2, -1.0) == pytest.approx(0.2)
    assert blend_probability(0.8, 0.2, None) == pytest.approx(0.2)


# --------------------------------------------------------------------------
# 6. It refuses the modelling error it cannot detect at runtime.
# --------------------------------------------------------------------------

def test_the_docstring_says_one_market_day_per_call():
    import allocator
    assert "mutually exclusive" in allocator.allocate.__doc__, (
        "bands from two days are not mutually exclusive and the (1 - sum p) "
        "term would be nonsense - if it cannot be enforced it must be stated"
    )


def test_an_empty_ladder_is_an_answer_not_a_crash():
    stakes, detail = allocate([], BANK)
    assert stakes == [] and detail["reason"]
    stakes, detail = allocate(_ladder(), 0)
    assert stakes == [] and detail["reason"]


# --------------------------------------------------------------------------
# 7. The per-signal floor under it: Strategy.size().
#
#    It used to be `cap_usd / price` - the same fraction of bankroll on every
#    signal a strategy ever fired. A 5c band with a twenty-point edge and a
#    60c band with a two-point edge got identical money, and the cap was doing
#    all the work.
# --------------------------------------------------------------------------

from strategies.base import Strategy, StrategyConfig, Signal
from paper_engine import Portfolio


class _Probe(Strategy):
    def entry_signals(self, ctx):
        return []

    def exit_signals(self, ctx, open_positions):
        return []


def _probe(cap_pct=5.0):
    return _Probe(StrategyConfig("sX", "probe", "YES", ["ALL"], ["SHARP"],
                                 "directional", cap_pct, 10, True))


def _signal(prob, price):
    return Signal("sX", "b", "YES", "ENTER", "r", price, prob, 0.0, 0.0, 0.0,
                  "SHARP", "info", "k")


def test_sizing_now_depends_on_the_edge():
    s, P = _probe(), Portfolio(bankroll=10_000.0)
    thin = s.size(_signal(0.22, 0.20), P) * 0.20
    fat = s.size(_signal(0.60, 0.40), P) * 0.40
    assert thin < fat, "a two-point edge and a twenty-point edge cannot cost the same"
    assert thin < 0.25 * fat


def test_a_signal_with_no_edge_after_fees_is_not_sized():
    s, P = _probe(), Portfolio(bankroll=10_000.0)
    assert s.size(_signal(0.10, 0.20), P) == 0.0, (
        "the flat cap paid full size for a bet it expected to lose"
    )


def test_the_configured_cap_is_still_a_ceiling():
    s, P = _probe(cap_pct=5.0), Portfolio(bankroll=10_000.0)
    # p=0.60 at 40c wants far more than 5% under Kelly; the limit must hold.
    notional = s.size(_signal(0.60, 0.40), P) * 0.40
    assert notional == pytest.approx(10_000.0 * 0.05), (
        "capital_cap_pct is a risk limit, and a risk limit the maths can talk "
        "its way past is not one"
    )


def test_a_strategy_with_no_model_probability_keeps_the_flat_cap():
    # s2_combination_arb's whole premise is arithmetic on the prices. A Kelly
    # fraction computed from a probability it does not have would be invented.
    s, P = _probe(), Portfolio(bankroll=10_000.0)
    assert s.size(_signal(None, 0.20), P) * 0.20 == pytest.approx(10_000.0 * 0.05)


def test_sizing_uses_the_fee_inclusive_cost():
    s, P = _probe(cap_pct=100.0), Portfolio(bankroll=10_000.0)
    price, prob = 0.40, 0.55
    cost = effective_cost(price)
    expected = 10_000.0 * ((prob - cost) / (1 - cost)) * DEFAULT_KELLY_FRACTION / price
    assert s.size(_signal(prob, price), P) == pytest.approx(expected, rel=1e-9)


def test_no_bankroll_is_no_position():
    s = _probe()
    assert s.size(_signal(0.6, 0.4), Portfolio(bankroll=0.0)) == 0.0
    assert s.size(_signal(0.6, None), Portfolio(bankroll=10_000.0)) == 0.0
