"""The model had no idea what month it was.

Every feature it could use described ONE MORNING - how warm it started, how
dry the air was, how hard the wind blew - plus yesterday's maximum carried
over. Measured on the feature lists as they stood on 2026-09-21, there was no
day-of-year term, no climatology, no seasonal anomaly, nothing. Seasonality
was handled entirely by refitting weekly, which moves the coefficients but
cannot tell a single prediction where in the year it sits.

WHY THAT MATTERS HERE SPECIFICALLY. The largest coefficient in every city's
fit is prev_max_c, and a carry-over term is a persistence forecast with the
edges filed off. It inherits persistence's one systematic error: it does not
know the season is moving. Through September a day is on average cooler than
the day before it; through March, warmer. Neither yesterday's number nor this
morning's temperature can express that drift, because the drift is a property
of the DATE.

WHY NOT A DAY NUMBER. 31 December and 1 January are one day apart and 364
units apart, so a coefficient on the raw day-of-year fits a ramp that falls
off a cliff at New Year. A sine and a cosine of the day-of-year angle put the
two days next to each other, where they belong, and let the fit choose the
phase - which matters, because a maritime west coast peaks weeks after the
solstice and an inland city peaks close to it.

WHY THEY ARE NOT COLUMNS. Pressure and cloud are real measurements the desk
has to accumulate before a fit can use them; measured 2026-09-21, 0 of 22,294
city-days carried a morning pressure and 1,052 carried a cloud mean. These
terms need nothing - they come from the row's own date, so they exist for the
whole 14-month history and on the forecast side by construction. The cost is
that they break the invariant every other feature obeys ("a fitted feature is
a fetched feature"), so the exemption is narrow, named, and tested from both
directions.
"""

import datetime as dt
import math
import sys

import pytest

import weather_model as wm


# ---------------------------------------------------------------------------
# the terms themselves
# ---------------------------------------------------------------------------
def test_new_year_is_not_a_cliff():
    """The whole reason for a sine and a cosine rather than a day number."""
    dec31 = wm.seasonal_terms("2025-12-31")
    jan01 = wm.seasonal_terms("2026-01-01")
    gap = math.hypot(dec31["doy_sin"] - jan01["doy_sin"],
                     dec31["doy_cos"] - jan01["doy_cos"])
    # One day's worth of arc on a unit circle is about 0.017.
    assert gap < 0.05, f"31 Dec and 1 Jan are {gap:.3f} apart on the circle"


def test_a_day_number_would_have_been_a_cliff():
    """The thing NOT done, kept as a number so the choice is not folklore."""
    raw_gap = abs(dt.date(2025, 12, 31).timetuple().tm_yday
                  - dt.date(2026, 1, 1).timetuple().tm_yday)
    assert raw_gap == 364


def test_the_terms_lie_on_the_unit_circle():
    for day in ("2026-01-01", "2026-03-21", "2026-06-21", "2026-09-21", "2026-12-21"):
        t = wm.seasonal_terms(day)
        assert abs(t["doy_sin"] ** 2 + t["doy_cos"] ** 2 - 1.0) < 1e-5


def test_the_same_date_lands_in_the_same_place_in_a_leap_year():
    """2024 is a leap year and 2026 is not. 1 March must sit at the same angle
    in both, or the fit reads a one-day seasonal jolt every four years."""
    a = wm.seasonal_terms("2024-03-01")
    b = wm.seasonal_terms("2026-03-01")
    assert abs(a["doy_sin"] - b["doy_sin"]) < 0.02
    assert abs(a["doy_cos"] - b["doy_cos"]) < 0.02


def test_opposite_seasons_are_opposite_points():
    """Six months apart is half a turn - what makes the pair a cycle rather
    than two unrelated columns."""
    jun = wm.seasonal_terms("2026-06-21")
    dec = wm.seasonal_terms("2026-12-21")
    assert abs(jun["doy_sin"] + dec["doy_sin"]) < 0.05
    assert abs(jun["doy_cos"] + dec["doy_cos"]) < 0.05


def test_an_unreadable_date_loses_the_terms_not_the_run():
    """One malformed row must not take a whole city's fit down with it."""
    for bad in (None, "", "not-a-date", 17):
        assert wm.seasonal_terms(bad) == {}
    rows = [{"obs_date": "2026-09-21"}, {"obs_date": None}]
    wm.with_seasonal(rows, "obs_date")
    assert "doy_sin" in rows[0] and "doy_sin" not in rows[1]


def test_a_timestamp_is_read_as_its_date():
    """for_date arrives as a date and predicted_at as a timestamp; the same
    helper has to cope with either rather than silently returning {}."""
    assert wm.seasonal_terms("2026-09-21T14:30:00+00:00") == \
        wm.seasonal_terms("2026-09-21")


# ---------------------------------------------------------------------------
# both sides, which is the invariant they are exempt from
# ---------------------------------------------------------------------------
def test_they_are_offered_to_the_fit():
    for f in wm.DATE_DERIVED_FEATURES:
        assert f in wm.CANDIDATE_FEATURES
    assert not (set(wm.DATE_DERIVED_FEATURES) & set(wm.BASE_FEATURES)), (
        "a base feature is MANDATORY - usable() would reject every day of a "
        "city whose dates failed to parse")


def test_they_are_never_asked_of_the_database():
    """There is no doy_sin column anywhere. Asking PostgREST for one 400s the
    entire read, which takes every city down rather than one."""
    for f in wm.DATE_DERIVED_FEATURES:
        assert f not in wm.FIT_COLUMNS
        assert f not in wm.FORECAST_COLUMNS


def test_the_fit_read_attaches_them(monkeypatch, capsys):
    """The observed side. Checked by running main(), not by reading the source,
    because the failure mode is a missing call rather than a wrong list."""
    seen = {}
    cache = [{"city_key": "nyc", "obs_date": f"2026-0{1 + i // 28}-{1 + i % 28:02d}",
              "max_c": 20.0, "n_obs": 24} for i in range(56)]

    def fake_fit_city(rows, features=None, target="max_c"):
        seen["rows"] = rows
        return None, len(rows)

    monkeypatch.setattr(wm, "rest_all",
                        lambda path, params=None, **kw:
                        cache if path == "derived_city_day_features" else [])
    monkeypatch.setattr(wm, "active_city_keys", lambda: {"nyc"})
    monkeypatch.setattr(wm, "recent_days", lambda *a, **k: {})
    monkeypatch.setattr(wm, "log_run", lambda *a, **k: None)
    monkeypatch.setattr(wm, "write_rows", lambda *a, **k: 0)
    monkeypatch.setattr(wm, "fit_city", fake_fit_city)
    monkeypatch.setattr(sys, "argv", ["weather_model", "--dry-run", "--no-forecast"])
    wm.main()
    assert all("doy_sin" in r and "doy_cos" in r for r in seen["rows"])


def test_the_forecast_read_attaches_them(monkeypatch):
    """The forward side. Without this a fit that kept doy_sin reads None on
    every forecast row and forecast_city drops the whole city, silently."""
    d0 = dt.date.today()
    fc = [{"city_key": "nyc", "for_date": (d0 + dt.timedelta(days=i)).isoformat(),
           "run_at": "2026-09-21T00:00:00+00:00", "lead_days": i,
           "forecast_max_c": 25.0, "morning_temp_c": 14.0,
           "dewpoint_depression_c": 9.0, "wind_mean": 5.0, "precip_total": 0.0}
          for i in range(3)]
    monkeypatch.setattr(wm, "rest_all", lambda path, params=None, **kw: fc)

    coef = {"intercept": 1.0, "prev_max_c": 0.5, "morning_temp_c": 0.4,
            "dewpoint_depression_c": 0.2, "wind_mean": -0.1, "precip_total": -0.5,
            "doy_sin": 0.3, "doy_cos": -0.2}
    fit = {"coefficients": coef, "features": [k for k in coef if k != "intercept"],
           "mae_c": 1.2, "persistence_mae_c": 2.0, "beats_persistence": True}
    anchor = [{"obs_date": (d0 - dt.timedelta(days=1)).isoformat(),
               "max_c": 26.0, "n_obs": 24}]

    preds, note = wm.predict_forward({"nyc": fit}, {"nyc": anchor})
    assert len(preds) == 3 and note is None, "a seasonal fit must still predict"
    assert "doy_sin" in preds[0]["inputs"] and "doy_cos" in preds[0]["inputs"]
    assert "doy_sin" in preds[0]["contributions"]


# ---------------------------------------------------------------------------
# and it has to be able to earn its place, and to fail to
# ---------------------------------------------------------------------------
def seasonal_city(amplitude, noise=0.0, days=400, start=dt.date(2025, 1, 1)):
    """A city whose maximum is a pure annual cycle plus optional noise.

    prev_max_c is the PREVIOUS day's value, as the real cache computes it, so
    the carry-over term behaves the way it does on real data.
    """
    rows, prev = [], None
    for i in range(days + 1):
        day = start + dt.timedelta(days=i)
        ang = 2 * math.pi * (day.timetuple().tm_yday - 1) / 365.0
        wobble = ((i * 7919) % 101 - 50) / 50.0 * noise
        value = 20.0 + amplitude * math.cos(ang - 0.4) + wobble
        if prev is not None:
            rows.append({"city_key": "t", "obs_date": day.isoformat(),
                         "max_c": round(value, 2), "n_obs": 24,
                         "prev_max_c": round(prev, 2), "morning_temp_c": 10.0,
                         "dewpoint_depression_c": 5.0, "wind_mean": 4.0,
                         "precip_total": 0.0})
        prev = value
    return wm.with_seasonal(rows, "obs_date")


def test_a_real_seasonal_signal_can_be_kept():
    """Not a claim that it WILL be kept on this desk's cities - that is for
    the held-out test to decide, city by city. This is the weaker and
    necessary claim: the machinery can see it when it is unmistakably there."""
    fit, _ = wm.fit_city(seasonal_city(amplitude=12.0), wm.BASE_FEATURES)
    assert fit is not None
    assert set(wm.DATE_DERIVED_FEATURES) & set(fit["features"]), (
        "a 12 C annual swing is not visible to the selection")


def test_a_city_with_no_season_does_not_keep_them():
    """The complement, which is what stops this being a free pass. A flat
    city must reject the terms on held-out days like any other candidate."""
    fit, _ = wm.fit_city(seasonal_city(amplitude=0.0, noise=3.0), wm.BASE_FEATURES)
    assert fit is not None
    assert not (set(wm.DATE_DERIVED_FEATURES) & set(fit["features"])), (
        "a seasonless city kept a seasonal term - the held-out floor is not biting")


@pytest.mark.parametrize("term", wm.DATE_DERIVED_FEATURES)
def test_a_kept_seasonal_term_has_a_meaning(term):
    """describe() and the reasoning panel read MEANING; a coefficient with no
    sentence beside it is six numbers again."""
    assert term in wm.MEANING and wm.MEANING[term]
