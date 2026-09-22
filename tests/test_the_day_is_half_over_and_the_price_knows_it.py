"""Pricing the rest of the day instead of the whole of it.

Two different claims live in this file and they are tested differently.

THE IDENTITY is not a model: the final maximum is max(running maximum, the
maximum of what is left), so the predictive distribution has an ATOM at the
running maximum - every draw below it becomes exactly it. The desk used to
zero the passed bands and renormalise the rest IN PROPORTION, which hands
that mass to bands ABOVE the floor. Those tests assert arithmetic.

THE TRAJECTORY is a model: replacing the forecast's centre and width with
"this hour's reading plus what this city typically has left to climb" is a
claim that has to earn its place. Those tests assert that it is gated, that
the bar is the FLOORED forecast rather than a bare one, and that a cell which
cannot beat it stays in shadow.

The first test in the file is the derivation. Every gate decision is a CRPS
comparison, so a mistake in the closed form would quietly pick wrong
everywhere while looking entirely reasonable.
"""

import math
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import trajectory as tj
import probability_engine as pe
from forecast_postprocess import crps_gaussian
from trajectory import Hour


# ---------------------------------------------------------------------------
# The derivation.
# ---------------------------------------------------------------------------
def _crps_by_integration(floor_c, mu, sigma, y, lo=-60.0, hi=60.0, steps=200000):
    """CRPS from its definition, over the floored CDF. A different computation
    from the one under test - agreeing with itself would prove nothing."""
    step, total, prev = (hi - lo) / steps, 0.0, None
    for i in range(steps + 1):
        x = lo + i * step
        v = (tj.floored_cdf(x, floor_c, mu, sigma) - (1.0 if x >= y else 0.0)) ** 2
        if prev is not None:
            total += 0.5 * (prev + v) * step
        prev = v
    return total


@pytest.mark.parametrize("floor_c,mu,sigma,y", [
    (None, 20.0, 2.0, 21.5),     # no floor at all
    (15.0, 20.0, 2.0, 21.5),     # floor far below the centre
    (20.0, 20.0, 2.0, 21.5),     # floor exactly at the centre
    (21.0, 20.0, 2.0, 21.5),     # floor above the centre
    (21.4, 20.0, 1.0, 21.5),     # floor just under the outcome
    (24.0, 20.0, 3.0, 26.0),     # the day has run well past the forecast
    (10.0, 20.0, 0.5, 20.1),     # a tight width
])
def test_the_floored_crps_closed_form_matches_its_own_definition(floor_c, mu, sigma, y):
    closed = tj.crps_floored_gaussian(floor_c, mu, sigma, y)
    numeric = _crps_by_integration(floor_c, mu, sigma, y)
    assert closed == pytest.approx(numeric, abs=2e-3), (closed, numeric)


def test_with_no_floor_it_is_exactly_the_gaussian_score():
    """The floored form must collapse to the Gaussian one, not merely resemble
    it - the same function forecast_postprocess is scored with."""
    for mu, sigma, y in [(20.0, 2.0, 21.5), (5.0, 0.7, 4.1), (33.0, 4.0, 33.0)]:
        assert tj.crps_floored_gaussian(None, mu, sigma, y) == crps_gaussian(mu, sigma, y)


def test_a_floor_below_everything_changes_nothing():
    assert tj.crps_floored_gaussian(-50.0, 20.0, 2.0, 21.5) == pytest.approx(
        crps_gaussian(20.0, 2.0, 21.5), abs=1e-9)


def test_an_impossible_observation_is_punished_not_hidden():
    """A running maximum above the final one is a station or timezone
    mismatch. Scoring it as if the floor were right would teach the fit to
    trust a floor that is wrong."""
    honest = tj.crps_floored_gaussian(20.0, 20.0, 2.0, 21.0)
    impossible = tj.crps_floored_gaussian(25.0, 20.0, 2.0, 21.0)
    assert impossible > honest


def test_the_floored_cdf_is_zero_below_the_floor_and_normal_above():
    assert tj.floored_cdf(19.9, 20.0, 20.0, 2.0) == 0.0
    assert tj.floored_cdf(20.0, 20.0, 20.0, 2.0) == pytest.approx(0.5)
    assert tj.floored_cdf(22.0, 20.0, 20.0, 2.0) == pytest.approx(
        tj.floored_cdf(22.0, None, 20.0, 2.0))


# ---------------------------------------------------------------------------
# The atom, in the engine's own band arithmetic.
# ---------------------------------------------------------------------------
BANDS = [
    {"band_id": "a", "band_lo": None, "band_hi": 25, "open_low": True, "open_high": False},
    {"band_id": "b", "band_lo": 25, "band_hi": 26, "open_low": False, "open_high": False},
    {"band_id": "c", "band_lo": 26, "band_hi": 27, "open_low": False, "open_high": False},
    {"band_id": "d", "band_lo": 27, "band_hi": None, "open_low": False, "open_high": True},
]


def _probs(centre, sigma, floor=None):
    return dict(pe.compute_band_probabilities(centre, sigma, "C", BANDS, floor_c=floor))


def test_the_mass_below_the_floor_lands_on_the_band_holding_it():
    """THE REGRESSION. Zero-and-renormalise gave the sub-floor mass to every
    surviving band in proportion, which reads as room to climb the arithmetic
    forbids. Under the atom it goes to the band the day is standing in.

    Centre 25.0, sigma 1.5, the day already at 26.3 - so an effective floor of
    25.8 after the tolerance, which sits inside band c.
    """
    p = _probs(25.0, 1.5, floor=26.3)
    assert p["a"] == 0.0 and p["b"] == 0.0
    assert p["c"] == pytest.approx(0.841, abs=0.005)
    assert p["d"] == pytest.approx(0.159, abs=0.005)

    # what the old rule produced, computed here so the difference is explicit
    raw = _probs(25.0, 1.5)
    survivors = raw["c"] + raw["d"]
    old_c, old_d = raw["c"] / survivors, raw["d"] / survivors
    assert old_c == pytest.approx(0.570, abs=0.01)
    assert old_d == pytest.approx(0.430, abs=0.01)
    assert p["d"] < old_d, "the atom must take mass away from the bands above"


def test_the_probabilities_still_sum_to_one_with_a_floor():
    for floor in (None, 20.0, 24.0, 26.3, 26.9):
        total = sum(_probs(25.0, 1.5, floor=floor).values())
        assert total == pytest.approx(1.0, abs=1e-9), floor


def test_a_floor_under_the_whole_ladder_changes_nothing():
    assert _probs(25.0, 1.5, floor=10.0) == pytest.approx(_probs(25.0, 1.5))


def test_a_floor_over_the_whole_ladder_lands_on_the_open_end():
    """An open-high band is never impossible - there is no temperature that
    puts the day out of its range - so a floor above the whole ladder is not
    a contradiction, it is a statement that the top band has already won. The
    old zero-and-renormalise reached the same answer by a different route.
    """
    assert _probs(25.0, 1.5, floor=99.0) == {"a": 0.0, "b": 0.0, "c": 0.0, "d": 1.0}


def test_a_floor_over_a_CLOSED_ladder_is_refused_rather_than_obeyed():
    """With no open end the floor CAN kill everything, and then it is the
    ladder, the station or the city that is wrong - none of which improve by
    publishing a uniform distribution over impossibilities. Fall back to the
    forecast's opinion."""
    closed = [b for b in BANDS if not b["open_high"]] + [
        {"band_id": "d", "band_lo": 27, "band_hi": 28, "open_low": False, "open_high": False}]
    floored = dict(pe.compute_band_probabilities(25.0, 1.5, "C", closed, floor_c=99.0))
    plain = dict(pe.compute_band_probabilities(25.0, 1.5, "C", closed))
    assert floored == pytest.approx(plain)


def test_the_tolerance_still_protects_a_band_the_day_only_just_passed():
    """Our station and the venue's can disagree by a few tenths, so a band
    dies only once the floor has cleared its top by more than the tolerance."""
    edge = pe.unit_edge_c("C", 26)                      # top of band b
    just_over = edge + 0.1                              # inside the tolerance
    assert _probs(25.0, 1.5, floor=just_over)["b"] > 0
    well_over = edge + pe.OBSERVED_FLOOR_TOLERANCE_C + 0.2
    assert _probs(25.0, 1.5, floor=well_over)["b"] == 0.0


# ---------------------------------------------------------------------------
# The two predictors.
# ---------------------------------------------------------------------------
def _hour(hour=15, temp=24.0, run=24.5, climb=0.4, sd=0.6, final=24.9,
          fc=25.5, fc_sd=1.5, date="2026-09-01"):
    return Hour(local_date=date, local_hour=hour, temp_c=temp, running_max_c=run,
                climb_left_c=climb, climb_sd_c=sd, final_max_c=final,
                forecast_c=fc, forecast_sigma_c=fc_sd)


def test_the_trajectory_centre_is_this_hour_plus_what_is_left():
    floor, mu, sigma = tj.trajectory_predictor(_hour(temp=24.0, climb=0.4, sd=0.6))
    assert mu == pytest.approx(24.4)
    assert sigma == pytest.approx(0.6)
    assert floor == pytest.approx(24.5), "the running maximum is still the floor"


def test_the_width_correction_scales_the_climatological_spread():
    _f, _m, sigma = tj.trajectory_predictor(_hour(sd=0.6), sd_ratio=1.5)
    assert sigma == pytest.approx(0.9)


def test_the_day_is_never_known_to_better_than_the_floor():
    _f, _m, sigma = tj.trajectory_predictor(_hour(sd=0.0), sd_ratio=0.0)
    assert sigma == tj.SD_FLOOR_C


def test_the_forecast_predictor_is_the_floored_forecast_not_a_bare_one():
    """The bar the trajectory has to clear. Scoring against an unfloored
    Normal would credit this layer with a gain the floor already delivers."""
    floor, mu, sigma = tj.forecast_predictor(_hour(run=24.5, fc=25.5, fc_sd=1.5))
    assert (floor, mu, sigma) == (24.5, 25.5, 1.5)


def test_the_measured_ratio_is_the_factor_that_matches_the_errors():
    rows = [_hour(temp=20.0, climb=1.0, sd=1.0, final=22.0, date=f"2026-09-{d:02d}")
            for d in range(1, 9)]
    # every residual is exactly 1.0 against a stated 1.0 -> ratio 1.0
    assert tj.measured_sd_ratio(rows) == pytest.approx(1.0)
    wide = [h._replace(climb_sd_c=2.0) for h in rows]
    assert tj.measured_sd_ratio(wide) == pytest.approx(0.5)


# ---------------------------------------------------------------------------
# The gate.
# ---------------------------------------------------------------------------
def _decided_day(d):
    """Late afternoon, the day is over: the reading IS the answer and the
    forecast was a degree and a half out with a wide sigma."""
    return _hour(hour=17, temp=26.0, run=26.0, climb=0.0, sd=0.2, final=26.0,
                 fc=24.5, fc_sd=1.5, date=f"2026-09-{d:02d}")


def _morning(d, offset):
    """Early, with hours still to run: the climb profile is vague and the
    forecast is close."""
    return _hour(hour=8, temp=14.0, run=14.0, climb=6.0, sd=3.0,
                 final=20.0 + offset, fc=20.0 + offset, fc_sd=1.0,
                 date=f"2026-09-{d:02d}")


def _running_day(d, offset=0.0):
    """Mid-afternoon, still climbing, and the forecast is two degrees high.

    This is where the trajectory earns its place: the day itself contradicts
    the forecast and the remaining climb is a small, well-measured quantity.
    """
    return _hour(hour=15, temp=24.0, run=24.2, climb=0.5, sd=0.4,
                 final=24.5 + offset, fc=26.5, fc_sd=1.5,
                 date=f"2026-09-{d:02d}")


def test_the_trajectory_wins_when_the_day_contradicts_the_forecast():
    offsets = [0.05, -0.1, 0.0, 0.08, -0.05, 0.1, -0.08,
               0.03, -0.03, 0.06, 0.0, -0.06, 0.09, -0.09]
    rows = [_running_day(d, o) for d, o in zip(range(1, 15), offsets)]
    fit = tj.fit_cell("testville", 15, rows)
    assert fit.applied is True, fit.reason
    assert fit.crps_gain > 0
    assert fit.crps_trajectory < fit.crps_forecast


def test_on_a_day_that_is_already_over_the_atom_alone_is_enough():
    """THE GATE BEING HONEST ABOUT ITS OWN LAYER, and worth stating plainly.

    Once the floor is an atom rather than a truncation, a decided day is
    already priced almost perfectly BY THE FORECAST: with a reading of 26.0
    and a forecast of 24.5 +/- 1.5, Phi((26.0-24.5)/1.5) = 84% of the
    forecast's mass lands on the atom at 26.0, which is the answer. Measured
    here: the floored forecast scores 0.0109 and the trajectory 0.0175.

    So the trajectory must NOT be applied at that hour. It has nothing to add,
    and a gate that waved it through anyway would be measuring its own
    enthusiasm rather than the evidence.
    """
    rows = [_decided_day(d) for d in range(1, 15)]
    fit = tj.fit_cell("testville", 17, rows)
    assert fit.applied is False, fit.reason
    assert fit.crps_forecast < fit.crps_trajectory


def test_a_morning_hour_where_the_forecast_is_better_stays_in_shadow():
    rows = [_morning(d, o) for d, o in
            zip(range(1, 15), [0.1, -0.2, 0.0, 0.15, -0.1, 0.2, -0.05,
                               0.1, -0.15, 0.05, 0.0, -0.1, 0.2, -0.2])]
    fit = tj.fit_cell("testville", 8, rows)
    assert fit.applied is False, fit.reason
    assert "shadow" in fit.reason


def test_a_thin_cell_is_reported_not_fitted():
    fit = tj.fit_cell("testville", 15, [_decided_day(d) for d in range(1, 5)])
    assert fit.applied is False
    assert fit.sd_ratio == 1.0
    assert "needs" in fit.reason


def test_every_cell_says_why_whichever_way_it_went():
    for rows, hour in (([_decided_day(d) for d in range(1, 15)], 17),
                       ([_decided_day(d) for d in range(1, 4)], 15)):
        assert tj.fit_cell("t", hour, rows).reason


def test_the_gate_scores_on_days_the_fit_did_not_see():
    """A cell cannot pass by memorising its own days: one freak day must cost
    the held-out score even though an in-sample fit would absorb it."""
    rows = [_decided_day(d) for d in range(1, 15)]
    rows[6] = rows[6]._replace(final_max_c=32.0)      # a day nothing predicted
    fit = tj.fit_cell("testville", 17, rows)
    clean = tj.fit_cell("testville", 17, [_decided_day(d) for d in range(1, 15)])
    assert fit.crps_trajectory > clean.crps_trajectory


# ---------------------------------------------------------------------------
# The wiring, in the engine that actually runs.
# ---------------------------------------------------------------------------
def _traj_row(**kw):
    row = {"city_key": "london", "local_date": "2026-09-19", "local_hour": 16,
           "running_max_c": 24.8, "latest_temp_today_c": 24.6,
           "readings_today": 14, "typical_climb_left_c": 0.2,
           "climb_left_sd_c": 0.45, "pct_already_peaked": 83.9,
           "sd_ratio": 1.0, "crps_gain": 0.21, "trajectory_applied": True}
    row.update(kw)
    return row


def _price(monkeypatch, traj=None, for_date="2026-09-19"):
    from types import SimpleNamespace
    monkeypatch.setattr(pe, "_forecast_for", lambda *_a, **_k: {
        "lead_days": 0, "forecast_max_c": 20.0, "model": "nws",
        "run_at": "2026-09-18T12:00:00+00:00"})
    monkeypatch.setattr(pe, "_skill_for", lambda *_a, **_k: {
        "lead_days": 0, "mae_c": 2.0, "bias_c": 0.0, "n_days": 300,
        "evidence_scope": pe.VERIFIED_EVIDENCE_SCOPE, "verified": True})
    monkeypatch.setattr(pe.regime, "classify", lambda *_a, **_k: SimpleNamespace(
        confidence=1.0, reasons=[], sigma_multiplier=1.0, label="NORMAL"))
    monkeypatch.setattr(pe, "_divergence_for", lambda *_a, **_k: (1.0, None))
    monkeypatch.setattr(pe, "_calibration_for", lambda *_a, **_k: (1.0, None))
    monkeypatch.setattr(pe, "_calibration_map", lambda: None)
    monkeypatch.setattr(pe, "model_version_id", lambda *_a, **_k: None)
    monkeypatch.setattr(pe, "_postprocess_for", lambda *_a, **_k: None)
    monkeypatch.setattr(pe, "_trajectory_cache", {"london": traj} if traj else {})
    return pe.process_city_day("london", for_date, "C", BANDS, {}, None, None, None)


def test_with_nothing_applied_the_day_is_priced_exactly_as_before(monkeypatch):
    rows, _reg, why = _price(monkeypatch, traj=None)
    assert rows[0]["sigma_c"] == pytest.approx(2.0 * pe.MAE_TO_SIGMA)
    assert not any(r.startswith("trajectory") for r in why)


def test_an_applied_hour_replaces_the_centre_and_the_width(monkeypatch):
    rows, _reg, why = _price(monkeypatch, traj=_traj_row())
    assert rows[0]["sigma_c"] == pytest.approx(0.45)
    line = next((r for r in why if r.startswith("trajectory")), None)
    assert line is not None and "16h_local" in line and "83.9pct_peaked" in line


def test_the_width_correction_reaches_the_published_sigma(monkeypatch):
    rows, _reg, _why = _price(monkeypatch, traj=_traj_row(sd_ratio=2.0))
    assert rows[0]["sigma_c"] == pytest.approx(0.90)


def test_a_trajectory_says_nothing_about_tomorrow(monkeypatch):
    rows, _reg, why = _price(monkeypatch, traj=_traj_row(), for_date="2026-09-20")
    assert rows[0]["sigma_c"] == pytest.approx(2.0 * pe.MAE_TO_SIGMA)
    assert not any(r.startswith("trajectory") for r in why)


@pytest.mark.parametrize("missing", ["latest_temp_today_c", "typical_climb_left_c",
                                     "climb_left_sd_c"])
def test_a_missing_input_prices_from_the_forecast_rather_than_guessing(monkeypatch, missing):
    rows, _reg, why = _price(monkeypatch, traj=_traj_row(**{missing: None}))
    assert rows[0]["sigma_c"] == pytest.approx(2.0 * pe.MAE_TO_SIGMA)
    assert not any(r.startswith("trajectory") for r in why)


def test_the_floor_still_applies_on_top_of_the_trajectory(monkeypatch):
    """Two different statements: the trajectory says where the rest of the day
    is going, the floor says a maximum cannot go down. The second is an
    identity and holds whichever centre the first produces."""
    floors = {"london": ("2026-09-19", 26.3)}
    from types import SimpleNamespace
    monkeypatch.setattr(pe, "_forecast_for", lambda *_a, **_k: {
        "lead_days": 0, "forecast_max_c": 20.0, "model": "nws",
        "run_at": "2026-09-18T12:00:00+00:00"})
    monkeypatch.setattr(pe, "_skill_for", lambda *_a, **_k: {
        "lead_days": 0, "mae_c": 2.0, "bias_c": 0.0, "n_days": 300,
        "evidence_scope": pe.VERIFIED_EVIDENCE_SCOPE, "verified": True})
    monkeypatch.setattr(pe.regime, "classify", lambda *_a, **_k: SimpleNamespace(
        confidence=1.0, reasons=[], sigma_multiplier=1.0, label="NORMAL"))
    monkeypatch.setattr(pe, "_divergence_for", lambda *_a, **_k: (1.0, None))
    monkeypatch.setattr(pe, "_calibration_for", lambda *_a, **_k: (1.0, None))
    monkeypatch.setattr(pe, "_calibration_map", lambda: None)
    monkeypatch.setattr(pe, "model_version_id", lambda *_a, **_k: None)
    monkeypatch.setattr(pe, "_postprocess_for", lambda *_a, **_k: None)
    monkeypatch.setattr(pe, "_trajectory_cache",
                        {"london": _traj_row(latest_temp_today_c=26.0,
                                             typical_climb_left_c=0.1)})
    rows, _reg, _why = pe.process_city_day("london", "2026-09-19", "C", BANDS, {},
                                           floors, None, None)
    by_id = {r["band_id"]: r["raw_prob"] for r in rows}
    # clamp_prob floors a published probability at 1e-6 rather than zero, so a
    # dead band reads as that rather than as nothing at all.
    assert by_id["a"] <= 1e-6 and by_id["b"] <= 1e-6, "the floor still kills passed bands"
