"""Which cities are we working on? Asked of the WHOLE repo, not one directory.

This question has been answered wrongly four times, each time in a place the
previous check did not look:

  pass 1  the cities-table readers and the feature-cache readers
  pass 2  the SQL views built from markets and bands, and stored_fits(), which
          let --predict-only resurrect a retired city from a fit kept forever
  pass 3  four n8n collectors and the Live page, which renders from
          live_weather rather than from cities
  pass 4  this one - the enumeration itself only globbed web/app/**/page.tsx,
          so four components and four Analytics panels were never looked at

The lesson is not "check harder". It is that a check which SAMPLES cannot
prove a negative. This file enumerates every area of the repo that can decide
which cities the desk works on, and fails when a file joins the cities table
without being classified. Adding a surface now means classifying it.

THREE CLASSES, because "filter everywhere" is wrong:

  FILTERS   it offers cities to act on, or collects for them. Must exclude
            retired cities.
  RECORD    it is history, archive or diagnosis. Must NOT exclude them - a
            retired city that vanishes from the record looks like one that
            never existed, and those need different actions. The repo's rule
            is that nothing is ever deleted; hiding history is deleting it
            from the only place anyone would look.
  LOOKUP    it reads cities to build a map keyed by city_key, consumed for
            rows that came from somewhere else. Filtering would not remove a
            city from anything - it would make that city's value missing and
            silently default.

Measured on the live database 2026-09-20: 22 relations the desk reads still
contain retired cities, and that is correct for all but the four the Analytics
page rendered as lists. The fix is at the consumer, not in the views.
"""

import glob
import re

import pytest

# ---------------------------------------------------------------------------
# WEB - every .tsx and .ts, not just pages
# ---------------------------------------------------------------------------
WEB_LOOKUP_ONLY = {
    # city_key -> unit, read for rows sourced elsewhere.
    "web/components/RightRail.tsx",
}


def web_files():
    for path in sorted(glob.glob("web/**/*.tsx", recursive=True)
                       + glob.glob("web/**/*.ts", recursive=True)):
        if "/.next/" in path or "/node_modules/" in path:
            continue
        yield path


def test_every_web_read_of_the_cities_table_is_filtered_or_named():
    offenders = []
    for path in web_files():
        if path in WEB_LOOKUP_ONLY:
            continue
        src = open(path).read()
        lines = src.splitlines()
        filtered_in_file = ('(c.status ?? "active") === "active"' in src
                            or "status === 'active'" in src)
        for i, line in enumerate(lines):
            if 'from("cities")' not in line and "from('cities')" not in line:
                continue
            window = "\n".join(lines[i:i + 5])
            if ('"status", "active"' in window or "'status','active'" in window
                    or "'status', 'active'" in window or filtered_in_file):
                continue
            offenders.append(f"{path}:{i + 1}")
    assert offenders == [], (
        "these read the cities table without filtering on status, and are not "
        "named as lookup-only: " + ", ".join(offenders))


def test_a_panel_that_renders_one_row_per_city_is_scoped_to_the_roster():
    """The Analytics page built its city picker from whatever came back, so
    four skill panels listed retired cities. The views stay unfiltered - they
    are the record - and the PAGE asks for the roster."""
    src = open("web/components/ModelAnalytics.tsx").read()
    for view in ("v_model_forecast_skill", "v_condition_skill",
                 "v_persistence_skill", "v_city_climb_profile"):
        m = re.search(re.escape(view) + r'"\)([^;]*)', src)
        assert m, f"{view} is no longer read here - reclassify it"
        assert '.in("city_key", keys)' in m.group(1), (
            f"{view} is rendered as a per-city list without being scoped to "
            f"the active roster")


# ---------------------------------------------------------------------------
# PYTHON - one definition of active, and everything goes through it
# ---------------------------------------------------------------------------
PY_ALLOWED = {
    "scripts/common.py",                    # the definitions themselves
    "scripts/verify_resolution_source.py",  # a manual diagnostic: it SHOULD
                                            # be able to name a retired city
}


def test_no_script_reads_the_cities_table_around_common():
    offenders = []
    for path in sorted(glob.glob("scripts/*.py")):
        if path in PY_ALLOWED:
            continue
        for i, line in enumerate(open(path).read().splitlines()):
            if 'rest("cities"' in line or "rest('cities'" in line:
                offenders.append(f"{path}:{i + 1}")
    assert offenders == [], (
        "these bypass common.get_cities() / common.active_city_keys(): "
        + ", ".join(offenders))


def test_there_is_exactly_one_definition_of_active():
    src = open("scripts/common.py").read()
    assert src.count('"status": "eq.active"') == 2, (
        "get_cities() and active_city_keys() are the two places that define "
        "active; a third or a missing one means the definition has moved")


# ---------------------------------------------------------------------------
# N8N - the collection layer
# ---------------------------------------------------------------------------
def test_every_n8n_collector_asks_for_active_cities_only():
    offenders = []
    for path in sorted(glob.glob("n8n/*.template.json")):
        src = open(path).read()
        if "rest/v1/cities?" not in src:
            continue
        if "status=eq.active" in src or "!== 'active'" in src:
            continue
        offenders.append(path)
    assert offenders == [], (
        "these build a city list with no status filter, so they keep "
        "collecting for retired cities: " + ", ".join(offenders))


# ---------------------------------------------------------------------------
# SQL - the classification, and the proof that it is complete
# ---------------------------------------------------------------------------
# Every .sql file that SELECTs FROM or JOINs the cities table. Classified, with
# the reason, so that adding one forces a decision rather than a default.
SQL_FILTERS = {
    "sql/ad4_13_reconcile.sql":                 "v_opportunities - and through it v_band_ladder, v_trade_plan, v_city_day_plan",
    "sql/ad4_22_opportunity_context.sql":       "v_opportunity_context - the movement panel behind each card",
    "sql/ad4_68_prediction_ladder_outcomes.sql":"v_prediction_ladder - the Predictive page",
    "sql/ad4_87_market_settlement_gaps.sql":    "v_market_settlement_gaps - ended days to act on; a "
                                                "retired city is not collected on purpose",
    "sql/ad4_30_open_meteo.sql":                "the forecast collector's city list",
    "sql/ad4_32_run_scope.sql":                 "v_run_scope - which cities a job runs over",
    "sql/ad4_33_control.sql":                   "the control surface's city list",
    "sql/ad4_71_observation_health.sql":        "is the DESK's data healthy - operational, not the record",
    "sql/ad4_75_probability_reliability.sql":   "the measured haircut, applied to live prices",
    "sql/ad4_live_weather_timing.sql":          "refresh_live_weather_timing - only worth computing for a live city",
    # v_city_trajectory_now prices the rest of TODAY for cities the desk is
    # trading, and filters to active on its own. The evidence view beside it
    # joins cities only for the timezone that turns a timestamp into a local
    # hour, so it is not asking a question about the roster at all. FILTERS is
    # the right call for the file, because the relation an engine reads is the
    # one that decides.
    "sql/ad4_86_trajectory.sql":                "v_city_trajectory_now - what the engine prices the rest of today from",
    # v_trade_plan joins cities for observation_trust, the gate that decides
    # whether s5 and s7 may fire in a city at all. FILTERS, and it already is:
    # every row comes from v_opportunities, which admits only the active
    # roster, and the join is a LEFT join off that - it can attach a trust
    # score to a row, never add a retired city's row.
    "sql/ad4_34_trade_plan.sql":                "v_trade_plan - the board, and the s5/s7 trust gate",
}
SQL_RECORD = {
    "sql/ad4_16_nws.sql", "sql/ad4_17_city_stats.sql", "sql/ad4_19_stats_cache.sql",
    "sql/ad4_21_weather_features.sql", "sql/ad4_23_reasoning.sql",
    "sql/ad4_26_temp_trend.sql", "sql/ad4_28_feature_cache.sql",
    "sql/ad4_29_retention.sql", "sql/ad4_35_databank_inventory.sql",
    "sql/ad4_37_peak_hour.sql", "sql/ad4_40_synthesis.sql",
    "sql/ad4_41_campaigns.sql", "sql/ad4_43_forecast_audit.sql",
    "sql/ad4_45_calibration_feedback.sql", "sql/ad4_55_city_coordinates.sql",
    "sql/ad4_56_correlation_speed_and_peak_key.sql",
    "sql/ad4_58_city_prediction_confidence.sql", "sql/ad4_72_model_promotion.sql",
    "sql/ad4_diagnose.sql", "sql/ad4_phase2.sql", "sql/ad4_phase2_ranking.sql",
    # v_station_day_max and v_settlement_agreement. RECORD, and the call is
    # not obvious, so: observation_trust is plainly something to act on - it
    # is the weight a running-max strategy should carry in that city - which
    # argues FILTERS. It is still the record. The question it answers is "how
    # often did our thermometer name the band the venue settled on", asked of
    # days that are already settled, and a city retired last week has a
    # history worth reading precisely BECAUSE somebody may want it back. The
    # roster filter belongs at the consumer, the way v_trade_plan applies it,
    # not in a table of what happened.
    "sql/ad4_82_settlement_agreement.sql",
    # v_city_hit_history and v_city_hit_summary. RECORD, and for the same
    # reason: "on the days that settled, did the band we called turn out to be
    # the band that paid" is a question about the past, asked of frozen
    # evidence. It joins cities only to print a display_name and a unit. A
    # retired city's hit record is the most useful thing there is to read
    # BEFORE deciding whether to bring it back, so filtering it out here would
    # destroy the one answer somebody would come looking for.
    "sql/ad4_85_city_hit_history.sql",
}

# MIGRATIONS COUNT TOO. The first version of this file globbed sql/*.sql and
# nothing else, so seven migrations that build from the cities table were never
# looked at - and four of them define relations that exist ONLY there
# (v_city_day_readiness, v_operational_health, v_city_metadata_health,
# v_city_metadata_verification), with no sql/*.sql file superseding them.
#
# Measured on the live database 2026-09-20 there was no leak: readiness, the
# sigma inputs and the model-skill view hold zero retired cities, and the one
# that does - v_city_metadata_verification, 5 of 54 - is a diagnostic asking
# whether each city's resolution source matches what the desk believes, which
# is a question you ask ABOUT a retired city. So this closes a blind spot
# rather than a bug. It is here because a blind spot is how the last four got
# in.
MIGRATION_RECORD = {
    "supabase/migrations/20260923170000_trust_shrunk_toward_the_pool.sql":
        "refresh_observation_trust() - the RECORD of how often each city's thermometer "
        "names the venue's band, like the migration it replaces; the gate that reads it "
        "is ad4_34, filtered",
    "supabase/migrations/20260923160000_when_was_the_forecast_issued.sql":
        "v_forecast_issued - cities.timezone only, to put each forecast's issue time on "
        "the city's calendar; a retired city's forecasts were issued when they were",
    "supabase/migrations/20260923140000_a_market_whose_day_ended_is_closed.sql":
        "market_day_ended() - closing a market whose local day has ended is the "
        "record being made true, and a retired city's ended market is ended too",
    "supabase/migrations/20260922230000_a_rule_is_only_as_good_as_the_thermometer_under_it.sql":
        "cities.observation_trust and refresh_observation_trust() - the RECORD "
        "of how often a city's thermometer named the band the venue settled on. "
        "Deliberately unfiltered: a retired city's measured trust is history "
        "worth keeping, and putting it back is the same one word as the roster. "
        "The GATE that reads it is in ad4_34, and that one is filtered.",
    "supabase/migrations/20260912213000_phase1_data_foundation.sql":
        "v_archive_daily and the data-quality flags - the record",
    "supabase/migrations/20260912234500_phase1c_operational_readiness.sql":
        "v_city_day_readiness / v_operational_health - derived from markets, so "
        "already carries no retired city; diagnosis either way",
    "supabase/migrations/20260923010000_readiness_reads_one_row_per_band.sql":
        "v_city_day_readiness / v_city_day_execution_readiness, re-issued with "
        "the latest book, probability and edge read per band instead of over "
        "their whole history. Same rows and the same classification as phase1c "
        "and phase1d: derived from markets, so no retired city; diagnosis",
    "supabase/migrations/20260913091000_phase1d_databank_city_rollup.sql":
        "v_archive_by_city - the archive rollup",
    "supabase/migrations/20260913092000_phase1d_city_metadata_evidence.sql":
        "v_city_metadata_health / _verification - does each city's resolution "
        "source match what we believe? asked ABOUT a retired city too",
    "supabase/migrations/20260913102000_phase2a_artifact_provenance.sql":
        "provenance and the sigma inputs - the record",
    "supabase/migrations/20260914100000_observation_prune_local_day_guard.sql":
        "prune_observations - pruning must walk every city or a retired one's "
        "rows would never be aged out",
    "supabase/migrations/20260914110000_archive_prune_count_contract.sql":
        "prune_forecasts / prune_observations - same",
}

JOINS_CITIES = re.compile(r"(?:join|from)\s+(?:public\.)?cities(?:\s|$|,|\))", re.I)


def sql_files_touching_cities():
    out = []
    for path in sorted(glob.glob("sql/*.sql")
                       + glob.glob("supabase/migrations/*.sql")):
        body = "\n".join(l for l in open(path).read().splitlines()
                         if not l.strip().startswith("--"))
        if JOINS_CITIES.search(body):
            out.append(path)
    return out


def test_every_sql_file_that_builds_from_cities_is_classified():
    """THE POINT OF THE WHOLE FILE. A new view over cities cannot be added
    without deciding whether it offers something to act on or records what
    happened - and that now includes a migration, which is where four
    relations live that no sql/*.sql file supersedes."""
    classified = set(SQL_FILTERS) | SQL_RECORD | set(MIGRATION_RECORD)
    unclassified = [p for p in sql_files_touching_cities() if p not in classified]
    assert unclassified == [], (
        "these SQL files build from the cities table and are in neither "
        "FILTERS nor RECORD - decide which, and say why: "
        + ", ".join(unclassified))


@pytest.mark.parametrize("path,why", sorted(SQL_FILTERS.items()))
def test_a_filtering_sql_file_actually_filters(path, why):
    src = open(path).read()
    assert re.search(r"status.{0,20}=.{0,4}'active'", src), (
        f"{path} is classified as filtering ({why}) but does not test status")


def test_the_classification_has_not_gone_stale():
    """A file listed but no longer touching cities means the list is drifting
    away from the code it describes."""
    touching = set(sql_files_touching_cities())
    stale = sorted((set(SQL_FILTERS) | SQL_RECORD | set(MIGRATION_RECORD)) - touching)
    assert stale == [], (
        "these are classified but no longer build from cities: " + ", ".join(stale))


def test_the_record_is_never_filtered():
    """Stated as a rule so a later 'filter it everywhere' sweep has to argue
    with something. History, archive and diagnosis keep every city."""
    for path in sorted(SQL_RECORD):
        assert path not in SQL_FILTERS, f"{path} cannot be both"
