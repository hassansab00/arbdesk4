"""The Predictive page reads stored rows (plan v2 P6.5).

On 24 Sep the gateway log held 300 browser reads that died at the 8 s
statement timeout in 24 hours. The three heaviest page views (2.6, 2.5 and
1.9 s alone) are now wrappers over materialized copies, refreshed
CONCURRENTLY; midday VACUUM FULL is moved to the night. These tests hold the
shape in place.
"""
import pathlib
import re

import yaml

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = (ROOT / "supabase/migrations/20260924040000_pages_read_stored_rows.sql").read_text()
SQL = (ROOT / "sql/ad4_89_page_cache.sql").read_text()
ORDER = [l.strip() for l in (ROOT / "sql/INSTALL_ORDER.txt").read_text().splitlines()
         if l.strip() and not l.startswith("#")]
CACHED = ("v_prediction_ladder", "v_forecast_convergence_all", "v_city_hit_history")


def test_every_cached_view_is_converted_in_place_with_a_unique_key():
    for text in (MIG, SQL):
        for v in CACHED:
            assert f"('{v}'," in text
        assert "create or replace view public.%I as select %s from public.%I" in text, (
            "replaced IN PLACE, so grants and dependent views stay bound")
        assert "create unique index" in text, "CONCURRENTLY needs a unique index"
        assert "revoke all on public.%I from public, anon, authenticated" in text


def test_the_refresh_never_blocks_a_reader():
    for text in (MIG, SQL):
        body = text[text.index("create or replace function public.refresh_page_cache()"):]
        assert "refresh materialized view concurrently" in body


def test_the_page_cache_installs_after_every_file_that_defines_its_views():
    at = ORDER.index("ad4_89_page_cache.sql")
    for f in ("ad4_31_predictive.sql", "ad4_62_settled_history_ungated.sql",
              "ad4_68_prediction_ladder_outcomes.sql", "ad4_85_city_hit_history.sql"):
        assert ORDER.index(f) < at, f


def test_the_pipelines_refresh_after_they_write():
    for wf in ("pipeline_intraday.yml", "pipeline_daily.yml"):
        doc = yaml.safe_load((ROOT / ".github/workflows" / wf).read_text())
        steps = next(iter(doc["jobs"].values()))["steps"]
        assert steps[-1]["name"] == "Refresh the stored page rows", wf
        assert 'rpc("refresh_page_cache")' in steps[-1]["run"]


def test_no_vacuum_full_is_scheduled_for_the_middle_of_the_day():
    for text in (MIG, (ROOT / "sql/ad4_66_reclaim_archived_tables.sql").read_text()):
        body = text[text.index("create or replace function public.request_reclaim"):]
        assert "if v_hour < 6 then" in body
        assert "interval '1 day' + interval '1 hour'" in body
        assert re.search(r"2 \* \(array_position\(v_allowed, p_table\) - 1\)", body), "staggered"
