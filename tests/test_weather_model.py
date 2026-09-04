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


# ------------------------------------------------------ predicting forward --
# The fit only ever explained days that had already happened. These cover the
# step that turns it into a forecast: applying the same coefficients to
# weather_forecast_features, whose columns carry the same names on purpose.
def fc_days(n, *, start="2026-09-05", cloud=4.0, run="2026-09-04T12:00:00+00:00"):
    d0 = __import__("datetime").date.fromisoformat(start)
    return [{
        "city_key": "t",
        "for_date": (d0 + __import__("datetime").timedelta(days=i)).isoformat(),
        "run_at": run, "lead_days": i + 1, "forecast_max_c": 25.0,
        "morning_temp_c": 14.0, "dewpoint_depression_c": 9.0,
        "cloud_mean": cloud, "wind_mean": 5.0, "precip_total": 0.0,
    } for i in range(n)]


def fitted():
    fit, _ = wm.fit_city(synth(400), wm.FEATURES)
    return fit


def test_the_first_day_is_anchored_on_an_observation_and_the_rest_are_chained():
    """Only day one has a real yesterday. Every day after it rests on the
    model's own previous answer, and the row has to say which it is - a day-5
    prediction built on four of its own guesses is not the same object as a
    day-0 one."""
    rows = wm.forecast_city("t", fitted(), fc_days(4), last_observed_max=26.0)
    assert [r["prev_source"] for r in rows] == ["observed", "chained", "chained", "chained"]
    assert rows[0]["prev_max_c"] == pytest.approx(26.0)
    # each row carries forward exactly the previous row's prediction
    for a, b in zip(rows, rows[1:]):
        assert b["prev_max_c"] == pytest.approx(a["predicted_max_c"])


def test_a_gap_in_the_series_breaks_the_chain_rather_than_stepping_over_it():
    """Chaining across a missing day would pass a two-day-old prediction off
    as yesterday's number, and nothing downstream could tell."""
    days = fc_days(2) + fc_days(1, start="2026-09-09")
    rows = wm.forecast_city("t", fitted(), days, last_observed_max=26.0)
    # the day after the gap has no usable prev_max_c, so it is not predicted
    assert [r["for_date"] for r in rows] == ["2026-09-05", "2026-09-06"]


def test_cloud_still_costs_what_the_fit_said_it_costs():
    """The whole design rests on the coefficients applying unchanged to
    forecast columns. A clear forecast day must come out hotter than an
    overcast one by the fitted cloud coefficient times the difference."""
    fit = fitted()
    clear = wm.forecast_city("t", fit, fc_days(1, cloud=0.0), 26.0)[0]
    overcast = wm.forecast_city("t", fit, fc_days(1, cloud=8.0), 26.0)[0]
    expected = fit["coefficients"]["cloud_mean"] * 8.0
    assert (overcast["predicted_max_c"] - clear["predicted_max_c"]) == pytest.approx(
        expected, abs=0.02)


def test_the_contributions_add_up_to_the_prediction():
    """Stored so the UI shows the arithmetic the number was actually made
    from. If they do not sum to it, the explanation is decoration."""
    row = wm.forecast_city("t", fitted(), fc_days(1), 26.0)[0]
    assert sum(row["contributions"].values()) == pytest.approx(
        row["predicted_max_c"], abs=0.02)


def test_a_forecast_day_missing_a_feature_is_not_guessed_at():
    days = fc_days(2)
    days[0]["cloud_mean"] = None
    rows = wm.forecast_city("t", fitted(), days, 26.0)
    # and the missing day breaks the chain rather than being skipped silently
    assert rows == []


def test_the_model_s_measured_skill_travels_with_every_prediction():
    """A prediction from a model that loses to persistence is still written -
    the point is that it is written MARKED, so nothing downstream trusts it
    blind."""
    fit = fitted()
    row = wm.forecast_city("t", fit, fc_days(1), 26.0)[0]
    assert row["model_mae_c"] == fit["mae_c"]
    assert row["persistence_mae_c"] == fit["persistence_mae_c"]
    assert row["beats_persistence"] == fit["beats_persistence"]
    assert row["nws_max_c"] == 25.0        # carried for comparison, never used as input


def test_the_chain_is_anchored_on_the_day_before_not_just_any_recent_day(monkeypatch):
    """The anchor must be the day BEFORE the first forecast day. Reaching
    further back is allowed within a few days - an ingest can be late - but
    the row still has to be built from the nearest real maximum available,
    not the newest row in the table."""
    days = fc_days(2)                                  # 2026-09-05, 09-06
    monkeypatch.setattr(wm, "rest", lambda *a, **k: days)
    observed = [
        {"obs_date": "2026-09-01", "max_c": 40.0, "n_obs": 24},   # too old to use
        {"obs_date": "2026-09-04", "max_c": 26.0, "n_obs": 24},   # the day before
    ]
    preds, note = wm.predict_forward({"t": fitted()}, {"t": observed})
    assert note is None
    assert preds[0]["prev_max_c"] == pytest.approx(26.0)
    assert preds[0]["prev_source"] == "observed"


def test_a_city_with_no_recent_observation_is_named_not_silently_dropped(monkeypatch):
    monkeypatch.setattr(wm, "rest", lambda *a, **k: fc_days(2))
    preds, note = wm.predict_forward(
        {"t": fitted()}, {"t": [{"obs_date": "2026-08-01", "max_c": 26.0, "n_obs": 24}]})
    assert preds == []
    assert "observed maximum" in note and "P1.2" in note


def test_a_thin_observed_day_cannot_anchor_a_chain(monkeypatch):
    """n_obs < 12 means the day's maximum is understated - the same rule the
    fit uses. Anchoring on one would bias every day chained off it."""
    monkeypatch.setattr(wm, "rest", lambda *a, **k: fc_days(2))
    preds, _ = wm.predict_forward(
        {"t": fitted()}, {"t": [{"obs_date": "2026-09-04", "max_c": 26.0, "n_obs": 3}]})
    assert preds == []


def test_no_forecast_rows_says_which_job_fills_them(monkeypatch):
    monkeypatch.setattr(wm, "rest", lambda *a, **k: [])
    preds, note = wm.predict_forward({"t": fitted()}, {"t": []})
    assert preds == []
    assert "P1.4" in note


def test_a_stored_fit_uses_the_features_it_was_actually_fitted_with(monkeypatch):
    """A city whose fit dropped a zero-variance feature has fewer coefficients
    than FEATURES does. Predicting with FEATURES would look up a coefficient
    that was never fitted, and the run would die on the city that needed the
    drop most."""
    stored = [{
        "city_key": "t", "target": "max_c",
        # no precip_total: it never varied in that city's training window
        "coefficients": {"intercept": 10.0, "prev_max_c": 0.1, "morning_temp_c": 0.9,
                         "dewpoint_depression_c": 0.35, "cloud_mean": -1.1,
                         "wind_mean": 0.0},
        "mae_c": 0.9, "persistence_mae_c": 1.6, "beats_persistence": True,
    }]
    monkeypatch.setattr(wm, "rest", lambda *a, **k: stored)
    fits = wm.stored_fits()
    assert "precip_total" not in fits["t"]["features"]

    rows = wm.forecast_city("t", fits["t"], fc_days(1), 26.0)
    assert len(rows) == 1
    assert "precip_total" not in rows[0]["contributions"]
    assert rows[0]["model_mae_c"] == 0.9


def test_a_stored_row_with_no_intercept_is_not_used(monkeypatch):
    """An empty or half-written coefficients blob would silently predict from
    a missing intercept, i.e. from zero."""
    monkeypatch.setattr(wm, "rest", lambda *a, **k: [
        {"city_key": "t", "target": "max_c", "coefficients": {"cloud_mean": -1.1}},
        {"city_key": "u", "target": "max_c", "coefficients": None},
    ])
    assert wm.stored_fits() == {}


def test_every_key_written_is_a_column_that_exists():
    """PostgREST rejects the whole batch on one unknown column, and the run
    would fail with a 400 that names the column but not the cause. Checked
    against the DDL rather than a copy of it, so a rename in either place has
    to be made in both."""
    import pathlib
    import re

    ddl = pathlib.Path(__file__).resolve().parents[1] / "sql" / "ad4_25_model_forecast.sql"
    body = re.search(r"create table if not exists derived_model_forecast \((.*?)\n\);",
                     ddl.read_text(), re.S).group(1)
    columns = {
        m.group(1) for line in body.splitlines()
        if (m := re.match(r"\s{2}([a-z_]+)\s+\S", line))
    }
    assert "predicted_max_c" in columns, "the DDL was not parsed"

    row = wm.forecast_city("t", fitted(), fc_days(1), 26.0)[0]
    assert set(row) <= columns, sorted(set(row) - columns)
