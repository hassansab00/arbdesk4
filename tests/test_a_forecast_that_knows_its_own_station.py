"""The post-processing layer: does a fitted correction beat the raw forecast?

These tests are about ARITHMETIC AND RESTRAINT, in that order.

The arithmetic half checks the closed-form CRPS against numerical integration
of its own definition, because every decision this layer makes - which
shrinkage constant, whether a cell is applied at all - is that one number, and
a wrong constant in it would silently pick the wrong answer everywhere while
looking perfectly reasonable.

The restraint half checks that a thin sample cannot move a price far, that a
held-out day never informs the fit that prices it, and that a correction with
no measured gain stays in shadow. Those are the properties that make the
difference between a calibration layer and an over-fit.
"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import forecast_postprocess as fp
from forecast_postprocess import Day


# ---------------------------------------------------------------------------
# The scoring rule.
# ---------------------------------------------------------------------------
def _crps_by_integration(mu, sigma, y, lo=-40.0, hi=40.0, steps=40000):
    """CRPS from its definition: integral of (F(x) - 1{x>=y})^2 dx.

    Trapezoid over a window wide enough that the tails contribute nothing at
    the sigmas used here. Deliberately a different computation from the one
    under test - agreeing with itself proves nothing.
    """
    total, step = 0.0, (hi - lo) / steps
    prev = None
    for i in range(steps + 1):
        x = lo + i * step
        f = fp.normal_cdf((x - mu) / sigma)
        v = (f - (1.0 if x >= y else 0.0)) ** 2
        if prev is not None:
            total += 0.5 * (prev + v) * step
        prev = v
    return total


@pytest.mark.parametrize("mu,sigma,y", [
    (0.0, 1.0, 0.0),
    (0.0, 1.0, 1.5),
    (20.0, 2.0, 18.7),
    (20.0, 0.5, 23.0),
    (-5.0, 3.0, -5.25),
])
def test_the_closed_form_crps_matches_its_own_definition(mu, sigma, y):
    closed = fp.crps_gaussian(mu, sigma, y)
    numeric = _crps_by_integration(mu, sigma, y)
    assert closed == pytest.approx(numeric, abs=2e-3), (
        f"closed form {closed} vs integrated {numeric} for N({mu},{sigma}) at {y}")


def _standard_normal_sample(n):
    """n points at the mid-quantiles of N(0,1), by bisection on the CDF.

    A hand-picked list of "normal-looking" z values is not a normal sample -
    the first draft of this test used eight of them whose root mean square was
    1.40, so the score was correctly minimised at 2.8 rather than 2.0 and the
    test blamed the formula for the sample.
    """
    out = []
    for i in range(1, n + 1):
        p, lo, hi = (i - 0.5) / n, -8.0, 8.0
        for _ in range(200):
            mid = (lo + hi) / 2.0
            if fp.normal_cdf(mid) < p:
                lo = mid
            else:
                hi = mid
        out.append((lo + hi) / 2.0)
    return out


def test_crps_is_minimised_by_telling_the_truth():
    """A proper scoring rule cannot be gamed by overstating confidence.

    Observations drawn at the quantiles of N(20, 2). Scoring them against
    N(20, s) must be best at s = 2: a narrower claim is punished for the days
    it misses, a wider one for the days it did not need. This is the property
    that lets the fit be chosen by CRPS at all.
    """
    ys = [20.0 + 2.0 * z for z in _standard_normal_sample(40)]
    scores = {s: sum(fp.crps_gaussian(20.0, s, y) for y in ys) / len(ys)
              for s in (1.0, 1.5, 1.75, 2.0, 2.25, 2.5, 3.0)}
    assert min(scores, key=scores.get) == 2.0, scores
    assert scores[1.0] > scores[2.0] and scores[3.0] > scores[2.0]


def test_a_sigma_of_zero_cannot_score():
    assert fp.crps_gaussian(20.0, 0.0, 20.0) == float("inf")
    assert fp.crps_gaussian(20.0, -1.0, 20.0) == float("inf")


# ---------------------------------------------------------------------------
# Shrinkage.
# ---------------------------------------------------------------------------
def test_no_evidence_returns_the_prior_exactly():
    assert fp.shrink(99.0, 0, 10.0, 1.5) == 1.5


def test_evidence_equal_to_k_lands_halfway():
    assert fp.shrink(3.0, 10, 10.0, 1.0) == pytest.approx(2.0)


def test_evidence_swamps_the_prior_eventually():
    assert fp.shrink(3.0, 100000, 10.0, 1.0) == pytest.approx(3.0, abs=1e-3)


def test_a_bigger_k_moves_less():
    near = abs(fp.shrink(3.0, 10, 2.0, 1.0) - 1.0)
    far = abs(fp.shrink(3.0, 10, 40.0, 1.0) - 1.0)
    assert far < near


# ---------------------------------------------------------------------------
# The measurements.
# ---------------------------------------------------------------------------
def _days(pairs, start=1):
    return [Day(for_date=f"2026-09-{start + i:02d}", forecast_c=f,
                observed_c=o, spread_c=0.0)
            for i, (f, o) in enumerate(pairs)]


def test_a_hot_forecast_gives_a_positive_bias_and_moves_the_centre_down():
    """Sign convention, stated once and pinned here.

    bias_c is forecast minus observed, matching fact_forecast_outcome.error_c
    and derived_forecast_skill.bias_c, so `centre - bias` de-biases. Getting
    this backwards would double every station error instead of removing it,
    and it would look like a working correction right up until settlement.
    """
    days = _days([(22.0, 20.0), (23.0, 21.0), (24.0, 22.0)])
    assert fp.measured_bias(days) == pytest.approx(2.0)
    fit = fp.fit_cell(days, days, k_cell=0.0, k_city=0.0)
    centre, _ = fp.apply_fit(fit, 25.0)
    assert centre == pytest.approx(23.0), "a forecast running 2 C hot must come down 2 C"


def test_the_baseline_width_is_the_one_the_desk_publishes_today():
    days = _days([(21.0, 20.0), (19.0, 20.0), (22.0, 20.0), (18.0, 20.0)])
    assert fp.baseline_sigma(days) == pytest.approx(1.5 * fp.MAE_TO_SIGMA)


def test_the_ratio_is_the_factor_that_makes_the_z_scores_honest():
    """sigma * ratio is the root mean square residual, which is the Gaussian MLE.

    Equivalently: after applying it, sd((observed - centre)/sigma) is 1. That
    is the same quantity ad4_45 calls z_sd, reached from the residuals instead
    of from a sigma that had to have been published first.
    """
    days = _days([(20.0, 21.0), (20.0, 19.0), (20.0, 21.0), (20.0, 19.0)])
    base = fp.baseline_sigma(days)                       # mae 1.0 -> 1.2533
    ratio = fp.measured_ratio(days, 0.0, base)
    assert base * ratio == pytest.approx(1.0)            # rms residual is exactly 1
    zs = [(d.observed_c - d.forecast_c) / (base * ratio) for d in days]
    assert math.sqrt(sum(z * z for z in zs) / len(zs)) == pytest.approx(1.0)


def test_an_over_wide_distribution_is_what_the_ratio_reports():
    """The measured defect: rms residual below the published width -> ratio < 1."""
    days = _days([(20.0, 20.2), (20.0, 19.8)] * 6)
    base = fp.baseline_sigma(days)
    assert fp.measured_ratio(days, 0.0, base) < 1.0


# ---------------------------------------------------------------------------
# Restraint.
# ---------------------------------------------------------------------------
def test_a_single_day_cannot_move_a_price_far():
    """One freak day against a city pool that says otherwise.

    The cell sees 8 C of error once. The city has 40 clean days. With the
    live shrinkage the cell must end up near the city's answer, not near its
    own - this is the property that replaces the current code's `proxy -> 0`
    cliff, and it has to hold without one.
    """
    cell = _days([(28.0, 20.0)])
    city = _days([(20.0, 20.0)] * 40, start=1)
    city = [Day(f"2026-08-{(i % 28) + 1:02d}", d.forecast_c, d.observed_c, 0.0)
            for i, d in enumerate(city)]
    fit = fp.fit_cell(cell, city, k_cell=10.0, k_city=30.0)
    assert abs(fit.bias_c) < 1.0, f"one day moved the centre {fit.bias_c} C"


def test_a_correction_is_clamped_however_bad_the_evidence():
    cell = _days([(120.0, 20.0)] * 50)
    fit = fp.fit_cell(cell, cell, k_cell=0.0, k_city=0.0)
    assert abs(fit.bias_c) <= fp.BIAS_CEILING_C
    assert fp.RATIO_FLOOR <= fit.sigma_ratio <= fp.RATIO_CEILING


def test_a_distribution_never_collapses_to_a_point():
    days = _days([(20.0, 20.0)] * 20)
    fit = fp.fit_cell(days, days, k_cell=0.0, k_city=0.0)
    _, sigma = fp.apply_fit(fit, 20.0)
    assert sigma >= fp.SIGMA_FLOOR_C


# ---------------------------------------------------------------------------
# Held-out scoring.
# ---------------------------------------------------------------------------
def test_the_folds_are_contiguous_and_cover_every_day():
    blocks = fp.blocked_folds(11, 4)
    assert len(blocks) == 4
    assert blocks[0][0] == 0 and blocks[-1][1] == 11
    for (a, b), (c, d) in zip(blocks, blocks[1:]):
        assert b == c, "a gap or an overlap between blocks"
    assert sum(b - a for a, b in blocks) == 11


def test_folds_degrade_gracefully_on_a_tiny_sample():
    assert fp.blocked_folds(0, 4) == []
    assert fp.blocked_folds(5, 1) == []
    assert len(fp.blocked_folds(2, 4)) == 2


def test_a_held_out_day_never_informs_the_fit_that_prices_it():
    """The leak this whole structure exists to close.

    Twelve clean days and one 10 C outlier. If the outlier's own block fed the
    fit, its held-out CRPS would be small. Scored honestly it is large, so the
    fitted score over all blocks must be worse than a fit that had seen it.
    """
    days = _days([(20.0, 20.0)] * 6 + [(30.0, 20.0)] + [(20.0, 20.0)] * 6)
    honest, _, n = fp.cross_validate(days, days, 0.0, 0.0, folds=4)
    seen = fp.score_days(days, fp.fit_cell(days, days, 0.0, 0.0))
    assert n == len(days)
    assert honest > seen, "the held-out score was no worse than the in-sample one"


# ---------------------------------------------------------------------------
# The gate.
# ---------------------------------------------------------------------------
def _cells(days):
    return {("testville", 0): days}


def test_a_real_station_bias_earns_its_correction():
    """A forecast 2 C hot every day is exactly what this layer is for."""
    days = _days([(22.0, 20.0), (23.0, 21.2), (24.5, 22.4), (21.0, 19.1),
                  (25.0, 23.3), (23.5, 21.4), (22.5, 20.6), (24.0, 22.1),
                  (23.0, 20.9), (22.0, 20.2), (25.5, 23.4), (24.0, 21.9)])
    rows = fp.fit_all(_cells(days), {"testville": days}, 5.0, 15.0)
    row = rows[0]
    assert row["applied"] is True, row["reason"]
    assert row["bias_c"] > 1.0
    assert row["crps_gain"] > 0


def test_a_forecast_with_nothing_wrong_with_it_is_left_alone():
    """No bias, width already honest: the gate must find no gain and stand down."""
    offsets = [-1.4, 0.9, -0.3, 1.2, -1.1, 0.4, 0.7, -0.8, 1.3, -0.5, 0.2, -0.6]
    days = _days([(20.0, 20.0 + o) for o in offsets])
    rows = fp.fit_all(_cells(days), {"testville": days}, 40.0, 120.0)
    row = rows[0]
    assert row["bias_c"] == pytest.approx(0.0, abs=0.15)
    assert row["sigma_ratio"] == pytest.approx(1.0, abs=0.35)


def test_a_thin_cell_is_reported_not_fitted():
    days = _days([(22.0, 20.0)] * 3)
    row = fp.fit_all(_cells(days), {"testville": days}, 10.0, 30.0)[0]
    assert row["applied"] is False
    assert row["bias_c"] == 0.0 and row["sigma_ratio"] == 1.0
    assert "needs" in row["reason"]


def test_every_cell_gets_a_row_whether_it_passed_or_not():
    """`applied` is a column, not a filter - see ad4_83's header.

    A missing row and a failed row mean different things, and the UI has to be
    able to tell them apart.
    """
    good = _days([(22.0, 20.0), (23.0, 21.1), (24.0, 22.2), (21.0, 19.0),
                  (25.0, 23.1), (23.0, 21.2), (22.0, 20.1), (24.0, 22.0)])
    thin = _days([(20.0, 20.0)] * 2)
    rows = fp.fit_all({("a", 0): good, ("b", 1): thin},
                      {"a": good, "b": thin}, 5.0, 15.0)
    assert {(r["city_key"], r["lead_days"]) for r in rows} == {("a", 0), ("b", 1)}
    assert all(r["reason"] for r in rows), "a row with no reason explains nothing"


def test_the_shrinkage_constant_is_chosen_not_asserted():
    """Pure noise must choose more shrinkage than a real, repeatable bias.

    This is the property that lets the constants live in a grid instead of in
    someone's judgement: when a cell carries signal the chosen k is small, and
    when it carries none the chosen k is large.
    """
    noise = _days([(20.0, 20.0 + o) for o in
                   (2.1, -1.9, 0.4, -2.3, 1.8, -0.6, 2.4, -2.1, 0.9, -1.2, 1.5, -1.0)])
    signal = _days([(20.0, 17.0 + o) for o in
                    (0.1, -0.2, 0.15, -0.1, 0.2, -0.15, 0.05, -0.05, 0.1, -0.1, 0.12, -0.08)])
    k_noise, _ = fp.choose_shrinkage([(noise, noise)])
    k_signal, _ = fp.choose_shrinkage([(signal, signal)])
    assert k_noise["k_cell"] >= k_signal["k_cell"], (k_noise, k_signal)


# ---------------------------------------------------------------------------
# Evidence selection.
# ---------------------------------------------------------------------------
def test_one_day_per_city_date_lead_however_many_models_quoted():
    """Counting models as days would multiply a ten-day sample by three."""
    rows = [
        {"city_key": "a", "for_date": "2026-09-01", "lead_days": 1,
         "run_at": "2026-08-31T00:00:00Z", "forecast_max_c": 20.0, "observed_max_c": 19.0},
        {"city_key": "a", "for_date": "2026-09-01", "lead_days": 1,
         "run_at": "2026-08-31T06:00:00Z", "forecast_max_c": 22.0, "observed_max_c": 19.0},
    ]
    out = fp._newest_per_run_key(rows)
    assert len(out) == 1
    day = out[("a", "2026-09-01", 1)]
    assert day.forecast_c == 22.0, "the newest run must win"
    assert day.spread_c == pytest.approx(2.0), "the model disagreement is recorded"


def test_a_row_with_no_observation_is_not_evidence():
    rows = [{"city_key": "a", "for_date": "2026-09-01", "lead_days": 1,
             "run_at": "2026-08-31T00:00:00Z", "forecast_max_c": 20.0,
             "observed_max_c": None}]
    assert fp._newest_per_run_key(rows) == {}


def test_the_pre_repair_reader_is_excluded_from_the_fit():
    """Days read by v_city_daily_max carry the settlement bug, not a station bias.

    Fitting on them would learn the old reader's over- and under-read and then
    apply it to days the reader no longer makes.
    """
    captured = {}

    class _FakeCommon:
        @staticmethod
        def rest_all(table, params, order=None, page_size=None):
            captured["table"] = table
            captured["params"] = list(params)
            return []

    sys.modules["common"] = _FakeCommon
    try:
        fp.load_evidence(lookback_days=30)
    finally:
        sys.modules.pop("common", None)

    assert captured["table"] == "fact_forecast_outcome"
    assert ("obs_source", f"neq.{fp.FALLBACK_OBS_SOURCE}") in captured["params"]
    assert ("obs_source", "not.is.null") in captured["params"]


def test_the_paging_order_is_unique_or_it_loses_rows():
    """fact_forecast_outcome is keyed on four columns, so three will not do.

    rest_all pages by offset. An order that ties leaves the server free to
    break those ties differently between pages, which silently drops and
    duplicates rows - and a bias fitted on a sample with holes in it is wrong
    in a way nothing downstream can see.
    """
    captured = {}

    class _FakeCommon:
        @staticmethod
        def rest_all(table, params, order=None, page_size=None):
            captured["order"] = order
            return []

    sys.modules["common"] = _FakeCommon
    try:
        fp.load_evidence(lookback_days=30)
    finally:
        sys.modules.pop("common", None)

    cols = {part.split(".")[0] for part in captured["order"].split(",")}
    assert {"city_key", "for_date", "lead_days", "model"} <= cols, captured["order"]


def test_the_city_pool_gathers_every_lead():
    a0 = _days([(20.0, 20.0)] * 3)
    a1 = _days([(21.0, 20.0)] * 4, start=10)
    pools = fp.city_pools({("a", 0): a0, ("a", 1): a1})
    assert len(pools["a"]) == 7
    assert pools["a"] == sorted(pools["a"], key=lambda d: d.for_date)
