from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
MIGRATION = ROOT / "supabase/migrations/20260912213000_phase1_data_foundation.sql"


def rectify_sql():
    return MIGRATION.read_text().lower()


def test_phase1_never_rewrites_proprietary_source_rows():
    sql = rectify_sql()
    sources = (
        "weather_observations",
        "weather_forecasts",
        "book_snapshots",
        "trades_observed",
        "bands",
        "markets",
        "cities",
    )
    for source in sources:
        assert f"delete from public.{source}" not in sql
        assert f"update public.{source}" not in sql
        assert f"truncate public.{source}" not in sql


def test_daily_archive_is_incremental_and_not_a_live_full_scan():
    sql = rectify_sql()
    assert "create table if not exists public.archive_daily_rollup" in sql
    assert "referencing new table as new_rows for each statement" in sql
    view = sql.split("create or replace view public.v_archive_daily", 1)[1]
    view = view.split("grant select on public.v_archive_daily", 1)[0]
    assert "archive_daily_rollup" in view
    assert "weather_observations" not in view
    assert "weather_forecasts" not in view
    assert "book_snapshots" not in view
    assert "trades_observed" not in view


def test_quality_findings_are_additive_and_idempotent():
    sql = rectify_sql()
    assert "insert into public.proprietary_data_quality_flags" in sql
    assert "not exists" in sql
    for issue in (
        "zero_width_non_tail_band",
        "market_city_unit_conflict",
        "future_observation_timestamp",
        "future_forecast_run_timestamp",
        "active_city_missing_coordinates",
        "active_city_missing_timezone",
    ):
        assert issue in sql


def test_rollup_view_obeys_rls_as_the_browser_role():
    sql = rectify_sql()
    assert "with (security_invoker = true)" in sql
    assert "enable row level security" in sql
    assert "for select to anon, authenticated using (true)" in sql
