"""The ladder solver and the allocation gate, in the path that actually runs.

scripts/allocator.py and scripts/strategy_gate.py are the maths. This is the
wiring, and the wiring is where an unused library hides: both could be
perfect and the desk would go on sizing every signal at a flat 5% of bankroll
without one test failing.

TWO THINGS HAPPEN AFTER THE STRATEGIES HAVE PROPOSED.

  _earned_weights()   reads one return per settled signal out of
                      v_signal_mark - mark_net_per_share over price_at_fire -
                      and asks strategy_gate what share of bankroll each
                      strategy has earned. s1 spent three weeks losing 17.8c
                      on the dollar before anyone measured it; this is the
                      measurement happening every run.

  _allocate_ladders() groups the day's YES entries by (city, resolution_date)
                      and solves each ladder as ONE allocation, because its
                      bands are mutually exclusive. A band the solver does not
                      choose is sized to zero rather than left at whatever the
                      per-signal rule produced.

WHAT IS DELIBERATELY LEFT ALONE. Exits - an exit is not a stake. NO legs - a
NO is a bet on the complement of one band, not on one of the ladder's
mutually exclusive alternatives, and folding it in means modelling 2N
outcomes with constraints between them. That is a different problem and
guessing at it would be worse than leaving those signals on their per-signal
size.

AND IT MUST NOT BECOME A SINGLE POINT OF FAILURE. If v_signal_mark cannot be
read, every strategy keeps full weight and sizing falls back to base.size().
The gate exists to hold money back from strategies that have earned nothing,
not to stop the desk when a view is missing.
"""

import datetime as dt

import pytest

import signal_engine as se
from strategies.base import BandView, Signal


class _P:
    def __init__(self, bankroll=100_000.0):
        self.bankroll = bankroll


def _view(band_id, city="nyc", date="2026-09-22", depth=50_000.0, label="b"):
    return BandView(
        band_id=band_id, city_key=city, resolution_date=date,
        band_lo=20.0, band_hi=21.0, open_low=False, open_high=False,
        band_label=label, unit="C", model_prob_yes=0.3,
        yes_price=0.20, no_price=0.80, yes_edge_net_pp=0.05, no_edge_net_pp=0.0,
        yes_tradeable=True, yes_block_reason=None,
        no_tradeable=True, no_block_reason=None,
        confidence=0.7, regime_label="SHARP", market_state="open",
        fillable_usd_5c_yes=depth)


def _sig(band_id, prob, price, strategy="sA", side="YES", action="ENTER", shares=999.0):
    return Signal(strategy, band_id, side, action, "r", price, prob, 0.0,
                  shares, 0.7, "SHARP", "info", f"{strategy}:{band_id}")


# --------------------------------------------------------------------------
# 1. The weights.
# --------------------------------------------------------------------------

def test_the_return_per_signal_is_net_over_stake(monkeypatch):
    captured = {}

    def fake(view, params, **kw):
        captured["view"] = view
        return [{"strategy_id": "sA", "price_at_fire": 0.20, "mark_net_per_share": 0.04}] * 60

    monkeypatch.setattr(se, "rest_all", fake)
    out = se._earned_weights()
    assert captured["view"] == "v_signal_mark"
    assert out["sA"].observed_mean == pytest.approx(0.20)   # 0.04 / 0.20


def test_a_losing_record_earns_no_allocation(monkeypatch):
    import random
    r = random.Random(7)
    rows = [{"strategy_id": "sLose", "price_at_fire": 0.5,
             "mark_net_per_share": -0.05 + r.gauss(0, 0.05)} for _ in range(200)]
    monkeypatch.setattr(se, "rest_all", lambda *a, **k: rows)
    assert se._earned_weights()["sLose"].weight == 0.0


def test_a_missing_view_does_not_size_the_desk_to_zero(monkeypatch):
    def boom(*a, **k):
        raise RuntimeError("v_signal_mark does not exist")
    monkeypatch.setattr(se, "rest_all", boom)
    assert se._earned_weights() == {}, (
        "an empty map means every strategy keeps full weight - a gate that "
        "stops the desk when a view is missing is worse than no gate"
    )


def test_a_signal_with_no_stake_is_not_a_return(monkeypatch):
    # A return per dollar needs a dollar. None of these three rows carries
    # one, so the strategy contributes no evidence and does not appear - and
    # a strategy that does not appear keeps full weight, because malformed
    # marks are missing data, not a losing record. Its signals are then sized
    # by base.size()'s per-signal Kelly, which is the correct fallback.
    monkeypatch.setattr(se, "rest_all", lambda *a, **k: [
        {"strategy_id": "sA", "price_at_fire": 0.0, "mark_net_per_share": 0.5},
        {"strategy_id": "sA", "price_at_fire": None, "mark_net_per_share": 0.5},
        {"strategy_id": "sA", "price_at_fire": 0.5, "mark_net_per_share": None},
    ])
    assert se._earned_weights() == {}


# --------------------------------------------------------------------------
# 2. The ladder.
# --------------------------------------------------------------------------

def test_a_days_entries_are_solved_together():
    views = [_view("b1"), _view("b2"), _view("b3")]
    fired = [_sig("b1", 0.40, 0.20), _sig("b2", 0.30, 0.18), _sig("b3", 0.02, 0.30)]
    n = se._allocate_ladders(fired, views, _P(), {})
    assert n == 3
    # b3 is priced far above its probability and must lose its size entirely.
    assert dict((s.band_id, s.suggested_shares) for s in fired)["b3"] == 0.0
    assert fired[0].suggested_shares > 0


def test_two_days_are_not_one_ladder():
    # Same city, different resolution dates. Their bands are NOT mutually
    # exclusive, and pooling them would apply the (1 - sum p) term across
    # outcomes that can both happen.
    views = [_view("b1", date="2026-09-22"), _view("b2", date="2026-09-23")]
    fired = [_sig("b1", 0.40, 0.20), _sig("b2", 0.40, 0.20)]
    se._allocate_ladders(fired, views, _P(), {})
    assert fired[0].suggested_shares == pytest.approx(fired[1].suggested_shares), (
        "two identical bands on different days must each be solved as their "
        "own ladder and get the same answer"
    )


def test_pooling_a_day_stakes_less_than_solving_it_alone():
    single = [_view("b1")]
    pooled = [_view("b1"), _view("b2"), _view("b3")]
    f1 = [_sig("b1", 0.40, 0.20)]
    f3 = [_sig("b1", 0.40, 0.20), _sig("b2", 0.35, 0.20), _sig("b3", 0.30, 0.20)]
    se._allocate_ladders(f1, single, _P(), {})
    se._allocate_ladders(f3, pooled, _P(), {})
    assert f3[0].suggested_shares != f1[0].suggested_shares, (
        "a band's size must depend on what else is on its ladder, or the "
        "solver is not being consulted"
    )


def test_an_exit_keeps_the_size_the_strategy_asked_for():
    views = [_view("b1")]
    fired = [_sig("b1", 0.40, 0.20, action="EXIT", shares=777.0)]
    assert se._allocate_ladders(fired, views, _P(), {}) == 0
    assert fired[0].suggested_shares == 777.0


def test_a_no_leg_is_left_on_its_per_signal_size():
    views = [_view("b1")]
    fired = [_sig("b1", 0.40, 0.80, side="NO", shares=555.0)]
    assert se._allocate_ladders(fired, views, _P(), {}) == 0
    assert fired[0].suggested_shares == 555.0


def test_a_signal_with_no_probability_is_left_alone():
    views = [_view("b1")]
    fired = [_sig("b1", None, 0.20, shares=321.0)]
    assert se._allocate_ladders(fired, views, _P(), {}) == 0
    assert fired[0].suggested_shares == 321.0


def test_no_bankroll_changes_nothing():
    views = [_view("b1")]
    fired = [_sig("b1", 0.40, 0.20, shares=111.0)]
    assert se._allocate_ladders(fired, views, _P(bankroll=0.0), {}) == 0
    assert fired[0].suggested_shares == 111.0


def test_depth_limits_what_the_ladder_hands_out():
    deep = [_view("b1", depth=1_000_000.0)]
    thin = [_view("b1", depth=25.0)]
    fd = [_sig("b1", 0.40, 0.20)]
    ft = [_sig("b1", 0.40, 0.20)]
    se._allocate_ladders(fd, deep, _P(), {})
    se._allocate_ladders(ft, thin, _P(), {})
    assert ft[0].suggested_shares * 0.20 <= 25.0 + 1e-9
    assert fd[0].suggested_shares > ft[0].suggested_shares


# --------------------------------------------------------------------------
# 3. The weight and the ladder together.
# --------------------------------------------------------------------------

def test_a_strategy_that_has_earned_nothing_is_sized_to_nothing():
    views = [_view("b1")]
    fired = [_sig("b1", 0.40, 0.20, strategy="sBroke")]
    earned = {"sBroke": se.strategy_gate.assess("sBroke", [-0.2] * 50)}
    assert earned["sBroke"].weight == 0.0
    se._allocate_ladders(fired, views, _P(), earned)
    assert fired[0].suggested_shares == 0.0, (
        "the gate has to reach the size, not just the log line"
    )


def test_a_half_trusted_strategy_gets_half_the_ladders_answer():
    views = [_view("b1")]
    full = [_sig("b1", 0.40, 0.20, strategy="sA")]
    half = [_sig("b1", 0.40, 0.20, strategy="sA")]

    class _W:
        weight = 0.5

    se._allocate_ladders(full, views, _P(), {})
    se._allocate_ladders(half, views, _P(), {"sA": _W()})
    assert half[0].suggested_shares == pytest.approx(0.5 * full[0].suggested_shares)


def test_an_unrecorded_strategy_keeps_full_weight():
    views = [_view("b1")]
    a = [_sig("b1", 0.40, 0.20, strategy="sKnown")]
    b = [_sig("b1", 0.40, 0.20, strategy="sUnknown")]
    se._allocate_ladders(a, views, _P(), {})
    se._allocate_ladders(b, views, _P(), {"sOther": None})
    assert b[0].suggested_shares == pytest.approx(a[0].suggested_shares)


def test_two_strategies_on_one_band_stake_it_once():
    # The band is one position however many strategies want it, and the
    # better-earning strategy's claim is the one that stands.
    views = [_view("b1")]

    class _W:
        def __init__(self, w):
            self.weight = w

    fired = [_sig("b1", 0.40, 0.20, strategy="sLow"),
             _sig("b1", 0.40, 0.20, strategy="sHigh")]
    earned = {"sLow": _W(0.2), "sHigh": _W(0.9)}
    se._allocate_ladders(fired, views, _P(), earned)
    sized = [s for s in fired if s.suggested_shares > 0]
    assert len(sized) == 1 and sized[0].strategy_id == "sHigh"


# ---------------------------------------------------------------------------
# The three risk limits, in the path that actually runs.
#
# scripts/risk_budget.py is the arithmetic and has its own tests. These are
# about the wiring: a perfect budget module that _allocate_ladders never calls
# would leave the desk sizing forty-eight independent quarter-Kelly ladders
# with nothing above them, and not one test in that file would fail.
# ---------------------------------------------------------------------------
def _no_risk():
    """The state before any of this existed: no history, nothing spent."""
    return (None, None, 0.0)


def test_one_weather_event_cannot_take_more_than_its_cap():
    import risk_budget as rb
    sigs = [_sig("b1", 0.40, 0.20)]
    se._allocate_ladders(sigs, [_view("b1")], _P(), {}, _no_risk(), {})
    usd = sigs[0].suggested_shares * 0.20
    assert usd <= 100_000.0 * rb.PER_CITY_DAY_CAP_PCT / 100.0 + 1e-6


def test_a_drawn_down_desk_stakes_less_on_the_same_ladder():
    healthy = [_sig("b1", 0.40, 0.20)]
    hurt = [_sig("b1", 0.40, 0.20)]
    se._allocate_ladders(healthy, [_view("b1")], _P(), {}, (100_000.0, 100_000.0, 0.0), {})
    se._allocate_ladders(hurt, [_view("b1")], _P(), {}, (80_000.0, 100_000.0, 0.0), {})
    assert hurt[0].suggested_shares < healthy[0].suggested_shares


def test_at_the_drawdown_floor_nothing_is_funded_but_the_signal_still_fires():
    """A stop, not a silence. The day goes on the record as one the desk
    declined to fund rather than one it never looked at."""
    sigs = [_sig("b1", 0.40, 0.20)]
    se._allocate_ladders(sigs, [_view("b1")], _P(), {}, (70_000.0, 100_000.0, 0.0), {})
    assert sigs[0].suggested_shares == 0.0


def test_a_spent_day_budget_stops_further_entries():
    import risk_budget as rb
    full = 100_000.0 * rb.DAILY_ENTRY_BUDGET_PCT / 100.0
    sigs = [_sig("b1", 0.40, 0.20)]
    se._allocate_ladders(sigs, [_view("b1")], _P(), {}, (None, None, full), {})
    assert sigs[0].suggested_shares == 0.0


def test_correlated_cities_are_funded_less_than_independent_ones():
    """Two ladders, two cities. The only difference is whether their forecast
    errors move together, and that has to change the money."""
    views = [_view("b1", city="chicago"), _view("b2", city="toronto")]

    def _run(corr):
        sigs = [_sig("b1", 0.40, 0.20), _sig("b2", 0.40, 0.20)]
        se._allocate_ladders(sigs, views, _P(bankroll=20_000.0), {}, _no_risk(), corr)
        return sum(s.suggested_shares * 0.20 for s in sigs)

    together = _run({("chicago", "toronto"): 1.0})
    apart = _run({("chicago", "toronto"): 0.0})
    assert together < apart, (together, apart)


def test_the_cap_is_applied_before_the_earned_weight():
    """Order matters and it is not interchangeable.

    Weighting first and capping second lets the cap swallow the weight: a
    half-trusted strategy and a fully trusted one both land on the same cap
    and are funded identically, which makes the weight mean nothing exactly
    where the money is largest. This is the regression that caught it.
    """
    class _W:
        weight = 0.5

    full = [_sig("b1", 0.40, 0.20, strategy="sA")]
    half = [_sig("b1", 0.40, 0.20, strategy="sA")]
    se._allocate_ladders(full, [_view("b1")], _P(), {}, _no_risk(), {})
    se._allocate_ladders(half, [_view("b1")], _P(), {"sA": _W()}, _no_risk(), {})
    assert full[0].suggested_shares > 0
    assert half[0].suggested_shares == pytest.approx(0.5 * full[0].suggested_shares)


def test_a_missing_risk_view_does_not_stop_the_desk():
    """_risk_state returns (None, None, 0) when the view is absent, and that
    has to mean "behave as before", not "size nothing"."""
    sigs = [_sig("b1", 0.40, 0.20)]
    se._allocate_ladders(sigs, [_view("b1")], _P(), {}, _no_risk(), None)
    assert sigs[0].suggested_shares > 0
