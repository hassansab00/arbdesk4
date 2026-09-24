"""The holdings solver, part 1 (plan v2 P5.5)."""
import math
import random

import pytest

import holdings_solver as hs
from allocator import effective_cost


def _ladder(ps, yes, no=None):
    return [{"id": f"b{i}", "p": p, "yes_price": y, "no_price": (no[i] if no else None)}
            for i, (p, y) in enumerate(zip(ps, yes))]


def _random_ladder(rng, n):
    raw = [rng.random() ** 2 for _ in range(n)]
    z = sum(raw)
    ps = [r / z for r in raw]
    # Prices around the probabilities with noise and an overround, so some
    # buckets carry an edge and most do not.
    yes = [min(max(round(p * rng.uniform(0.6, 1.4) + 0.01, 2), 0.01), 0.99) for p in ps]
    return _ladder(ps, yes)


# --------------------------------------------------------------------------
# The closed form is the optimum
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(12))
def test_the_horse_race_closed_form_is_the_numeric_optimum(seed):
    rng = random.Random(seed)
    ladder = _random_ladder(rng, rng.randint(3, 12))
    closed, R = hs.horse_race(ladder)
    numeric = hs.solve(ladder, allow=("YES",))
    for b in ladder:
        assert numeric["weights"].get(f"{b['id']}:YES", 0.0) == pytest.approx(
            closed.get(b["id"], 0.0), abs=2e-4), f"bucket {b['id']} differs"
    assert numeric["cash"] == pytest.approx(R, abs=2e-4)
    names, x = hs.assets(ladder, ("YES",))
    w = [R] + [closed.get(n.split(":")[0], 0.0) for n, *_ in names[1:]]
    assert hs.growth([b["p"] for b in ladder], w, x) == pytest.approx(numeric["growth"], abs=1e-7)


def test_a_worked_horse_race():
    # Two buckets carry an edge after the fee; the third (0.2 at 45c) does not.
    ladder = _ladder([0.5, 0.3, 0.2], [0.40, 0.20, 0.45])
    fr, R = hs.horse_race(ladder)
    c0, c1 = effective_cost(0.40), effective_cost(0.20)
    assert R == pytest.approx((1 - 0.8) / (1 - c0 - c1))
    assert fr == pytest.approx({"b0": 0.5 - R * c0, "b1": 0.3 - R * c1})


def test_a_ladder_that_costs_under_a_dollar_is_bought_whole():
    # 40c + 20c + 30c plus fees is 93c for a sure dollar: Kelly spends
    # everything, p_i on each bucket, and keeps no cash.
    fr, R = hs.horse_race(_ladder([0.5, 0.3, 0.2], [0.40, 0.20, 0.30]))
    assert R == 0.0 and fr == pytest.approx({"b0": 0.5, "b1": 0.3, "b2": 0.2})


# --------------------------------------------------------------------------
# No edge, no trade
# --------------------------------------------------------------------------

def test_no_trade_when_every_bucket_costs_at_least_its_probability():
    ladder = _ladder([0.5, 0.3, 0.2], [0.50, 0.30, 0.20])     # the fee makes each c > p
    assert hs.horse_race(ladder) == ({}, 1.0)
    got = hs.solve(ladder, allow=("YES",))
    assert got["cash"] == pytest.approx(1.0, abs=1e-6)
    assert got["growth"] == pytest.approx(0.0, abs=1e-9)


def test_no_trade_with_no_shares_either_when_nothing_is_mispriced():
    ps = [0.5, 0.3, 0.2]
    ladder = _ladder(ps, ps, [1 - p for p in ps])
    got = hs.solve(ladder)
    assert got["cash"] == pytest.approx(1.0, abs=1e-5)


# --------------------------------------------------------------------------
# Constraints as allowed assets
# --------------------------------------------------------------------------

def test_a_no_only_strategy_buys_only_no():
    # b2 is overpriced on YES, so NO on it is cheap relative to 1 - p.
    ladder = _ladder([0.5, 0.3, 0.2], [0.50, 0.30, 0.40], [0.52, 0.72, 0.55])
    got = hs.solve(ladder, allow=("NO",))
    assert got["weights"] and all(a.endswith(":NO") for a in got["weights"])
    assert "b2:NO" in got["weights"]


def test_s10_picks_the_growth_bucket_not_the_likely_one():
    ladder = _ladder([0.5, 0.2, 0.3], [0.45, 0.10, 0.35])
    best, g = hs.best_single_bucket(ladder)
    assert best == "b1", "a 20% bucket at 10c grows the ledger more than a 50% bucket at 45c"
    assert g == pytest.approx(hs.single_bucket_growth(0.2, 0.10))
    assert hs.single_bucket_growth(0.5, 0.45) < g
    assert max(ladder, key=lambda b: b["p"])["id"] == "b0"


def test_single_bucket_growth_is_the_kelly_optimum():
    p, q = 0.3, 0.2
    c = effective_cost(q)
    f = (p - c) / (1 - c)
    direct = p * math.log(1 - f + f / c) + (1 - p) * math.log(1 - f)
    assert hs.single_bucket_growth(p, q) == pytest.approx(direct)


# --------------------------------------------------------------------------
# The lock
# --------------------------------------------------------------------------

@pytest.mark.parametrize("seed", range(8))
def test_a_book_that_passes_the_lock_never_loses(seed):
    rng = random.Random(100 + seed)
    ladder = _random_ladder(rng, rng.randint(3, 9))
    total_c = sum(effective_cost(b["yes_price"]) for b in ladder)
    shares = {b["id"]: (10.0, 0.0) for b in ladder}          # equal shares on every bucket
    spent = 10.0 * total_c
    held = hs.lock_holds(ladder, shares, spent)
    assert held == (total_c <= 1.0), "equal shares pay 10 whatever wins; it locks iff they cost <= 10"
    if held:
        for k in ladder:
            assert sum(y if bid == k["id"] else n for bid, (y, n) in shares.items()) - spent >= -1e-9


def test_the_lock_refuses_a_book_with_a_losing_outcome():
    ladder = _ladder([0.5, 0.5], [0.4, 0.4])
    assert not hs.lock_holds(ladder, {"b0": (10.0, 0.0)}, 4.0)


# --------------------------------------------------------------------------
# lambda and the no-trade band (Rule 11: bounded on every use)
# --------------------------------------------------------------------------

def test_lambda_scales_the_kelly_book_and_is_bounded():
    got = hs.solve(_ladder([0.5, 0.3, 0.2], [0.40, 0.20, 0.30]), allow=("YES",))
    q = hs.fractional(got)
    assert q["lambda"] == hs.LAMBDA_PRIOR
    for a, f in got["weights"].items():
        assert q["weights"][a] == pytest.approx(0.25 * f)
    assert hs.fractional(got, 3.0)["lambda"] == hs.LAMBDA_BOUNDS[1]
    assert hs.fractional(got, 0.0)["lambda"] == hs.LAMBDA_BOUNDS[0]


def test_the_no_trade_band():
    assert not hs.worth_trading(0.0015, 0.0)
    assert hs.worth_trading(0.0025, 0.0)
    assert not hs.worth_trading(0.009, 0.0, h=1.0), "h is clipped to 0.01, not taken as given"
    assert hs.worth_trading(0.0006, 0.0, h=0.0), "and floored at 0.0005"


# --------------------------------------------------------------------------
# The finding: averaging growth over posterior draws ignores p_sd
# --------------------------------------------------------------------------

def test_the_draw_average_ignores_p_sd():
    """For a fixed book, mean over draws of sum_k pi_dk log W_k equals
    sum_k (mean pi_k) log W_k. So a solver that maximises the draw average
    gives the same book whatever the spread of the draws - it cannot size an
    uncertain bucket smaller, which is what the plan wants the draws for.
    That is why solve_robust() optimises the worst ALPHA of draws instead."""
    ladder = _ladder([0.5, 0.3, 0.2], [0.40, 0.20, 0.30])
    ps = [b["p"] for b in ladder]
    book = hs.solve(ladder, allow=("YES",))
    names, x = hs.assets(ladder, ("YES",))
    w = [book["cash"]] + [book["weights"].get(n, 0.0) for n, *_ in names[1:]]
    for sd in (0.01, 0.05, 0.15):
        draws = hs.dirichlet_draws(ps, hs.concentration(ps, [sd] * 3), n=400, seed=7)
        mean_pi = [sum(d[k] for d in draws) / len(draws) for k in range(3)]
        avg = sum(hs.growth_by_draw(draws, w, x)) / len(draws)
        assert avg == pytest.approx(hs.growth(mean_pi, w, x), abs=1e-12)
    # The spread across draws is what does change - it is reported, not optimised.
    tight = hs.growth_by_draw(hs.dirichlet_draws(ps, hs.concentration(ps, [0.01] * 3), seed=1), w, x)
    wide = hs.growth_by_draw(hs.dirichlet_draws(ps, hs.concentration(ps, [0.15] * 3), seed=1), w, x)
    sd_of = lambda v: (sum((g - sum(v) / len(v)) ** 2 for g in v) / len(v)) ** 0.5
    assert sd_of(wide) > 5 * sd_of(tight)


def test_the_concentration_matches_the_buckets_sd():
    ps = [0.5, 0.3, 0.2]
    kappa = hs.concentration(ps, [0.05, 0.05, 0.05])
    draws = hs.dirichlet_draws(ps, kappa, n=4000, seed=3)
    for k, p in enumerate(ps):
        v = [d[k] for d in draws]
        m = sum(v) / len(v)
        assert m == pytest.approx(p, abs=0.01)
    # The middle bucket's sd is close to the target the kappa was built from.
    v = [d[1] for d in draws]
    m = sum(v) / len(v)
    assert (sum((a - m) ** 2 for a in v) / len(v)) ** 0.5 == pytest.approx(0.05, abs=0.01)


# --------------------------------------------------------------------------
# The robust objective: the worst ALPHA of posterior draws (Hassan, 24 Sep)
# --------------------------------------------------------------------------

_EDGE = _ladder([0.5, 0.3, 0.2], [0.40, 0.20, 0.45])      # two buckets with an edge


def _risky(r):
    return 1.0 - r["cash"]


def test_the_robust_book_shrinks_as_the_belief_grows_less_sure():
    stakes = [_risky(hs.solve_robust(_EDGE, [sd] * 3, allow=("YES",))) for sd in (0.005, 0.03, 0.08, 0.15)]
    assert stakes == sorted(stakes, reverse=True), stakes
    assert stakes[-1] < 0.2 * stakes[0], "a very unsure belief must be staked far less"


def test_a_sure_belief_is_staked_like_the_mean_book():
    mean_book = _risky(hs.solve(_EDGE, allow=("YES",)))
    assert _risky(hs.solve_robust(_EDGE, [0.005] * 3, allow=("YES",))) == pytest.approx(mean_book, rel=0.05)


def test_alpha_one_is_the_plans_mean_objective():
    """At ALPHA = 1 every draw counts, which is the draw average - the growth
    at the sample-mean ladder. So it must equal solve() on that ladder."""
    ps = [b["p"] for b in _EDGE]
    got = hs.solve_robust(_EDGE, [0.08] * 3, alpha=1.0, allow=("YES",), seed=5)
    draws = hs.dirichlet_draws(ps, hs.concentration(ps, [0.08] * 3), hs.N_DRAWS, 5)
    sample_mean = [sum(d[k] for d in draws) / len(draws) for k in range(3)]
    ref = hs.solve([dict(b, p=q) for b, q in zip(_EDGE, sample_mean)], allow=("YES",))
    for a, f in ref["weights"].items():
        assert got["weights"].get(a, 0.0) == pytest.approx(f, abs=5e-3)


def test_the_robust_book_is_never_worse_on_its_own_objective():
    """The whole claim: on the worst ALPHA of the draws, the robust book grows
    at least as much as the mean-optimal book does."""
    ps = [b["p"] for b in _EDGE]
    sds = [0.08] * 3
    r = hs.solve_robust(_EDGE, sds, allow=("YES",), seed=2)
    names, x = hs.assets(_EDGE, ("YES",))
    draws = hs.dirichlet_draws(ps, hs.concentration(ps, sds), hs.N_DRAWS, 2)
    mean_book = hs.solve(_EDGE, allow=("YES",))
    as_w = lambda b: [b["cash"]] + [b["weights"].get(n, 0.0) for n, *_ in names[1:]]
    robust_worst = hs.worst_mean(hs.growth_by_draw(draws, as_w(r), x), hs.ALPHA_PRIOR)
    mean_worst = hs.worst_mean(hs.growth_by_draw(draws, as_w(mean_book), x), hs.ALPHA_PRIOR)
    assert robust_worst >= mean_worst - 1e-6
    assert robust_worst == pytest.approx(r["growth"])


def test_no_edge_is_still_no_trade_however_sure():
    ladder = _ladder([0.5, 0.3, 0.2], [0.50, 0.30, 0.20])
    assert _risky(hs.solve_robust(ladder, [0.01] * 3, allow=("YES",))) == pytest.approx(0.0, abs=1e-3)


def test_alpha_is_bounded_and_the_answer_reproducible():
    assert hs.solve_robust(_EDGE, [0.05] * 3, alpha=0.0, allow=("YES",))["alpha"] == hs.ALPHA_BOUNDS[0]
    assert hs.solve_robust(_EDGE, [0.05] * 3, alpha=7.0, allow=("YES",))["alpha"] == hs.ALPHA_BOUNDS[1]
    a = hs.solve_robust(_EDGE, [0.05] * 3, allow=("YES",), seed=9)
    b = hs.solve_robust(_EDGE, [0.05] * 3, allow=("YES",), seed=9)
    assert a == b
