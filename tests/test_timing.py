"""Timing: act now or wait (plan v2 P5.6)."""
import datetime as dt

import pytest

import timing as tm

T0 = dt.datetime(2026, 9, 25, 10, 0, tzinfo=dt.timezone.utc)
STATE = {"p": 0.40, "ask": 0.30, "depth": 200.0, "hours_to_peak": 4.0, "regime": "NORMAL"}


def _obs(minutes, p, ask, depth=200.0):
    return {"at": T0 + dt.timedelta(minutes=minutes), "p": p, "ask": ask, "depth": depth,
            "hours_to_peak": 4.0, "regime": "NORMAL"}


# ---- cells and pairs ---------------------------------------------------------

def test_the_cell_bins_each_condition():
    assert tm.cell(4.0, 0.10, "NORMAL", 200.0) == (2, 2, "NORMAL", 1)
    assert tm.cell(-1.0, -0.20, None, 10.0) == (0, 3, "UNKNOWN", 0)
    assert tm.cell(None, None, "SHARP", None) == ("na", "na", "SHARP", "na")


def test_only_consecutive_hours_make_pairs():
    series = [_obs(0, 0.40, 0.30), _obs(60, 0.42, 0.33, 150.0), _obs(180, 0.45, 0.35),   # 120 min: not an hour
              _obs(245, 0.44, 0.36)]                                                      # 65 min: an hour
    pairs = tm.pairs_from_series(list(reversed(series)))          # order does not matter
    assert [d for _c, d in pairs] == [pytest.approx((0.02, 0.03, -50.0)), pytest.approx((-0.01, 0.01, 0.0))]
    assert pairs[0][0] == tm.cell(4.0, 0.10, "NORMAL", 200.0)    # the cell at the START of the hour


# ---- the prior rule ---------------------------------------------------------

def test_under_the_minimum_sample_the_prior_decides_and_says_so():
    model = tm.TransitionModel([(tm.cell(4.0, 0.10, "NORMAL", 200.0), (0, 0, 0))] * (tm.MIN_PAIRS - 1))
    act, rec = tm.decide(0.01, {**STATE, "gap_prev": 0.08}, model, g_next_fn=lambda *a: 1.0)
    assert act and rec["rule"] == "prior" and rec["cell_pairs"] == tm.MIN_PAIRS - 1
    assert "499 hourly pairs" in rec["prior_because"] and rec["timing_version"] == tm.TIMING_VERSION


@pytest.mark.parametrize("g_now,gap_prev,act,trend", [
    (0.01, 0.12, False, "shrinking"),        # gap 0.10 now, 0.12 an hour ago: the market is coming to us
    (0.01, 0.10, True, "not_shrinking"),
    (0.01, None, True, "unknown"),
    (0.0, 0.05, False, "not_shrinking"),     # nothing to gain
    (-0.01, None, False, "unknown"),
])
def test_the_prior_rule(g_now, gap_prev, act, trend):
    got, rec = tm.decide(g_now, {**STATE, "gap_prev": gap_prev})
    assert got is act and rec["gap_trend"] == trend and rec["rule"] == "prior"


def test_no_growth_is_never_an_action():
    assert tm.decide(None, STATE)[0] is False
    assert tm.decide(float("nan"), STATE)[1]["rule"] == "no_growth"


# ---- the model rule ---------------------------------------------------------

def _model(delta, n=tm.MIN_PAIRS):
    return tm.TransitionModel([(tm.cell(4.0, 0.10, "NORMAL", 200.0), delta)] * n, version="v-test")


def g_of(p, ask, depth):
    """A simple stand-in: the edge per dollar, which is what the solver's growth rises with."""
    return p - ask


def test_waiting_wins_when_the_price_is_expected_to_fall():
    # Every observed hour in this cell the ask dropped 5c: waiting is worth 0.15, acting 0.10.
    act, rec = tm.decide(0.10, STATE, _model((0.0, -0.05, 0.0)), g_next_fn=g_of)
    assert act is False and rec["rule"] == "model" and rec["g_wait"] == pytest.approx(0.15)
    assert rec["model_version"] == "v-test"


def test_acting_wins_when_the_price_is_expected_to_run_away():
    act, rec = tm.decide(0.10, STATE, _model((0.0, 0.05, 0.0)), g_next_fn=g_of)
    assert act is True and rec["g_wait"] == pytest.approx(0.05)


def test_the_option_not_to_trade_floors_the_next_tick_at_zero():
    # The ask runs 30c away: acting next hour would lose, but not acting is still there.
    act, rec = tm.decide(0.001, STATE, _model((0.0, 0.30, 0.0)), g_next_fn=g_of)
    assert rec["g_wait"] == 0.0 and act is True


def test_the_cost_of_waiting_is_bounded():
    # A tie broken by c_wait; a huge c_wait is clipped to the bound.
    _, rec = tm.decide(0.10, STATE, _model((0.0, 0.0, 0.0)), g_next_fn=g_of, c_wait=5.0)
    assert rec["c_wait"] == tm.C_WAIT_MAX
    act, _ = tm.decide(0.099, STATE, _model((0.0, 0.0, 0.0)), g_next_fn=g_of, c_wait=0.0)
    assert act is False


def test_the_simulation_is_reproducible():
    m = tm.TransitionModel([(tm.cell(4.0, 0.10, "NORMAL", 200.0), (0.0, d / 100, 0.0))
                            for d in range(-5, 6)] * 50)
    assert tm.decide(0.10, STATE, m, g_next_fn=g_of, seed=3) == tm.decide(0.10, STATE, m, g_next_fn=g_of, seed=3)
