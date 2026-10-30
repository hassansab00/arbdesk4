"""Classify city readers and enforce active-roster versus historical access."""

import glob
import re

import pytest

WEB_LOOKUP_ONLY = {
    "web/app/api/paper-desk/route.ts",  # names/timezones of saved Day Max selections, including historical entries
    
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

    src = open("web/components/ModelAnalytics.tsx").read()
    for view in ("v_model_forecast_skill", "v_condition_skill",
                 "v_persistence_skill", "v_city_climb_profile"):
        m = re.search(re.escape(view) + r'"\)([^;]*)', src)
        assert m, f"{view} is no longer read here - reclassify it"
        assert '.in("city_key", keys)' in m.group(1), (
            f"{view} is rendered as a per-city list without being scoped to "
            f"the active roster")

PY_ALLOWED = {
    "scripts/common.py",                    
    "scripts/verify_resolution_source.py",  
                                            
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

SQL_FILTERS = {
    "sql/ad4_page_status.sql": "Active-city operational input",
    "supabase/migrations/20261018172215_page_evidence_health_sample.sql": "Active-city operational input",
    "sql/ad4_13_reconcile.sql":                 "Active-city operational input",
    "sql/ad4_22_opportunity_context.sql":       "Active-city operational input",
    "sql/ad4_68_prediction_ladder_outcomes.sql":"Active-city operational input",

    "supabase/migrations/20260927170000_the_card_shows_what_was_priced.sql":
        "Active-city operational input",

    "sql/ad4_89_page_cache.sql":
        "Active-city operational input",
    "supabase/migrations/20261007150000_the_page_cache_keeps_the_days_it_shows.sql":
        "Active-city operational input",
    "supabase/migrations/20260923180000_a_priceable_market_is_one_whose_local_day_has_not_ended.sql":
        "Active-city operational input",
    "sql/ad4_87_market_settlement_gaps.sql":    "Active-city operational input",
    "sql/ad4_30_open_meteo.sql":                "Active-city operational input",
    "sql/ad4_32_run_scope.sql":                 "Active-city operational input",
    "sql/ad4_33_control.sql":                   "Active-city operational input",
    "sql/ad4_71_observation_health.sql":        "Active-city operational input",
    "sql/ad4_75_probability_reliability.sql":   "Active-city operational input",
    "sql/ad4_live_weather_timing.sql":          "Active-city operational input",

    "supabase/migrations/20261004120000_the_floor_is_what_the_venue_reads.sql":
        "Active-city operational input",
    "supabase/migrations/20261004150000_the_basis_is_the_feeds.sql":
        "Active-city operational input",
    "supabase/migrations/20261009090100_the_feed_age_reads_the_cache.sql":
        "Active-city operational input",
    "supabase/migrations/20261016081209_station_report_fallback.sql":
        "Active-city operational input",
    "supabase/migrations/20261023171236_awc_contract_observation_receipts.sql":
        "Active-city operational input",

    "supabase/migrations/20261005110000_the_model_inputs_have_their_own_freshness.sql":
        "Active-city operational input",

    "supabase/migrations/20261006180000_each_city_has_a_daily_status.sql":
        "Active-city operational input",

    "sql/ad4_86_trajectory.sql":                "Active-city operational input",

    "sql/ad4_34_trade_plan.sql":                "Active-city operational input",
}
SQL_RECORD = {
    "supabase/migrations/20261018171559_monitoring_city_clocks_and_daily_maximum.sql",  
    "supabase/migrations/20261017170738_frozen_city_learning.sql",  
    "supabase/migrations/20261018084700_city_predictor_selection.sql",  
    "sql/ad4_city_calibration_forward.sql",  

    "sql/ad4_88_hit_tournament.sql",

    "sql/ad4_80_prune_edge_history.sql",
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

    "sql/ad4_82_settlement_agreement.sql",

    "sql/ad4_85_city_hit_history.sql",

    "sql/ad4_96_prune_band_probabilities.sql",

    "sql/ad4_97_evidence_cache.sql",

    "sql/ad4_weather_readers_read_the_caches.sql",
}

MIGRATION_RECORD = {
    "supabase/migrations/20261030080013_day_max_prediction_paper.sql":
        "Clock lookup for one already selected frozen call, not city enumeration",
    "supabase/migrations/20261016174633_city_calibration_forward_record.sql":
        "Historical or diagnostic city lookup",

    "supabase/migrations/20261009090000_the_weather_tables_keep_three_days.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20261008200000_the_long_weather_readers_read_the_caches.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260930100000_the_ladders_are_asked_market_by_market.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260930004500_the_hit_record_shows_the_priced_centre.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260929230000_the_marks_the_prune_took_come_back.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260929220000_the_edges_keep_the_prices_the_record_reads.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260929210000_the_weather_tables_keep_thirty_days.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260929190000_the_backtest_counts_what_it_counted.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260929180000_the_station_days_and_forecast_leads_outlast_the_keep.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260929160000_the_evidence_outlasts_the_weather_tables.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260929140000_the_prices_readers_use_stay.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260926100000_calibration_learns_frozen_calls.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260924110000_fixed_risk_rails.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260928090000_the_cluster_rail.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260923170000_trust_shrunk_toward_the_pool.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260923160000_when_was_the_forecast_issued.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260923140000_a_market_whose_day_ended_is_closed.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260922230000_a_rule_is_only_as_good_as_the_thermometer_under_it.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260912213000_phase1_data_foundation.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260912234500_phase1c_operational_readiness.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260923010000_readiness_reads_one_row_per_band.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260913091000_phase1d_databank_city_rollup.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260913092000_phase1d_city_metadata_evidence.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260913102000_phase2a_artifact_provenance.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260914100000_observation_prune_local_day_guard.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260914110000_archive_prune_count_contract.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260929010000_a_cut_day_keeps_its_cached_values.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20261004190000_one_prediction_contract.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20261004200000_the_page_reads_the_contract.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20261005090000_the_same_day_corrected_candidate.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20261004160000_one_morning_reading.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20261004130000_the_label_is_what_the_venue_reads.sql":
        "Historical or diagnostic city lookup",
    "supabase/migrations/20260929100000_the_climb_profile_reads_thirty_days.sql":
        "Historical or diagnostic city lookup",
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

    touching = set(sql_files_touching_cities())
    stale = sorted((set(SQL_FILTERS) | SQL_RECORD | set(MIGRATION_RECORD)) - touching)
    assert stale == [], (
        "these are classified but no longer build from cities: " + ", ".join(stale))

def test_the_record_is_never_filtered():

    for path in sorted(SQL_RECORD):
        assert path not in SQL_FILTERS, f"{path} cannot be both"
