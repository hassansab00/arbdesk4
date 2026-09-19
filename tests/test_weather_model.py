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
import os
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


def test_rows_missing_a_BASE_feature_are_dropped_not_defaulted():
    """A base feature is mandatory: a day without one is not a usable day.

    The thing that must never happen is a missing value becoming a zero - a
    day with no wind reading is not a windless day, and fitting it as one puts
    a coefficient on an invention.
    """
    rows = synth(300)
    for r in rows[:100]:
        r["wind_mean"] = None
    fit, _ = wm.fit_city(rows, wm.FEATURES)
    assert fit is None or fit["n_days"] == 200, "a null feature must not become a zero"


def test_rows_missing_an_OPTIONAL_feature_keep_the_day_and_lose_the_feature():
    """The other half, and the one that cost the platform every model it had.

    cloud_mean was mandatory. Measured on the live cache: 0 of 53 cities had
    120 cloud-complete days and 52 had them without it, so requiring cloud
    meant fitting nothing at all - 959 usable city-days instead of 21,631. A
    missing optional feature costs the city that feature, never the day.
    """
    rows = synth(300)
    for r in rows[:100]:
        r["cloud_mean"] = None
    fit, _ = wm.fit_city(rows, wm.FEATURES)
    assert fit is not None, "a city with cloud on two thirds of its days is fittable"
    assert fit["n_days"] == 300, (
        "a day is still a day without a cloud reading - the feature is optional")
    assert "cloud_mean" not in fit["features"], (
        "a feature absent from a third of the training days must not be fitted "
        "on the rest and then applied to all of them")
    verdicts = {v["feature"]: v["verdict"] for v in fit["selection"]}
    assert "not present on every training day" in verdicts.get("cloud_mean", ""), verdicts


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
    monkeypatch.setattr(wm, "rest_all", lambda *a, **k: days)
    observed = [
        {"obs_date": "2026-09-01", "max_c": 40.0, "n_obs": 24},   # too old to use
        {"obs_date": "2026-09-04", "max_c": 26.0, "n_obs": 24},   # the day before
    ]
    preds, note = wm.predict_forward({"t": fitted()}, {"t": observed})
    assert note is None
    assert preds[0]["prev_max_c"] == pytest.approx(26.0)
    assert preds[0]["prev_source"] == "observed"


def test_a_city_with_no_recent_observation_is_named_not_silently_dropped(monkeypatch):
    monkeypatch.setattr(wm, "rest_all", lambda *a, **k: fc_days(2))
    preds, note = wm.predict_forward(
        {"t": fitted()}, {"t": [{"obs_date": "2026-08-01", "max_c": 26.0, "n_obs": 24}]})
    assert preds == []
    assert "observed maximum" in note and "P1.2" in note


def test_a_thin_observed_day_cannot_anchor_a_chain(monkeypatch):
    """n_obs < 12 means the day's maximum is understated - the same rule the
    fit uses. Anchoring on one would bias every day chained off it."""
    monkeypatch.setattr(wm, "rest_all", lambda *a, **k: fc_days(2))
    preds, _ = wm.predict_forward(
        {"t": fitted()}, {"t": [{"obs_date": "2026-09-04", "max_c": 26.0, "n_obs": 3}]})
    assert preds == []


def test_no_forecast_rows_says_which_job_fills_them(monkeypatch):
    monkeypatch.setattr(wm, "rest_all", lambda *a, **k: [])
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
    monkeypatch.setattr(wm, "rest_all", lambda *a, **k: stored)
    fits = wm.stored_fits()
    assert "precip_total" not in fits["t"]["features"]

    rows = wm.forecast_city("t", fits["t"], fc_days(1), 26.0)
    assert len(rows) == 1
    assert "precip_total" not in rows[0]["contributions"]
    assert rows[0]["model_mae_c"] == 0.9


def test_a_stored_row_with_no_intercept_is_not_used(monkeypatch):
    """An empty or half-written coefficients blob would silently predict from
    a missing intercept, i.e. from zero."""
    monkeypatch.setattr(wm, "rest_all", lambda *a, **k: [
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
    # A column added later is still a column. The sibling test for
    # derived_weather_model already collected these; this one did not, so
    # model_version - added by an alter so an existing table gains it without
    # being dropped - read as an unknown column.
    columns |= set(re.findall(
        r"alter table derived_model_forecast add column if not exists ([a-z_]+)",
        ddl.read_text()))
    assert "predicted_max_c" in columns, "the DDL was not parsed"

    row = wm.forecast_city("t", fitted(), fc_days(1), 26.0)[0]
    assert set(row) <= columns, sorted(set(row) - columns)


# ------------------------------------------------- earning a place ---------
# Six features to nine on a few hundred days is exactly how a model learns the
# noise in its own training set. These decide whether the selection is real.
def synth_plus(n, *, humidity_effect=0.0, noise=0.4, seed=7, collinear=False):
    """The same known rule as synth(), plus the three unused variables.

    humidity_effect=0 makes morning_humidity pure noise - it must be rejected.
    Give it a coefficient and it must be kept.
    """
    rng = random.Random(seed)
    rows, prev = [], None
    for i in range(n):
        cloud = rng.uniform(0, 8)
        dry = rng.uniform(0, 18)
        hum = rng.uniform(20, 95)
        morning = 12 + 4 * math.sin(i / 30.0)
        mx = (morning + 10 - 1.1 * cloud + 0.35 * dry
              + humidity_effect * hum + rng.gauss(0, noise))
        rows.append({
            "city_key": "t", "obs_date": f"2026-{1 + i // 28:02d}-{1 + i % 28:02d}",
            "max_c": mx, "n_obs": 24, "prev_max_c": prev if prev is not None else mx,
            "morning_temp_c": morning, "dewpoint_depression_c": dry,
            "cloud_mean": cloud, "wind_mean": 5.0 + rng.uniform(-1, 1),
            "precip_total": 0.0,
            # a near-duplicate of cloud_mean when collinear=True
            "cloud_max": cloud * 1.0001 + 1e-6 if collinear else min(8, cloud + rng.uniform(0, 2)),
            "morning_humidity": hum,
            "wind_max": 7.0 + rng.uniform(-1, 1),
        })
        prev = mx
    return rows


def verdicts(fit):
    return {s["feature"]: s["verdict"] for s in fit["selection"]}


def test_a_useless_feature_is_rejected_and_says_so():
    """morning_humidity is pure noise here. Keeping it would still lower the
    error on the training days, which is the whole reason selection has to be
    judged somewhere else."""
    fit, _ = wm.fit_city(synth_plus(400, humidity_effect=0.0), wm.BASE_FEATURES)
    assert "morning_humidity" not in fit["features"]
    assert fit["added_features"] == [] or "morning_humidity" not in fit["added_features"]
    assert any("no material improvement" in v for v in verdicts(fit).values())


def test_a_real_feature_is_kept_and_the_reason_is_recorded():
    fit, _ = wm.fit_city(synth_plus(400, humidity_effect=0.12, noise=0.3), wm.BASE_FEATURES)
    assert "morning_humidity" in fit["features"], verdicts(fit)
    assert "kept" in verdicts(fit)["morning_humidity"]


def test_a_collinear_feature_is_refused_by_name():
    """cloud_max as a near-copy of cloud_mean. The normal equations would still
    return numbers - enormous ones of opposite sign that cancel - and they would
    move wildly with one more day of data."""
    fit, _ = wm.fit_city(synth_plus(400, collinear=True), wm.BASE_FEATURES)
    v = verdicts(fit)
    assert "cloud_max" not in fit["features"]
    assert "collinear with cloud_mean" in v.get("cloud_max", ""), v


def test_selection_never_looks_at_the_holdout():
    """The holdout's one job is to be the first data the finished model has
    ever seen. If selection could see it, its score would describe the
    selection rather than the model - so removing the last quarter must not
    change which features were chosen."""
    # Both sets share their first 300 days, so the TRAINING window is
    # identical and only the holdout differs. Trimming the list instead would
    # have changed the training data too and tested nothing.
    shared = synth_plus(400, humidity_effect=0.12, noise=0.3, seed=7)[:300]
    a = shared + synth_plus(400, humidity_effect=0.12, noise=0.3, seed=11)[300:]
    b = shared + synth_plus(400, humidity_effect=0.12, noise=0.3, seed=99)[300:]
    fa, _ = wm.fit_city(a, wm.BASE_FEATURES)
    fb, _ = wm.fit_city(b, wm.BASE_FEATURES)
    assert fa["features"] == fb["features"], (fa["features"], fb["features"])


def test_a_leaky_feature_is_refused_loudly():
    """diurnal_range_c is max_c - min_c. A model using it reports a spectacular
    error and is worth nothing forward, and the number that would give it away
    is the one it improves - so this has to raise, not warn."""
    for leak in ("diurnal_range_c", "morning_to_max_c", "max_c"):
        with pytest.raises(ValueError, match="derived from it|contain the target"):
            wm.fit_city(synth_plus(200), wm.BASE_FEATURES + [leak])


def test_the_new_features_exist_on_both_sides_of_the_join():
    """A feature the FORECAST table lacks can be fitted and then never applied,
    and the failure is silent - forecast_city skips any row missing a feature.
    Checked against the two DDLs rather than a copy of them."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "sql"
    fc = re.search(r"create table if not exists weather_forecast_features \((.*?)\n\);",
                   (root / "ad4_24_nws_gridpoint.sql").read_text(), re.S).group(1)
    forecast_cols = {m.group(1) for line in fc.splitlines()
                     if (m := re.match(r"\s{2}([a-z_]+)\s+\S", line))}
    observed = (root / "ad4_21_weather_features.sql").read_text()

    for f in wm.CANDIDATE_FEATURES:
        assert f in forecast_cols, f"{f} is not in weather_forecast_features"
        assert f in observed, f"{f} is not in v_city_day_features"


def test_every_key_written_to_the_model_table_is_a_column():
    """The same failure mode as derived_model_forecast: PostgREST rejects the
    whole batch on one unknown column, and the 400 names the column but not the
    cause. `selection` was added to the payload before it existed in the DDL."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1]
    ddl = (root / "sql" / "ad4_21_weather_features.sql").read_text()
    body = re.search(r"create table if not exists derived_weather_model \((.*?)\n\);", ddl, re.S).group(1)
    cols = {m.group(1) for line in body.splitlines()
            if (m := re.match(r"\s{2}([a-z_]+)\s+\S", line))}
    cols |= set(re.findall(r"alter table derived_weather_model add column if not exists ([a-z_]+)", ddl))
    assert "coefficients" in cols, "the DDL was not parsed"

    src = (root / "scripts" / "weather_model.py").read_text()
    # the derived_weather_model payload specifically - forecast_city has an
    # out.append too, and matching the first one tested the wrong table
    payload = re.search(r'out\.append\(\{\s*\n\s*"city_key": city, "target": "max_c"(.*?)\n        \}\)',
                        src, re.S).group(1)
    written = set(re.findall(r'"([a-z_]+)":', payload))
    assert written <= cols, sorted(written - cols)


def test_the_cache_does_not_smuggle_a_leaky_column_into_the_model():
    """sql/ad4_28 materialises v_city_day_features - diurnal_range_c included,
    because the cache mirrors the view. That column must still be unusable: a
    cache is a performance decision, never a change to what may be fitted."""
    import pathlib
    import re

    root = pathlib.Path(__file__).resolve().parents[1] / "sql"
    ddl = (root / "ad4_28_feature_cache.sql").read_text()
    body = re.search(r"create table if not exists derived_city_day_features \((.*?)\n\);", ddl, re.S).group(1)
    cached = {m.group(1) for line in body.splitlines()
              if (m := re.match(r"\s{2}([a-z_0-9]+)\s+\S", line))}
    assert "diurnal_range_c" in cached, "the cache no longer mirrors the view"

    # every leaky column that reached the cache is still refused by the fit
    for col in wm.LEAKY_FEATURES & cached:
        with pytest.raises(ValueError):
            wm.fit_city(synth_plus(200), wm.BASE_FEATURES + [col])


# ---------------------------------------------------- the archive round trip --
# weather_observations is ~98% of the database. Moving it out is only safe if
# the file that replaces it is complete, so the export is checked here the same
# way the script checks it in production: count the rows back.
def test_the_archive_round_trips_every_row():
    import importlib.util
    import pathlib

    root = pathlib.Path(__file__).resolve().parents[1]
    spec = importlib.util.spec_from_file_location(
        "archive_observations", root / "scripts" / "archive_observations.py")
    mod = importlib.util.module_from_spec(spec)
    import sys as _s
    _s.path.insert(0, str(root / "scripts"))
    spec.loader.exec_module(mod)

    rows = [
        {"city_key": "nyc", "station": "KNYC", "valid_at": "2026-01-01T12:00:00+00:00",
         "temp_c": 4.4, "temp_f": 39.9, "dewpoint_c": -1.1, "humidity": 68,
         "wind_speed": 9.7, "wind_dir_deg": 250, "precip": 0.0, "cloud_cover": 6,
         "pressure_hpa": 1013.2, "source": "IEM"},
        # nulls, commas and quotes are what break a naive CSV writer
        {"city_key": "beirut", "station": None, "valid_at": "2026-01-01T13:00:00+00:00",
         "temp_c": None, "temp_f": None, "dewpoint_c": None, "humidity": None,
         "wind_speed": None, "wind_dir_deg": None, "precip": None,
         "cloud_cover": None, "pressure_hpa": None, "source": 'a,b"c'},
    ]
    # Drive the REAL export path - it streams now, and the paging, the CSV
    # quoting and the count all have to hold together.
    served = [dict(r, obs_id=i + 1) for i, r in enumerate(rows)]

    def fake_rest(table, params):
        after = next((int(v[3:]) for k, v in params if k == "obs_id"), 0)
        return [r for r in served if r["obs_id"] > after]

    mod.rest = fake_rest
    import datetime as _dt
    blob, n, lo, hi = mod.export_cold(mod.TABLES["observations"],
                                      _dt.datetime(2027, 1, 1, tzinfo=_dt.timezone.utc))
    assert n == len(rows)
    assert mod.count_rows(blob) == len(rows), "the verify step would pass a short file"
    assert lo == "2026-01-01T12:00:00+00:00" and hi == "2026-01-01T13:00:00+00:00", (lo, hi)

    import csv as _csv
    import gzip as _gzip
    import io as _io
    back = list(_csv.DictReader(_io.StringIO(_gzip.decompress(blob).decode())))
    assert len(back) == 2
    assert back[0]["city_key"] == "nyc" and back[0]["temp_c"] == "4.4"
    assert back[1]["source"] == 'a,b"c', "quoting is not round-tripping"
    assert back[1]["temp_c"] == "", "a null must not become the string None"
    assert "obs_id" not in back[0], "the paging key is not part of the archive"


def test_the_archive_refuses_a_window_too_small_to_model_on():
    """THE FLOOR MOVED, because one number could not serve four tables.

    The script used to refuse any --keep-days under 30. That protected the
    weather and trade archives, whose models need months, and made
    research_captures impossible to archive at all: its entire history is five
    days, so a 30-day floor meant the job ran, succeeded, and deleted nothing
    while the table added ~29 MB a day.

    Each prune RPC now carries its own floor - 30 for trades, 2 for research -
    which is also the only place a typo cannot route around, since the script
    is one caller of several and n8n or psql can call the RPC directly. The
    script keeps the one check that is table-independent: a window below a day
    is nonsense whatever the table.
    """
    import subprocess
    import sys as _s
    r = subprocess.run([_s.executable, "scripts/archive_observations.py",
                        "--keep-days", "0"], capture_output=True, text=True,
                       cwd=str(pathlib_root()), env={**os.environ, "PYTHONPATH": "scripts"})
    assert r.returncode == 1
    assert "at least 1" in r.stderr

    root = pathlib_root()
    trades = (root / "sql" / "ad4_65_prune_trades.sql").read_text(encoding="utf-8")
    assert "p_keep_days < 30" in trades, (
        "the 30-day floor must survive where it matters - the 24h volume "
        "window needs room to be wrong")


def pathlib_root():
    import pathlib
    return pathlib.Path(__file__).resolve().parents[1]
