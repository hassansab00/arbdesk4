"""Retiring a city has to mean retired everywhere, and it must not delete.

Five cities were retired on 2026-09-19, for two different reasons measured on
the live database:

  THE VENUE DROPPED THEM. dc (last market 2025-01-20), lagos (2026-04-15) and
  jakarta (2026-04-16) - the only three of 54 with no market in 30 days, the
  same three at 7 days. Healthy feeds, fitted models, nothing to trade.

  WE CANNOT OBSERVE THEM. hong_kong has no icao and no station_name at all, so
  the METAR ingest has never requested it - zero observations, ever. jinan has
  icao ZSJN and 60 readings total, all inside a four-minute window on
  2026-08-26, while beijing, shanghai and qingdao each hold 2,152 over the same
  window. Both are actively traded; neither can carry a model.

NOTHING IS DELETED. That is not a detail, it is the rule this repo runs on:
archive so the platform can still read it, never delete. Retirement moves one
column, `cities.status`, and every row those cities ever wrote stays. So the
only correct question anywhere is "is this city active", never "do we have
data for it" - and these tests exist because five separate code paths were
answering the second question, or no question at all.

common.get_cities() already gated on status='active', which covers every
ingest, pricing and settlement script. It did not cover:

  weather_model      reads derived_city_day_features directly, no city filter,
                     so it would fit a retired city from its cached days
  model_promotion    same table, so it would go on scoring that fit
  refresh_feature_cache, capacity x2   iterate the cities table raw
  three web pages    listed every city regardless of status

The filter is one shared helper rather than five spellings of it, because five
spellings is how this happened.
"""

import subprocess
import sys

import pytest

import common
import capacity
import model_promotion as mp
import weather_model as wm


RETIRED = ["dc", "hong_kong", "jakarta", "jinan", "lagos"]
MIGRATION = "supabase/migrations/20260919230000_retire_five_cities.sql"


def rows_for(*cities, n=3):
    out = []
    for c in cities:
        for i in range(n):
            out.append({"city_key": c, "obs_date": f"2026-09-{i + 1:02d}",
                        "max_c": 20.0, "n_obs": 24})
    return out


# ---------------------------------------------------------------------------
# the helper
# ---------------------------------------------------------------------------
def test_active_city_keys_asks_the_database_for_active_only(monkeypatch):
    seen = {}

    def fake_rest(table, params=None):
        seen["table"], seen["params"] = table, params
        return [{"city_key": "nyc"}, {"city_key": "london"}]

    monkeypatch.setattr(common, "rest", fake_rest)
    assert common.active_city_keys() == {"nyc", "london"}
    assert seen["table"] == "cities"
    assert seen["params"]["status"] == "eq.active", (
        "the filter must be in the QUERY - filtering after the read would page "
        "through retired cities and then throw them away")


def test_drop_retired_names_what_it_dropped():
    """A count is not enough. A city list that quietly got shorter is the shape
    of the bug that left six cities unpredicted on 2026-09-19."""
    rows = rows_for("nyc", "dc", "london")
    kept, dropped = common.drop_retired(rows, {"nyc", "london"})
    assert {r["city_key"] for r in kept} == {"nyc", "london"}
    assert dropped == ["dc"]


def test_drop_retired_keeps_everything_when_nothing_is_retired():
    rows = rows_for("nyc", "london")
    kept, dropped = common.drop_retired(rows, {"nyc", "london"})
    assert len(kept) == len(rows) and dropped == []


# ---------------------------------------------------------------------------
# the five paths that did not ask
# ---------------------------------------------------------------------------
def test_the_fit_does_not_fit_a_retired_city(monkeypatch, capsys):
    """Its cached days are kept forever, so only the filter stops it."""
    cache = rows_for("nyc", "dc")
    monkeypatch.setattr(wm, "rest_all",
                        lambda path, params=None, **kw:
                        cache if path == "derived_city_day_features" else [])
    monkeypatch.setattr(wm, "active_city_keys", lambda: {"nyc"})
    monkeypatch.setattr(wm, "log_run", lambda *a, **k: None)
    monkeypatch.setattr(wm, "write_rows", lambda *a, **k: 0)
    monkeypatch.setattr(sys, "argv", ["weather_model", "--dry-run"])
    wm.main()
    out = capsys.readouterr().out
    assert "dc" in out and "retired" in out, (
        "the retired city has to be NAMED, not silently absent")


def test_promotion_does_not_score_a_retired_city(monkeypatch, capsys):
    def fake_rest_all(path, params=None, **kw):
        if path == "derived_model_forecast":
            return [{"city_key": c, "for_date": "2026-09-01", "run_at": "2026-08-31",
                     "lead_days": 1, "predicted_max_c": 20.0, "nws_max_c": 20.0,
                     "prev_source": "observed", "model_version": "v",
                     "predicted_at": "2026-08-31"} for c in ("nyc", "jinan")]
        if path == "derived_city_day_features":
            return rows_for("nyc", "jinan")
        if path == "derived_weather_model":
            return [{"city_key": c, "target": "max_c", "fitted_at": "2026-08-01",
                     "model_version": "v"} for c in ("nyc", "jinan")]
        return []

    monkeypatch.setattr(mp, "rest_all", fake_rest_all)
    monkeypatch.setattr(mp, "active_city_keys", lambda: {"nyc"})
    preds, obs, fits = mp.load()
    for name, rows in (("preds", preds), ("obs", obs), ("fits", fits)):
        assert {r["city_key"] for r in rows} == {"nyc"}, f"{name} kept a retired city"
    assert "jinan" in capsys.readouterr().out


def test_the_cache_refresh_skips_retired_cities(monkeypatch):
    asked = []
    monkeypatch.setattr(common, "active_city_keys", lambda: {"nyc", "london"})
    monkeypatch.setattr(common, "rpc",
                        lambda fn, params=None: asked.append((params or {}).get("p_city"))
                        or {"ok": True, "city_days_touched": 1, "city_hours": 1,
                            "city_days_total": 1, "ms": 1})
    common.refresh_feature_cache(quiet=True)
    assert sorted(asked) == ["london", "nyc"]


def test_capacity_walks_active_cities_only(monkeypatch):
    seen = []
    monkeypatch.setattr(capacity, "active_city_keys", lambda: {"nyc"})
    monkeypatch.setattr(capacity, "_call_rpc",
                        lambda fn, params=None, timeout=120:
                        seen.append((params or {}).get("p_city")) or 1)
    capacity.refresh_peaks([])
    assert seen == ["nyc"]


@pytest.mark.parametrize("page", [
    "web/app/predictive/page.tsx",
    "web/app/live/page.tsx",
    "web/app/campaigns/page.tsx",
    "web/app/page.tsx",
])
def test_no_desk_page_lists_a_retired_city(page):
    """Every page that reads the cities table filters on status.

    Four pages read it and only one filtered, so the desk would have gone on
    offering cities it had stopped trading."""
    src = open(page).read()
    if 'from("cities")' not in src:
        pytest.skip(f"{page} no longer reads cities directly")
    for i, line in enumerate(src.splitlines()):
        if 'from("cities")' in line:
            window = "\n".join(src.splitlines()[i:i + 4])
            assert '"status", "active"' in window, (
                f"{page}:{i + 1} reads cities without filtering on status")


# ---------------------------------------------------------------------------
# the migration
# ---------------------------------------------------------------------------
def test_the_migration_retires_exactly_the_five():
    sql = open(MIGRATION).read()
    for city in RETIRED:
        assert f"'{city}'" in sql, f"{city} is not in the migration"


def test_the_migration_deletes_nothing():
    """The rule this repo runs on. A retirement that deletes is not reversible,
    and 'we dont need them right now' is a reversible statement."""
    body = "\n".join(l for l in open(MIGRATION).read().splitlines()
                     if not l.strip().startswith("--"))
    for forbidden in ("delete from", "drop table", "truncate", "drop column"):
        assert forbidden not in body.lower(), f"the migration contains {forbidden!r}"
    assert "update public.cities" in body.lower()
    assert "status = 'retired'" in body


def test_the_migration_can_run_twice():
    """Every migration runs on every contract build - see CLAUDE.md - so one
    that is not idempotent breaks the suite for everyone."""
    body = open(MIGRATION).read().lower()
    assert "is distinct from 'retired'" in body, (
        "the update must be a no-op on a database that already has it")


def test_the_migration_is_in_git():
    out = subprocess.run(["git", "ls-files", "--error-unmatch", MIGRATION],
                         capture_output=True, text=True)
    assert out.returncode == 0 or "20260919230000" in subprocess.run(
        ["git", "status", "--short"], capture_output=True, text=True).stdout, (
        "the migration exists on disk but git has never seen it")
