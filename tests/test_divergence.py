"""Forecast divergence: how a second forecast model changes sigma.

v_forecast_divergence (sql/ad4_16_nws.sql) measures how far apart the latest
run of each model is about the same date; probability_engine multiplies sigma
by that. The property that matters is the FLOOR: a second opinion is allowed
to add doubt, never to remove it. A bug that let the multiplier drop below 1.0
would make AD4 more confident than its own measured skill says it should be -
and would do it silently, since the output is still a plausible number.
"""

import pytest

import probability_engine as pe


@pytest.fixture(autouse=True)
def clear_cache():
    pe._divergence_cache = None
    yield
    pe._divergence_cache = None


def load(monkeypatch, rows, count=None, seen=None):
    def fake_rest(table, params=None, **kw):
        assert table == "v_forecast_divergence"
        if count is not None:
            count.append(1)
        if seen is not None:
            seen.append(dict(params or []))
        return rows
    monkeypatch.setattr(pe, "rest", fake_rest)


def row(**kw):
    base = {"city_key": "nyc", "for_date": "2026-09-04", "n_models": 2,
            "models": "nws, open-meteo", "spread_c": 1.6, "sigma_multiplier": 1.8}
    base.update(kw)
    return base


def test_spread_widens_sigma(monkeypatch):
    load(monkeypatch, [row()])
    mult, r = pe._divergence_for("nyc", "2026-09-04")
    assert mult == pytest.approx(1.8)
    assert r["models"] == "nws, open-meteo"


def test_a_city_with_no_row_is_unchanged(monkeypatch):
    """One model, or a database without ad4_16 data, must behave as before."""
    load(monkeypatch, [row()])
    assert pe._divergence_for("miami", "2026-09-04") == (1.0, None)


def test_a_different_date_is_not_borrowed(monkeypatch):
    load(monkeypatch, [row()])
    assert pe._divergence_for("nyc", "2026-09-05") == (1.0, None)


@pytest.mark.parametrize("bad", [0.5, 0.0, -3.0])
def test_multiplier_can_never_narrow_sigma(monkeypatch, bad):
    """The view clamps this, but the clamp is not this module's to trust:
    agreement between models is not evidence that today is easy."""
    load(monkeypatch, [row(sigma_multiplier=bad)])
    mult, _ = pe._divergence_for("nyc", "2026-09-04")
    assert mult == 1.0


@pytest.mark.parametrize("bad", [None, "", "n/a", "1.8x"])
def test_unreadable_multiplier_falls_back_to_one(monkeypatch, bad):
    """A null or unparsable multiplier is not a licence to guess one.

    The caller only applies the row when the multiplier exceeds 1.0, so
    returning 1.0 here leaves sigma exactly as historical skill set it.
    """
    load(monkeypatch, [row(sigma_multiplier=bad)])
    mult, _ = pe._divergence_for("nyc", "2026-09-04")
    assert mult == 1.0


def test_missing_view_does_not_stop_pricing(monkeypatch, capsys):
    """A database that has not run ad4_16 has no such view. Price anyway."""
    def boom(table, params=None, **kw):
        raise RuntimeError("relation \"v_forecast_divergence\" does not exist")
    monkeypatch.setattr(pe, "rest", boom)
    assert pe._divergence_for("nyc", "2026-09-04") == (1.0, None)
    assert "no forecast divergence" in capsys.readouterr().err


def test_the_view_is_read_once_per_run_not_once_per_city(monkeypatch):
    calls = []
    load(monkeypatch, [row()], count=calls)
    for _ in range(5):
        pe._divergence_for("nyc", "2026-09-04")
        pe._divergence_for("miami", "2026-09-04")
    assert len(calls) == 1


def test_date_objects_and_strings_match_the_same_row(monkeypatch):
    """for_date arrives as a string from PostgREST and as a date from callers."""
    import datetime as dt
    load(monkeypatch, [row()])
    by_str = pe._divergence_for("nyc", "2026-09-04")
    by_date = pe._divergence_for("nyc", dt.date(2026, 9, 4))
    assert by_str[0] == by_date[0] == pytest.approx(1.8)


# --------------------------------------------------------------------------
# The view holds one row per (city, date) that ever had a forecast - 47,099 on
# a real database. rest() is a single unpaginated GET, so an unbounded query
# risks a truncated page, and a missing row is indistinguishable from "the
# models agree". Both halves of that are tested here.
# --------------------------------------------------------------------------
def test_only_dates_this_engine_prices_are_fetched(monkeypatch):
    import datetime as dt
    seen = []
    load(monkeypatch, [row()], seen=seen)
    pe._divergence()
    params = seen[0]
    assert params["for_date"] == f"gte.{dt.date.today().isoformat()}"
    assert int(params["limit"]) == pe._DIVERGENCE_LIMIT


def test_a_truncated_page_is_reported_not_swallowed(monkeypatch, capsys):
    """Silently pricing half the cities at 1.0 would look like model agreement."""
    monkeypatch.setattr(pe, "_DIVERGENCE_LIMIT", 3)
    load(monkeypatch, [row(city_key=f"c{i}") for i in range(3)])
    pe._divergence()
    assert "hit the 3-row limit" in capsys.readouterr().err


def test_a_full_page_under_the_limit_is_silent(monkeypatch, capsys):
    monkeypatch.setattr(pe, "_DIVERGENCE_LIMIT", 10)
    load(monkeypatch, [row(city_key=f"c{i}") for i in range(3)])
    pe._divergence()
    assert "limit" not in capsys.readouterr().err
