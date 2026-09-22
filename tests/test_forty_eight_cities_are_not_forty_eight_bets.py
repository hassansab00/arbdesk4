"""The three limits above the ladder: drawdown, correlation, and one day's spend.

allocator.allocate() sizes ONE ladder correctly and has never seen a second
one. These tests are about what it cannot know: that the desk is down 20% on
the month, that Chicago and Toronto miss together, and that a single morning
can commit the whole book before a single settlement reports back.

The drawdown control is a derived formula, not a chosen curve, so the first
tests check it against the one value of it everybody already knows - a
full-Kelly bettor's chance of ever halving their bankroll is one half. If that
does not come out, nothing built on it is worth testing.
"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import risk_budget as rb


# ---------------------------------------------------------------------------
# The derivation.
# ---------------------------------------------------------------------------
def test_a_full_kelly_bettor_halves_their_bankroll_half_the_time():
    """The classical Kelly result, and the anchor for everything below.

    P(ever reaching b) = b^(2/lambda - 1). At lambda = 1 the exponent is 1 and
    the probability is exactly b. If this drifts, the formula has been
    mistyped and every drawdown scale computed from it is wrong.
    """
    assert rb.ruin_probability(1.0, 0.5) == pytest.approx(0.5)
    assert rb.ruin_probability(1.0, 0.25) == pytest.approx(0.25)
    assert rb.ruin_probability(1.0, 0.9) == pytest.approx(0.9)


def test_a_quarter_kelly_bettor_almost_never_does():
    """Exponent 2/0.25 - 1 = 7, so the chance of ever halving is 0.5^7."""
    assert rb.ruin_probability(0.25, 0.5) == pytest.approx(0.5 ** 7)


def test_less_risk_is_always_less_ruin():
    ps = [rb.ruin_probability(lam, 0.6) for lam in (0.1, 0.25, 0.5, 0.75, 1.0)]
    assert ps == sorted(ps), ps


def test_betting_twice_the_kelly_stake_is_certain_ruin():
    """At lambda = 2 the drift term vanishes and the drift is no longer positive.

    Kept explicit because it is the boundary the inverse has to respect: no
    risk appetite, however relaxed, may return a fraction at or above it.
    """
    assert rb.ruin_probability(2.0, 0.5) == 1.0
    assert rb.ruin_probability(3.0, 0.5) == 1.0


def test_the_inverse_undoes_the_formula():
    for b in (0.5, 0.7, 0.85, 0.95):
        for target in (0.01, 0.05, 0.10, 0.25):
            lam = rb.kelly_for_risk_appetite(b, target)
            assert rb.ruin_probability(lam, b) == pytest.approx(target, rel=1e-9)


def test_a_level_at_or_above_current_wealth_leaves_nothing_to_risk():
    assert rb.kelly_for_risk_appetite(1.0) == 0.0
    assert rb.kelly_for_risk_appetite(1.4) == 0.0


def test_b_must_be_a_level_below_where_wealth_stands():
    with pytest.raises(ValueError):
        rb.ruin_probability(0.25, 1.0)
    with pytest.raises(ValueError):
        rb.ruin_probability(0.25, 0.0)


# ---------------------------------------------------------------------------
# 1. Drawdown.
# ---------------------------------------------------------------------------
def test_at_the_high_water_mark_the_existing_behaviour_binds():
    """The defaults allow 0.268 Kelly at the top against a base of 0.25.

    That ordering is deliberate: this control must do nothing at the high-water
    mark and take over as the desk falls, so it can be switched on without
    re-sizing a desk that is not in trouble.
    """
    scale, why = rb.drawdown_scale(100_000, 100_000, 0.25)
    assert scale == 1.0
    assert "tighter" in why


def test_risk_comes_off_smoothly_as_the_desk_falls():
    scales = [rb.drawdown_scale(100_000 * (1 - d), 100_000, 0.25)[0]
              for d in (0.0, 0.05, 0.10, 0.20, 0.28)]
    assert scales == sorted(scales, reverse=True), scales
    assert all(0.0 <= s <= 1.0 for s in scales)
    assert scales[0] == 1.0 and scales[-1] < 0.15


def test_at_the_floor_the_desk_stops_opening_risk():
    scale, why = rb.drawdown_scale(70_000, 100_000, 0.25)
    assert scale == 0.0
    assert "no new risk" in why


def test_below_the_floor_it_does_not_come_back():
    assert rb.drawdown_scale(40_000, 100_000, 0.25)[0] == 0.0


def test_the_control_can_only_take_risk_off():
    """A risk control that can ADD risk is a different thing wearing the name."""
    for equity in (50_000, 100_000, 180_000):
        for base in (0.05, 0.25, 0.5):
            assert rb.drawdown_scale(equity, 100_000, base)[0] <= 1.0


def test_a_desk_with_no_history_is_left_alone():
    assert rb.drawdown_scale(100_000, 0, 0.25)[0] == 1.0
    assert rb.drawdown_scale(100_000, None, 0.25)[0] == 1.0


def test_equity_above_the_high_water_mark_is_not_a_drawdown():
    assert rb.drawdown_scale(140_000, 100_000, 0.25)[0] == 1.0


# ---------------------------------------------------------------------------
# 2. Correlation.
# ---------------------------------------------------------------------------
def test_uncorrelated_cities_count_as_themselves():
    w = {"a": 1.0, "b": 1.0, "c": 1.0, "d": 1.0}
    corr = {(x, y): 0.0 for x in w for y in w if x != y}
    assert rb.effective_independent_bets(w, corr, default=0.0) == pytest.approx(4.0)


def test_perfectly_correlated_cities_count_as_one():
    w = {"a": 1.0, "b": 1.0, "c": 1.0}
    corr = {(x, y): 1.0 for x in w for y in w if x != y}
    assert rb.effective_independent_bets(w, corr, default=1.0) == pytest.approx(1.0)


def test_real_correlation_lands_between_the_two():
    w = {"chicago": 1.0, "toronto": 1.0}
    n = rb.effective_independent_bets(w, {("chicago", "toronto"): 0.6}, default=0.6)
    assert 1.0 < n < 2.0
    assert n == pytest.approx(4.0 / 3.2)


def test_an_unmeasured_pair_is_not_an_independent_pair():
    """"We have not measured it" and "they are independent" are different
    statements, and only one of them is safe to size on."""
    w = {"a": 1.0, "b": 1.0}
    assumed = rb.effective_independent_bets(w, {}, default=rb.DEFAULT_CORRELATION)
    independent = rb.effective_independent_bets(w, {}, default=0.0)
    assert assumed < independent


def test_a_negative_correlation_is_not_a_licence_to_size_up():
    """Two cities that missed opposite ways for 300 days may still miss the
    same way tomorrow. The floor keeps a measured -0.8 from manufacturing
    diversification that a single shared front would erase."""
    w = {"a": 1.0, "b": 1.0}
    n = rb.effective_independent_bets(w, {("a", "b"): -0.8}, default=0.0)
    assert n == pytest.approx(2.0)


def test_the_count_is_symmetric_in_the_pair():
    w = {"a": 1.0, "b": 1.0}
    assert (rb.effective_independent_bets(w, {("a", "b"): 0.7}, default=0.0)
            == rb.effective_independent_bets(w, {("b", "a"): 0.7}, default=0.0))


def test_an_empty_book_has_no_bets_in_it():
    assert rb.effective_independent_bets({}, {}) == 0.0
    assert rb.effective_independent_bets({"a": 0.0}, {}) == 0.0


def test_weight_matters_not_just_count():
    """One big position among four small ones is close to one bet."""
    corr = {}
    even = rb.effective_independent_bets({"a": 1, "b": 1, "c": 1, "d": 1}, corr, default=0.0)
    lopsided = rb.effective_independent_bets({"a": 100, "b": 1, "c": 1, "d": 1}, corr, default=0.0)
    assert lopsided < even
    assert lopsided < 1.2


# ---------------------------------------------------------------------------
# 3. The three together.
# ---------------------------------------------------------------------------
def _limits(**kw):
    base = dict(bankroll=100_000.0, equity=100_000.0, high_water=100_000.0,
                proposed_by_city={"a": 3000.0, "b": 3000.0}, corr={},
                lambda_base=0.25, spent_today_usd=0.0)
    base.update(kw)
    return rb.budget(**base)


def test_one_city_day_cannot_exceed_its_cap():
    limits = _limits()
    final, detail = rb.apply_budget({"a": 90_000.0}, limits)
    assert final["a"] == pytest.approx(limits.per_city_cap_usd)
    assert detail["per_city_cut"] == 1


def test_a_diversified_book_may_carry_more_than_a_concentrated_one():
    spread = _limits(proposed_by_city={c: 1000.0 for c in "abcdefgh"},
                     corr={(x, y): 0.0 for x in "abcdefgh" for y in "abcdefgh" if x != y})
    together = _limits(proposed_by_city={c: 1000.0 for c in "abcdefgh"},
                       corr={(x, y): 1.0 for x in "abcdefgh" for y in "abcdefgh" if x != y})
    assert spread.gross_cap_usd > together.gross_cap_usd
    assert together.n_eff == pytest.approx(1.0)


def test_the_day_budget_bounds_what_one_morning_can_commit():
    limits = _limits(spent_today_usd=19_000.0)
    assert limits.daily_remaining_usd == pytest.approx(1_000.0)
    final, _ = rb.apply_budget({"a": 5_000.0, "b": 5_000.0}, limits)
    assert sum(final.values()) <= 1_000.0 + 1e-9


def test_a_spent_budget_stops_the_desk_entering():
    limits = _limits(spent_today_usd=25_000.0)
    assert limits.daily_remaining_usd == 0.0
    final, _ = rb.apply_budget({"a": 5_000.0}, limits)
    assert final == {}


def test_a_drawn_down_desk_takes_a_smaller_fraction_of_its_edge():
    healthy = _limits()
    hurt = _limits(equity=80_000.0)
    assert hurt.lambda_scale < healthy.lambda_scale


def test_a_drawdown_does_not_move_the_ceilings():
    """The scale belongs to the Kelly fraction, which is a preference about
    how much of an edge to take. A cap is a limit on concentration whatever
    the edge, and a ceiling that moves with the thing it caps is not one.
    Shrinking both would apply the same drawdown twice."""
    healthy = _limits()
    hurt = _limits(equity=80_000.0)
    assert hurt.per_city_cap_usd == healthy.per_city_cap_usd
    assert hurt.gross_cap_usd == healthy.gross_cap_usd


def test_at_the_floor_the_kelly_fraction_is_zero():
    """apply_budget enforces ceilings; the floor acts through the fraction the
    caller solves with, which is what makes it a stop rather than a shrink."""
    limits = _limits(equity=70_000.0)
    assert limits.lambda_scale == 0.0


def test_the_city_cap_binds_before_the_book_wide_ones():
    """Order matters: capping one city first can only reduce what the gross
    cap then has to cut. The other way round leaves one city holding an
    oversized share of a shrunken total."""
    limits = _limits(proposed_by_city={"a": 50_000.0, "b": 1_000.0}, corr={})
    final, detail = rb.apply_budget({"a": 50_000.0, "b": 1_000.0}, limits)
    assert detail["per_city_cut"] == 1
    assert final["a"] <= limits.per_city_cap_usd + 1e-9
    assert final["b"] > 0, "the small position should survive the large one being cut"


def test_a_position_cut_below_the_venue_minimum_is_dropped_not_rounded_up():
    limits = _limits(spent_today_usd=19_996.0)
    final, detail = rb.apply_budget({"a": 5_000.0, "b": 5_000.0}, limits,
                                    min_notional_usd=5.0)
    assert final == {}
    assert detail["dropped_min"] == 2


def test_nothing_proposed_means_nothing_allocated():
    final, detail = rb.apply_budget({}, _limits())
    assert final == {} and detail["after_usd"] == 0.0


def test_every_limit_explains_itself():
    """A position that was cut and cannot say why is a position nobody can
    argue with."""
    limits = _limits(equity=85_000.0, spent_today_usd=4_000.0)
    joined = " ".join(limits.reasons)
    assert "drawdown" in joined
    assert "independent bet" in joined
    assert "entry budget" in joined


def test_the_budget_never_returns_more_than_was_asked_for():
    limits = _limits()
    proposed = {"a": 100.0, "b": 200.0}
    final, _ = rb.apply_budget(proposed, limits)
    for k, v in final.items():
        assert v <= proposed[k] + 1e-9
