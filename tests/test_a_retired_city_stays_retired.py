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

AND THE TWO OBSERVABLE ONES ARE NOT PENDING ANYTHING. The retirement migration
says hong_kong and jinan are retired "until a second source is wired up", which
left them on the books as work waiting to happen - an HKO feed for one, a
different station or Open-Meteo on the stored lat/long for the other. Asked on
2026-09-20 whether to keep holding them open, Hassan: "as for on kon andjinan,
remove entirely we dont ave to wait for tem." So they are out on the same
footing as dc, lagos and jakarta: the roster is 49 cities, and nothing in the
plan is waiting on a second weather source.

That costs nothing and undoes nothing. Retirement is still one column, every
row they ever wrote is still there, and if the decision ever reverses it is
still one update - it is simply not queued work any more.

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


# ---------------------------------------------------------------------------
# the views - found leaking the day after the retirement
# ---------------------------------------------------------------------------
# The first pass closed every path that READS THE CITIES TABLE and every Python
# path that reads the feature cache. It did not close the views that build
# their city list from markets and bands instead, so the morning after, the
# desk was still showing 44 hong_kong and 22 jinan rows in v_trade_plan and 902
# rows in v_prediction_ladder. No strategy would fire on any of them, so no
# money was at risk - but "retired" meant two different things in two halves of
# the same platform, which is the condition these tests exist to prevent.
#
# Three views carry the filter and six inherit it: v_band_ladder, v_trade_plan
# and v_city_day_plan are all built on v_opportunities.
FILTERED_VIEWS = {
    "v_opportunities": "sql/ad4_13_reconcile.sql",
    "v_prediction_ladder": "sql/ad4_68_prediction_ladder_outcomes.sql",
    "v_opportunity_context": "sql/ad4_22_opportunity_context.sql",
}


@pytest.mark.parametrize("view,path", sorted(FILTERED_VIEWS.items()))
def test_the_desk_views_filter_on_status(view, path):
    """Each of these must join cities and test status, in the same file that
    defines it - not in a later file that would silently be skipped by anyone
    installing only part of the schema."""
    src = open(path).read()
    assert f"view {view}" in src or f"view public.{view}" in src, (
        f"{path} no longer defines {view} — the filter has moved and this test "
        f"is now pointing at nothing")
    assert "coalesce(c.status, 'active') = 'active'" in src \
        or "coalesce(ct.status, 'active') = 'active'" in src, (
        f"{path} does not filter retired cities out of {view}")


def test_the_inheriting_views_are_not_given_their_own_copy_of_the_rule():
    """v_band_ladder, v_trade_plan and v_city_day_plan are built on
    v_opportunities and inherit its filter. A second copy of the rule in
    ad4_34 would be a second place for it to go stale - which is exactly how
    the leak happened in the first place."""
    src = open("sql/ad4_34_trade_plan.sql").read()
    assert "from v_opportunities" in src, (
        "v_band_ladder no longer reads v_opportunities, so it no longer "
        "inherits the filter and needs its own")


def test_history_is_deliberately_not_filtered():
    """Retirement removes a city from the DESK, not from the record.

    The rule this repo runs on is never to lose data, so the settled outcomes,
    the archive rollups and the per-city stats keep every retired city and stay
    readable. Filtering those would make a retired city look like one that
    never existed, and the two need completely different actions. This test
    exists so that a later "filter it everywhere" sweep has to argue with
    something.
    """
    kept = ["v_archive_by_city", "v_city_stats", "v_verified_fact_band_outcome"]
    for view in kept:
        assert view not in FILTERED_VIEWS, (
            f"{view} is the record, not a trading surface — a retired city has "
            f"to stay visible in it")

    # ONE VIEW IS ACTIVE-ONLY BY ITS OWN DESIGN, and it is worth saying so
    # rather than letting a reader assume it covers everything.
    # v_city_observation_health has filtered on status since it was written,
    # months before any city was retired, because it answers "is the desk's
    # data healthy", which is a question about the cities the desk is running.
    # The consequence is real and accepted: you cannot use it to ask why jinan
    # went quiet. weather_observations and derived_city_day_features still
    # hold every reading those cities ever produced, which is where that
    # question gets answered.
    health = open("sql/ad4_71_observation_health.sql").read()
    assert "coalesce(c.status, 'active') = 'active'" in health, (
        "the observation health view is documented as active-only; if that "
        "changed, this comment and the retirement notes need to change too")


def test_stored_fits_cannot_resurrect_a_retired_city(monkeypatch):
    """The leak that got through: the weekly fit filtered retired cities, but
    --predict-only never refits - it reads the fits already in the table, which
    are kept forever. Hours after dc was retired the 04:15 intraday run wrote
    eleven fresh forward predictions for it."""
    monkeypatch.setattr(wm, "rest_all", lambda *a, **k: [
        {"city_key": c, "target": "max_c",
         "coefficients": {"intercept": 1.0, "prev_max_c": 0.5},
         "mae_c": 1.0, "persistence_mae_c": 2.0, "beats_persistence": True}
        for c in ("nyc", "dc")])
    monkeypatch.setattr(wm, "active_city_keys", lambda: {"nyc"})
    fits = wm.stored_fits()
    assert set(fits) == {"nyc"}, "a retired city's stored fit is not a licence to predict"


# ---------------------------------------------------------------------------
# every surface that decides WHICH CITIES, in one list
# ---------------------------------------------------------------------------
# Three passes were needed to finish this retirement, and each time the miss
# was a surface nobody had enumerated:
#
#   pass 1  the cities table readers and the feature-cache readers
#   pass 2  the views that build their city list from markets and bands, and
#           stored_fits(), which let --predict-only resurrect a retired city
#   pass 3  the four n8n collectors, which went on discovering markets and
#           fetching forecasts for cities the desk had stopped trading, and
#           the Live page, which renders from live_weather rather than cities
#
# So the list itself is the fix. Anything that turns rows into "the cities we
# are working on" belongs here, and a new one cannot be added without being
# listed, because the enumeration is what is asserted.


def test_a_page_rendering_from_live_weather_filters_to_the_roster():
    """live_weather has a row per city forever, so a page that renders FROM it
    shows retired cities even when its cities query is filtered. The Live page
    did exactly that: 54 rows, five of them with no display name."""
    src = open("web/app/live/page.tsx").read()
    assert "cityByKey.has(l.city_key)" in src, (
        "web/app/live/page.tsx renders from live_weather without filtering to "
        "the active roster")


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
