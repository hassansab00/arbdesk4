"""Six cities fitted a model and then predicted nothing, and the run was green.

Measured on the live database right after the 2026-09-19 fit:

  52 models written, 43 beating persistence.
  390 forward predictions - across 46 cities, not 52.

The six missing were exactly the six whose forward selection KEPT wind_max:
tokyo, san_francisco, qingdao, los_angeles, beijing, busan. Five of those six
beat persistence, so five shadow models were producing no forward evidence at
all, and sql/ad4_72_model_promotion needs 30 forward days before a model may
price anything. They would have sat in shadow forever.

The cause was a hand-written column list. predict_forward asked
v_forecast_features for cloud_mean but not wind_max, cloud_max or
morning_humidity, while select_features was free to keep any of the four. A
kept-but-not-fetched feature reads None on every forecast row, forecast_city
skips every row, and the city vanishes - no error, no note, exit 0. The data
was never missing: v_forecast_features carries all four, and every future row
for those six cities had a wind_max.

It is the exact failure BASE_FEATURES' own comment warns about ("a feature
present on only one side can be fitted and then never applied, and the failure
is silent"), arriving through the candidate set instead.

Two things hold it shut. The column list is DERIVED from the feature lists, so
a new candidate cannot be added without being fetched. And a city that had
forecast days and an anchor and still produced nothing is NAMED, so the next
version of this mistake cannot be silent even if the first guard is wrong.
"""

import datetime as dt

import pytest

import weather_model as wm


# The list as it was written by hand, before it was derived. Kept as data so
# the regression can be reproduced rather than described.
HAND_WRITTEN = [
    "city_key", "for_date", "run_at", "lead_days", "forecast_max_c",
    "morning_temp_c", "dewpoint_depression_c", "cloud_mean",
    "wind_mean", "precip_total",
]


def fit_with(*extra):
    """A fit that kept `extra` on top of the base features."""
    coef = {"intercept": 1.0, "prev_max_c": 0.5, "morning_temp_c": 0.4,
            "dewpoint_depression_c": 0.2, "wind_mean": -0.1, "precip_total": -0.5}
    coef.update({f: 0.05 for f in extra})
    return {"coefficients": coef,
            "features": [k for k in coef if k != "intercept"],
            "mae_c": 1.2, "persistence_mae_c": 2.0, "beats_persistence": True}


def forecast_rows(n=3):
    """Forecast days carrying EVERY column v_forecast_features really has."""
    d0 = dt.date.today()
    return [{
        "city_key": "t",
        "for_date": (d0 + dt.timedelta(days=i)).isoformat(),
        "run_at": "2026-09-19T21:00:00+00:00", "lead_days": i,
        "forecast_max_c": 25.0, "morning_temp_c": 14.0,
        "dewpoint_depression_c": 9.0, "morning_humidity": 70.0,
        "morning_pressure_hpa": 1014.2, "pressure_change_24h_hpa": -2.4,
        "cloud_mean": 4.0, "cloud_max": 8.0,
        "wind_mean": 5.0, "wind_max": 11.0, "precip_total": 0.0,
        "wind_u_mean": -0.62, "wind_v_mean": 0.31,
    } for i in range(n)]


def anchor_day():
    """An observed maximum for the day before the first forecast day."""
    return [{"obs_date": (dt.date.today() - dt.timedelta(days=1)).isoformat(),
             "max_c": 26.0, "n_obs": 24}]


def projecting_rest_all(rows, columns):
    """A rest_all that honours `select` the way PostgREST does.

    This is the whole point of the test. A fake that returns every column
    regardless of what was asked for cannot reproduce the bug, because the bug
    IS the gap between what the fit uses and what the read requests.
    """
    def _f(table, params, **kw):
        assert dict(params)["select"].split(",") == columns
        return [{k: r[k] for k in columns if k in r} for r in rows]
    return _f


def test_every_feature_a_fit_may_keep_is_fetched_forward():
    """The invariant, stated once. select_features may keep any base or
    candidate feature; each one must be in the forecast read."""
    keepable = [f for f in list(wm.BASE_FEATURES) + list(wm.CANDIDATE_FEATURES)
                if f != "prev_max_c" and f not in wm.DATE_DERIVED_FEATURES]
    missing = [f for f in keepable if f not in wm.FORECAST_COLUMNS]
    assert missing == [], f"fitted but never fetched: {missing}"


def test_the_derived_features_are_computed_not_fetched():
    """The one exemption, stated so it cannot quietly widen.

    A DATE_DERIVED_FEATURE has no column on either side - with_seasonal builds
    it from the row's own date - so asking the view for it would 400 the read
    and take every city down. The exemption is exactly that set and nothing
    else, and predict_forward must still put the values on the rows."""
    for f in wm.DATE_DERIVED_FEATURES:
        assert f in wm.CANDIDATE_FEATURES, f"{f} is exempt from a list it is not on"
        assert f not in wm.FORECAST_COLUMNS, f"{f} has no column to select"
        assert f not in wm.FIT_COLUMNS, f"{f} has no column to select"


def test_every_feature_a_fit_may_keep_is_read_from_the_cache():
    """The mirror of the test above, on the side that was left hand-written.

    FORECAST_COLUMNS was derived from the feature lists after a hand-written
    list silenced six cities. The fit's own read was not, and it drifted:
    the pressure candidates were added to CANDIDATE_FEATURES and never added
    to the select, so select_features was offered two variables that arrived
    None on every row of every city, and reported them as absent from the
    data when they were absent from the request."""
    keepable = [f for f in list(wm.BASE_FEATURES) + list(wm.CANDIDATE_FEATURES)
                if f not in wm.DATE_DERIVED_FEATURES]
    missing = [f for f in keepable if f not in wm.FIT_COLUMNS]
    assert missing == [], f"offered to the fit and never read: {missing}"
    for required in ("city_key", "obs_date", "max_c", "n_obs"):
        assert required in wm.FIT_COLUMNS, f"{required} is not read"


def test_the_fit_reads_the_derived_list_not_a_copy_of_it(monkeypatch):
    """Pins the wiring on the fit side, as the forecast side is pinned."""
    import pathlib
    src = (pathlib.Path(__file__).resolve().parents[1]
           / "scripts" / "weather_model.py").read_text(encoding="utf-8")
    assert '(",".join(FIT_COLUMNS))' in src.replace('", ".join', '",".join') or            '",".join(FIT_COLUMNS)' in src,         "the derived_city_day_features read must ask for FIT_COLUMNS"


def test_prev_max_c_is_not_asked_of_the_forecast_table():
    """It is a base feature, and it is NOT a forecast column - forecast_city
    supplies it from the anchor or the chain. Asking for it would 400 the
    whole read and take all 52 cities down, not six."""
    assert "prev_max_c" not in wm.FORECAST_COLUMNS


def test_the_read_asks_for_the_derived_list_not_a_copy_of_it(monkeypatch):
    """Pins the wiring: predict_forward must use FORECAST_COLUMNS, so changing
    the feature lists changes the read."""
    seen = {}

    def _f(table, params, **kw):
        seen["select"] = dict(params)["select"]
        return []

    monkeypatch.setattr(wm, "rest_all", _f)
    wm.predict_forward({"t": fit_with()}, {"t": anchor_day()})
    assert seen["select"] == ",".join(wm.FORECAST_COLUMNS)


@pytest.mark.parametrize("extra", wm.CANDIDATE_FEATURES)
def test_a_city_whose_fit_kept_a_candidate_still_predicts(monkeypatch, extra):
    """Every candidate, not just the one that bit. If forward selection can
    keep it, a city that keeps it must still produce forward rows."""
    monkeypatch.setattr(
        wm, "rest_all", projecting_rest_all(forecast_rows(), wm.FORECAST_COLUMNS))
    preds, note = wm.predict_forward({"t": fit_with(extra)}, {"t": anchor_day()})
    assert len(preds) == 3, f"keeping {extra} silenced the city"
    assert note is None
    assert extra in preds[0]["inputs"]


def test_the_hand_written_list_is_what_silenced_them(monkeypatch):
    """The regression itself. With the old list, a fit that kept wind_max
    produces nothing - which is what the live run did for six cities."""
    monkeypatch.setattr(wm, "FORECAST_COLUMNS", HAND_WRITTEN)
    monkeypatch.setattr(
        wm, "rest_all", projecting_rest_all(forecast_rows(), HAND_WRITTEN))
    preds, note = wm.predict_forward({"t": fit_with("wind_max")}, {"t": anchor_day()})
    assert preds == []
    # ...and the second guard: it is no longer silent about it.
    assert "no prediction" in note and "t" in note


def test_a_city_that_produced_nothing_is_named_not_just_absent(monkeypatch):
    """A city with days and an anchor that yields no row is a defect, not a
    quiet skip. The count and the name both have to appear."""
    rows = forecast_rows()
    for r in rows:
        r["wind_max"] = None          # present in the read, absent in the data
    monkeypatch.setattr(
        wm, "rest_all", projecting_rest_all(rows, wm.FORECAST_COLUMNS))
    preds, note = wm.predict_forward({"t": fit_with("wind_max")}, {"t": anchor_day()})
    assert preds == []
    assert note is not None and "1 city" in note and "t" in note


def test_a_city_that_did_produce_rows_is_not_named(monkeypatch):
    """The complement, so the note cannot be made true by always firing."""
    monkeypatch.setattr(
        wm, "rest_all", projecting_rest_all(forecast_rows(), wm.FORECAST_COLUMNS))
    preds, note = wm.predict_forward({"t": fit_with("wind_max")}, {"t": anchor_day()})
    assert len(preds) == 3
    assert note is None
