"""Does the fitted correction actually move a published number?

scripts/forecast_postprocess.py fits a station bias and a width factor, and
tests/test_a_forecast_that_knows_its_own_station.py proves the fit is sound.
Neither says whether probability_engine reads it. That is the gap this file
covers, and it is the gap that matters: the whole layer could be correct and
the desk would go on publishing the same distributions it publishes today
without one of those tests failing.

WHAT THE LAYER IS REPLACING, measured on 294 settled city-days, 16-21 Sep:

  6,666 of 8,118 lead-0 rows carried bias_applied_c = 0.0000 because their
  skill row was borrowed from lead 1 and a borrowed row forces the bias to
  zero - while the same cities measured a mean |bias| of 0.696 C at lead 0.

  sd((observed - centre)/sigma) came to 0.873, and of 49 cities 35 read below
  0.7 with none above 1.3.

So there are two things to check beyond "it is read at all": that a borrowed
skill row no longer zeroes a correction that WAS fitted for that lead, and
that the width the fit chose is the width published rather than that width
multiplied by something else measuring the same error again.
"""

import os
import sys
from types import SimpleNamespace

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "scripts"))

import probability_engine as pe

BANDS = [
    {"band_id": "low", "band_lo": None, "band_hi": 20, "open_low": True, "open_high": False},
    {"band_id": "mid", "band_lo": 20, "band_hi": 21, "open_low": False, "open_high": False},
    {"band_id": "high", "band_lo": 21, "band_hi": None, "open_low": False, "open_high": True},
]


def _pp(bias=1.2, ratio=0.80, baseline=1.50, n_days=12, n_city=110, gain=0.031):
    return {"city_key": "london", "lead_days": 0, "bias_c": bias,
            "sigma_ratio": ratio, "baseline_sigma_c": baseline,
            "n_days": n_days, "n_city_days": n_city, "crps_gain": gain}


def _price(monkeypatch, pp=None, cal=(1.0, None), lead=0, proxy=False,
           promoted=None, model_forecasts=None, skill_bias=0.5, skill_mae=2.0):
    """One city-day through the real function, with only its readers stubbed."""
    monkeypatch.setattr(pe, "_forecast_for", lambda *_a, **_k: {
        "lead_days": lead, "forecast_max_c": 20.0, "model": "nws",
        "run_at": "2026-09-18T12:00:00+00:00"})

    def _skill(city, want_lead, model=None):
        # A proxy is produced by having NO row at the priced lead and one at
        # the next lead up, which is exactly how the live shortfall arises.
        if proxy and int(want_lead) == int(lead):
            return None
        return {"lead_days": want_lead, "mae_c": skill_mae, "bias_c": skill_bias,
                "n_days": 300, "evidence_scope": pe.VERIFIED_EVIDENCE_SCOPE,
                "verified": True}

    monkeypatch.setattr(pe, "_skill_for", _skill)
    monkeypatch.setattr(pe.regime, "classify", lambda *_a, **_k: SimpleNamespace(
        confidence=1.0, reasons=[], sigma_multiplier=1.0, label="NORMAL"))
    monkeypatch.setattr(pe, "_divergence_for", lambda *_a, **_k: (1.0, None))
    monkeypatch.setattr(pe, "_calibration_for", lambda *_a, **_k: cal)
    monkeypatch.setattr(pe, "_calibration_map", lambda: None)
    monkeypatch.setattr(pe, "model_version_id", lambda *_a, **_k: None)
    monkeypatch.setattr(pe, "_postprocess_for", lambda *_a, **_k: pp)
    return pe.process_city_day("london", "2026-09-19", "C", BANDS, {}, None,
                               promoted, model_forecasts)


# ---------------------------------------------------------------------------
# It is read, and it is the number published.
# ---------------------------------------------------------------------------
def test_the_fitted_bias_is_the_bias_applied(monkeypatch):
    rows, _reg, _why = _price(monkeypatch, pp=_pp(bias=1.2))
    assert rows[0]["bias_applied_c"] == pytest.approx(1.2)
    assert rows[0]["forecast_max_c"] == 20.0, "the raw forecast is still on the record"


def test_the_fitted_width_is_the_width_published(monkeypatch):
    rows, _reg, _why = _price(monkeypatch, pp=_pp(ratio=0.80, baseline=1.50))
    assert rows[0]["sigma_c"] == pytest.approx(1.20)


def test_the_baseline_travels_with_the_ratio_not_the_live_skill(monkeypatch):
    """The held-out gain was demonstrated for baseline x ratio and no other
    product. Applying the ratio to the live mae_c would be a correction that
    was never tested - and here that would give 2.0 * 1.2533 * 0.8 = 2.005,
    not 1.20."""
    rows, _reg, _why = _price(monkeypatch, pp=_pp(ratio=0.80, baseline=1.50),
                              skill_mae=2.0)
    assert rows[0]["sigma_c"] == pytest.approx(1.20)
    assert rows[0]["sigma_c"] != pytest.approx(2.0 * pe.MAE_TO_SIGMA * 0.80)


def test_a_narrower_distribution_concentrates_the_ladder(monkeypatch):
    """The point of the width fix, stated as a probability rather than a sigma.

    THE STATISTIC IS THE BAND CONTAINING THE CENTRE, not the largest
    probability on the ladder. The first draft asserted the latter and failed
    correctly: this ladder's outer bands are open-ended, so a WIDER
    distribution raises their probability without disagreeing at all with the
    claim being made. On a Polymarket ladder the outer buckets really are
    open-ended, so that is the shape to test against rather than around.

    bias -0.5 puts the centre at 20.5, the middle of the closed band, which is
    where a narrowing has somewhere to concentrate.
    """
    wide = _price(monkeypatch, pp=_pp(bias=-0.5, ratio=1.0, baseline=3.0))[0]
    tight = _price(monkeypatch, pp=_pp(bias=-0.5, ratio=1.0, baseline=1.0))[0]
    mid = lambda rows: next(r["raw_prob"] for r in rows if r["band_id"] == "mid")
    assert mid(tight) > mid(wide)
    assert mid(tight) > 2 * mid(wide), (mid(tight), mid(wide))


# ---------------------------------------------------------------------------
# The cliff this replaces.
# ---------------------------------------------------------------------------
def test_a_borrowed_skill_row_still_zeroes_the_bias_without_a_fit(monkeypatch):
    """The live defect, pinned so the fix cannot be mistaken for a no-op:
    with no fitted row, a proxy lead still publishes bias 0."""
    rows, _reg, _why = _price(monkeypatch, pp=None, proxy=True, skill_bias=0.7)
    assert rows[0]["skill_proxy"] is True
    assert rows[0]["bias_applied_c"] == 0.0


def test_a_fitted_correction_survives_a_borrowed_skill_row(monkeypatch):
    """And the fix: shrinkage already handles thin evidence continuously, so
    a cell fitted for THIS lead is not thrown away because the width had to
    be borrowed from the next one."""
    rows, _reg, _why = _price(monkeypatch, pp=_pp(bias=1.2), proxy=True)
    assert rows[0]["skill_proxy"] is True
    assert rows[0]["bias_applied_c"] == pytest.approx(1.2)


# ---------------------------------------------------------------------------
# What it must NOT stack with.
# ---------------------------------------------------------------------------
def test_the_calibration_multiplier_does_not_narrow_a_fitted_width_again(monkeypatch):
    """ad4_45 measures the SAME over-dispersion from the other end. Applying
    it to a width already fitted to those residuals corrects twice for one
    measurement."""
    cal = (0.60, {"n_days": 90, "sigma_multiplier": 0.60})
    with_cal = _price(monkeypatch, pp=_pp(), cal=cal)[0]
    without = _price(monkeypatch, pp=_pp(), cal=(1.0, None))[0]
    assert with_cal[0]["sigma_c"] == pytest.approx(without[0]["sigma_c"])


def test_the_calibration_multiplier_still_applies_where_there_is_no_fit(monkeypatch):
    """Removing it from the fitted path must not remove it from the other one."""
    cal = (1.50, {"n_days": 90, "sigma_multiplier": 1.50})
    widened = _price(monkeypatch, pp=None, cal=cal)[0]
    plain = _price(monkeypatch, pp=None, cal=(1.0, None))[0]
    assert widened[0]["sigma_c"] > plain[0]["sigma_c"]


def test_a_promoted_model_is_not_given_the_public_forecasts_station_bias(monkeypatch):
    """The correction is fitted on the PUBLIC forecast's errors. A centre from
    the desk's own model is a different number with different errors, and
    subtracting this bias from it would correct the wrong thing."""
    promoted = {("london", 0): {"city_key": "london", "lead_days": 0,
                                "model_version": "london:max_c:abc", "model_mae_c": 0.8,
                                "gain_vs_public_c": 0.42, "n_days": 64}}
    mfc = {("london", "2026-09-19"): {
        "city_key": "london", "for_date": "2026-09-19", "lead_days": 0,
        "run_at": "2026-09-19T06:00:00+00:00", "predicted_max_c": 26.0,
        "model_version": "london:max_c:abc"}}
    rows, _reg, why = _price(monkeypatch, pp=_pp(bias=1.2),
                             promoted=promoted, model_forecasts=mfc)
    assert rows[0]["forecast_max_c"] == 26.0
    assert rows[0]["bias_applied_c"] == 0.0
    assert not any("postprocessed" in r for r in why)


# ---------------------------------------------------------------------------
# Safe defaults.
# ---------------------------------------------------------------------------
def test_no_fitted_row_prices_exactly_as_before(monkeypatch):
    """An empty table is the state on any desk that has not run the fitter,
    and it has to mean "unchanged", not "unpriced"."""
    before = _price(monkeypatch, pp=None)
    assert before is not None
    assert before[0][0]["bias_applied_c"] == pytest.approx(0.5)
    assert before[0][0]["sigma_c"] == pytest.approx(2.0 * pe.MAE_TO_SIGMA)


@pytest.mark.parametrize("bad", [
    {"bias_c": 1.0, "sigma_ratio": 0.8, "baseline_sigma_c": None},
    {"bias_c": 1.0, "sigma_ratio": 0.8, "baseline_sigma_c": 0.0},
    {"bias_c": 1.0, "sigma_ratio": 0.0, "baseline_sigma_c": 1.5},
    {"bias_c": 1.0, "sigma_ratio": -0.5, "baseline_sigma_c": 1.5},
])
def test_a_row_that_cannot_make_a_distribution_is_ignored(monkeypatch, bad):
    """_postprocess_for screens these before the engine ever sees them; a
    sigma of zero would divide by zero in the normal CDF and a negative one
    would invert the ladder."""
    monkeypatch.setattr(pe, "rest", lambda *_a, **_k: [dict(bad, city_key="london", lead_days=0)])
    monkeypatch.setattr(pe, "_postprocess_cache", None)
    assert pe._postprocess_for("london", 0) is None


def test_a_missing_view_does_not_stop_the_engine(monkeypatch):
    def _boom(*_a, **_k):
        raise RuntimeError("relation v_forecast_postprocess_applied does not exist")
    monkeypatch.setattr(pe, "rest", _boom)
    monkeypatch.setattr(pe, "_postprocess_cache", None)
    assert pe._postprocess_for("london", 0) is None


def test_a_city_lead_with_no_row_is_not_given_another_ones(monkeypatch):
    monkeypatch.setattr(pe, "rest", lambda *_a, **_k: [
        {"city_key": "london", "lead_days": 1, "bias_c": 1.2,
         "sigma_ratio": 0.8, "baseline_sigma_c": 1.5}])
    monkeypatch.setattr(pe, "_postprocess_cache", None)
    assert pe._postprocess_for("london", 1) is not None
    assert pe._postprocess_for("london", 0) is None
    assert pe._postprocess_for("paris", 1) is None


# ---------------------------------------------------------------------------
# Traceability.
# ---------------------------------------------------------------------------
def test_a_moved_price_says_what_moved_it(monkeypatch):
    """A price that changed and cannot say why looks like drift, and drift is
    the one thing a desk must never have to guess about."""
    _rows, _reg, why = _price(monkeypatch, pp=_pp(bias=1.2, ratio=0.8, n_days=12,
                                                  n_city=110, gain=0.031))
    line = next((r for r in why if r.startswith("postprocessed")), None)
    assert line is not None, why
    assert "+1.20C" in line and "0.80x" in line
    assert "12d_cell" in line and "110d_city" in line
    assert "0.031" in line
