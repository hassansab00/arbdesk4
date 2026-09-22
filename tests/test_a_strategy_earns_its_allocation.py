"""A strategy's share of the bankroll, computed from its own settled record.

WHAT THIS REPLACES. Five strategies were retired by hand on 2026-09-22: the
return per stake on their settled signals was measured, written down, and
decided on. Right decision, wrong mechanism. It took three weeks of losses to
notice, the numbers existed only because somebody went looking, and in a
month it will be a different five.

THE STATISTIC. One number per settled signal - what a dollar committed to it
came back as, net of fees. Those are noisy and there are rarely many, so the
sample mean is the wrong summary: s8 and s9 each had FOUR settled signals,
and four signals is not a record. The mean is shrunk toward zero through a
conjugate normal update,

    posterior_mean = tau^2 * xbar / (tau^2 + se^2)

with the prior centred on ZERO rather than on what other strategies average.
The venue takes ~13.5% of overround out of every quoted ladder and charges a
fee on top, so a strategy with no edge returns slightly less than nothing,
and a new strategy has shown no edge.

THE WEIGHT IS CONFIDENCE, NOT RETURN. Sizing by the posterior mean would give
money to whatever looks best; what a strategy earns here is confidence that
it is above water at all. At P = 0.5 - no evidence either way, where every
strategy starts - the weight is zero.

IT IS NOT A RETIREMENT. A strategy at weight zero keeps firing, keeps being
marked, keeps its place on the board, and can earn its way back. Retiring one
stays a decision a person makes with a reason written down. This is the dial
in between, which did not exist: there was on, and there was off.
"""

import pytest

import strategy_gate as gate


def const(n, value):
    """n signals that each returned `value`, with a little spread.

    Exactly identical returns give zero sample variance and a degenerate
    posterior, which is not a case this ever sees and not one worth special
    handling - so the fixtures carry realistic noise.
    """
    import random
    r = random.Random(4)
    xs = [r.gauss(0, 0.25) for _ in range(n)]
    # Re-centre exactly on `value`. Without this a fixture asking for a
    # break-even record delivers +1.9c of sampling noise and the test ends up
    # measuring the random seed.
    shift = value - sum(xs) / n
    return [x + shift for x in xs]


# --------------------------------------------------------------------------
# 1. No record, no money.
# --------------------------------------------------------------------------

def test_a_strategy_that_has_never_settled_gets_nothing():
    v = gate.assess("s_new", [])
    assert v.weight == 0.0
    assert v.n == 0 and v.thin
    assert "no record" in v.reason


def test_four_lucky_signals_do_not_buy_an_allocation():
    # s8 and s9 shipped with exactly this much evidence, and these four agree
    # closely enough that the normal update alone reports near-certainty.
    v = gate.assess("s8", [0.30, 0.25, 0.40, 0.20])
    assert v.prob_positive > 0.9, "the four do agree - that is the trap"
    assert v.weight <= 0.4 + 1e-9, (
        "four signals must not be treated as a record however well they "
        "agree: a basket strategy emits one per leg, so these can be two "
        "market-days wearing four observations"
    )
    assert v.thin


def test_a_single_signal_does_not_become_a_certainty():
    v = gate.assess("s", [0.5])
    assert v.posterior_sd > 0
    assert v.weight <= 0.1 + 1e-9, (
        "one observation has no width of its own; the prior supplies it, and "
        "the evidence factor stops the result being an allocation"
    )


# --------------------------------------------------------------------------
# 2. The shrinkage does what shrinkage does.
# --------------------------------------------------------------------------

def test_the_posterior_is_pulled_toward_zero():
    v = gate.assess("s", const(40, 0.20))
    assert 0 < v.posterior_mean < v.observed_mean, (
        "a posterior that equals the sample mean is not a posterior"
    )


def test_more_evidence_moves_the_posterior_toward_what_was_observed():
    few = gate.assess("s", const(8, 0.10))
    many = gate.assess("s", const(400, 0.10))
    assert abs(many.posterior_mean - many.observed_mean) < \
           abs(few.posterior_mean - few.observed_mean)


def test_the_prior_is_centred_on_zero():
    v = gate.assess("s", const(200, 0.0))
    assert abs(v.posterior_mean) < 0.02
    assert v.weight == 0.0, (
        "a strategy that breaks even has not shown an edge, and on a venue "
        "with 13.5% of overround breaking even is already lucky"
    )


# --------------------------------------------------------------------------
# 3. It is monotone in both things that should move it.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("worse,better", [(-0.10, -0.02), (-0.02, 0.02), (0.02, 0.10)])
def test_a_better_record_never_earns_less(worse, better):
    a = gate.assess("s", const(120, worse))
    b = gate.assess("s", const(120, better))
    assert b.weight >= a.weight


def test_more_signals_at_the_same_edge_earn_more():
    few = gate.assess("s", const(15, 0.05))
    many = gate.assess("s", const(500, 0.05))
    assert many.weight > few.weight, (
        "confidence is what is being bought, and more of the same evidence "
        "buys more of it"
    )


def test_a_losing_strategy_earns_nothing_however_long_it_runs():
    for n in (20, 200, 2000):
        assert gate.assess("s", const(n, -0.05)).weight == 0.0


# --------------------------------------------------------------------------
# 4. The floor.
# --------------------------------------------------------------------------

def test_the_floor_is_where_the_weight_starts():
    v = gate.assess("s", const(300, 0.05))
    expected = (v.prob_positive - gate.CONFIDENCE_FLOOR) / (1 - gate.CONFIDENCE_FLOOR)
    assert v.weight == pytest.approx(min(1.0, max(0.0, expected)))


def test_the_weight_never_leaves_the_unit_interval():
    for mean, n in ((-5.0, 50), (5.0, 50), (0.0, 3), (0.5, 1000)):
        v = gate.assess("s", const(n, mean))
        assert 0.0 <= v.weight <= 1.0


def test_a_coin_flip_earns_nothing():
    v = gate.assess("s", const(100, 0.0))
    assert v.prob_positive == pytest.approx(0.5, abs=0.25)
    assert v.weight == 0.0


# --------------------------------------------------------------------------
# 5. Sharing one bankroll.
# --------------------------------------------------------------------------

def test_nothing_earned_means_the_money_stays_in_cash():
    vs = gate.weights({"a": const(50, -0.10), "b": const(50, -0.05)})
    shares = gate.normalise(vs)
    assert all(s == 0.0 for s in shares.values()), (
        "spreading a bankroll evenly over strategies that have all failed to "
        "show an edge is not diversification"
    )


def test_shares_sum_to_the_bankroll_when_something_has_earned_it():
    vs = gate.weights({"good": const(400, 0.06), "bad": const(400, -0.06)})
    shares = gate.normalise(vs, total=1.0)
    assert shares["bad"] == 0.0
    assert sum(shares.values()) == pytest.approx(1.0)


def test_the_better_strategy_gets_the_larger_share():
    vs = gate.weights({"ok": const(300, 0.02), "strong": const(300, 0.08)})
    shares = gate.normalise(vs)
    assert shares["strong"] > shares["ok"] > 0


# --------------------------------------------------------------------------
# 6. Against the records that prompted it.
# --------------------------------------------------------------------------

@pytest.mark.parametrize("sid,n,mean", [
    ("s1_buy_low_sell_signal", 738, -0.178),
    ("s3_concentration",       819, -0.091),
    ("s6_anchor_insurance",   1105, -0.204),
    ("s5_running_max_lock",     37, -0.328),
])
def test_every_strategy_this_was_written_for_earns_zero(sid, n, mean):
    v = gate.assess(sid, const(n, mean))
    assert v.weight == 0.0, f"{sid} would still have been given money: {v.reason}"


def test_it_is_a_dial_and_not_a_switch():
    # The module must not be mistaken for retirement: a zero weight has to be
    # recoverable from, or it is just a slower way to delete something.
    assert "not a retirement" in gate.__doc__.lower()
    losing = gate.assess("s", const(60, -0.05))
    recovered = gate.assess("s", const(60, -0.05) + const(600, 0.08))
    assert losing.weight == 0.0 and recovered.weight > 0.0
