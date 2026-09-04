"""The morning-conditions model, checked against physics it must recover.

Two things make this dangerous rather than merely wrong if it misbehaves:

  It can INVENT SKILL by leaking. Weather is strongly autocorrelated, so a
  shuffled train/test split lets the model see the days on either side of the
  ones it is scored on. That alone produces a model that looks good and is
  worthless. The split here is in time order and the test enforces it.

  It can LOOK like it beats persistence when it does not. Persistence -
  yesterday's max, unchanged - is the benchmark, and a model scored against a
  persistence figure computed on different days is not being compared to
  anything.
"""
import math
import random

import pytest

import weather_model as wm


def synth(n, *, cloud_effect=-1.1, dry_effect=0.35, noise=0.4, seed=11, start=None):
    """Days whose maximum follows a KNOWN rule, so the fit has a right answer:

        max = morning + 10 - 1.1*cloud + 0.35*dryness + noise
    """
    rng = random.Random(seed)
    rows, prev = [], None
    for i in range(n):
        cloud = rng.uniform(0, 8)
        dry = rng.uniform(0, 18)
        morning = 12 + 4 * math.sin(i / 30.0)
        mx = morning + 10 + cloud_effect * cloud + dry_effect * dry + rng.gauss(0, noise)
        rows.append({
            "city_key": "t", "obs_date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
            "max_c": mx, "n_obs": 24, "prev_max_c": prev if prev is not None else mx,
            "morning_temp_c": morning, "dewpoint_depression_c": dry,
            "cloud_mean": cloud, "wind_mean": 5.0, "precip_total": 0.0,
        })
        prev = mx
    return rows


def test_solve_handles_a_simple_system():
    x = wm.solve([[2.0, 1.0], [1.0, 3.0]], [5.0, 10.0])
    assert x[0] == pytest.approx(1.0)
    assert x[1] == pytest.approx(3.0)


def test_solve_refuses_a_singular_system():
    """Collinear features have no unique fit. Returning None beats returning
    numbers that happen to satisfy one of infinitely many solutions."""
    assert wm.solve([[1.0, 2.0], [2.0, 4.0]], [3.0, 6.0]) is None


def test_the_fit_recovers_the_coefficients_it_was_given():
    fit, _ = wm.fit_city(synth(400), wm.FEATURES)
    assert fit is not None
    c = fit["coefficients"]
    assert c["cloud_mean"] == pytest.approx(-1.1, abs=0.15), c
    assert c["dewpoint_depression_c"] == pytest.approx(0.35, abs=0.12), c


def test_it_beats_persistence_when_the_signal_is_real():
    fit, _ = wm.fit_city(synth(400), wm.FEATURES)
    assert fit["beats_persistence"], fit
    assert fit["mae_c"] < fit["persistence_mae_c"]


def test_it_admits_defeat_when_there_is_no_signal():
    """Pure random walk: nothing in the morning predicts the afternoon. The
    model must NOT claim to beat persistence - saying so is the whole value."""
    rng = random.Random(3)
    rows, prev = [], 20.0
    for i in range(400):
        prev = prev + rng.gauss(0, 2.0)
        rows.append({
            "city_key": "t", "obs_date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
            "max_c": prev, "n_obs": 24,
            "prev_max_c": rows[-1]["max_c"] if rows else prev,
            "morning_temp_c": rng.uniform(5, 15), "dewpoint_depression_c": rng.uniform(0, 18),
            "cloud_mean": rng.uniform(0, 8), "wind_mean": rng.uniform(0, 20),
            "precip_total": 0.0,
        })
    fit, _ = wm.fit_city(rows, wm.FEATURES)
    assert fit is not None
    assert not fit["beats_persistence"], (
        "a model fitted on noise claimed to beat persistence; the holdout is leaking")


def test_the_holdout_is_the_LAST_days_not_a_random_sample():
    """Weather is autocorrelated. A shuffled split lets the model see the days
    surrounding the ones it is scored on, which manufactures skill."""
    rows = synth(400)
    fit, _ = wm.fit_city(rows, wm.FEATURES)
    n = fit["n_days"]
    assert fit["n_test"] == n - fit["n_train"]
    assert fit["n_train"] == int(n * (1 - wm.HOLDOUT))
    # scrambling the input must not change the split, because fit_city sorts
    shuffled = rows[:]
    random.Random(5).shuffle(shuffled)
    again, _ = wm.fit_city(shuffled, wm.FEATURES)
    assert again["n_train"] == fit["n_train"]
    assert again["mae_c"] == pytest.approx(fit["mae_c"], abs=1e-9), (
        "the split must depend on the DATE, not on input order")


def test_too_few_days_is_no_model_at_all():
    assert wm.fit_city(synth(40), wm.FEATURES)[0] is None


def test_rows_missing_a_feature_are_dropped_not_defaulted():
    rows = synth(300)
    for r in rows[:100]:
        r["cloud_mean"] = None
    fit, _ = wm.fit_city(rows, wm.FEATURES)
    assert fit is None or fit["n_days"] == 200, "a null feature must not become a zero"


def test_a_thin_day_is_excluded():
    """Fewer than 12 observations means the day's maximum is not trustworthy."""
    rows = synth(300)
    for r in rows:
        r["n_obs"] = 3
    assert wm.fit_city(rows, wm.FEATURES)[0] is None


def test_the_description_names_the_drivers():
    fit, _ = wm.fit_city(synth(400), wm.FEATURES)
    text = wm.describe(fit)
    assert "cloud" in text
    assert "dry" in text or "dryness" in text
