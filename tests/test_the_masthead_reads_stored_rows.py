"""The masthead reads stored rows and indexed times (plan v2 P6.5; WXPredict
build 2.E, R11/R15; 9 Oct).

156 browser reads died at the 8 s statement timeout in the 24 h to 9 Oct
20:34Z; 95 of them were the two reads every open page repeats, the masthead's
count of tradeable edges (v_opportunities, once a minute) and the freshness
chips (v_data_freshness, every two minutes). v_opportunities is now stored
rows; the timestamps v_data_freshness takes max() of are indexed.
tests/database/masthead-cache.cjs holds the database side. These hold the
readers and the two installs to it.
"""
import pathlib
import re

import yaml

import signal_engine

ROOT = pathlib.Path(__file__).resolve().parents[1]
MIG = (ROOT / "supabase/migrations/20261009210000_the_masthead_reads_stored_rows_and_indexed_times.sql").read_text()
SQL = (ROOT / "sql/ad4_89_page_cache.sql").read_text()
AD4_39 = (ROOT / "sql/ad4_39_freshness.sql").read_text()
AD4_44 = (ROOT / "sql/ad4_44_indexes.sql").read_text()


def _block(text, start, end):
    i = text.index(start)
    return text[i:text.index(end, i)]


def _opportunities_block(text):
    i = text.index("v       regclass := to_regclass('public.v_opportunities');")
    end = "The pages read v_opportunities, which selects from here.';"
    return text[i:text.index(end, i) + len(end)]


def _refresh(text):
    return _block(text, "create or replace function public.refresh_page_cache()", "end $$;")


SHARED = [
    "def := pg_get_viewdef(v, true);",
    "execute format('create view public.v_opportunities_live as %s', def);",
    "create materialized view public.mv_opportunities as select v.* from public.v_opportunities_live v;",
    "create unique index mv_opportunities_key on public.mv_opportunities (band_id, side);",
    "execute format('create or replace view public.v_opportunities as select %s from public.mv_opportunities '",
    "'where resolution_date >= (now() at time zone coalesce(timezone, ''UTC''))::date '",
    "'order by score desc nulls last', cols);",
    "revoke all on public.v_opportunities_live from public, anon, authenticated;",
    "grant select on public.v_opportunities_live to service_role;",
    "revoke all on public.mv_opportunities from public, anon, authenticated;",
    "grant select on public.mv_opportunities to service_role;",
]


def test_both_installs_store_the_opportunities_the_same_way():
    """The same build in both; the migration removes nothing (it refuses
    where an old copy is left), the install file rebuilds that case."""
    mig, sql = _opportunities_block(MIG), _opportunities_block(SQL)
    for line in SHARED:
        assert line in mig and line in sql, line
    # Replaced IN PLACE, so its grants and the five views built on it stay bound.
    assert not re.search(r"\bcascade\b", MIG, re.I), "nothing built on v_opportunities may be dropped with it"
    assert not re.search(r"\bdrop\b", MIG, re.I), "the migration drops nothing"
    assert "raise exception 'v_opportunities is live but a stored copy exists: run sql/ad4_89_page_cache.sql" in mig
    assert "drop materialized view if exists public.mv_opportunities;" in sql
    assert "drop view if exists public.v_opportunities_live;" in sql


def test_the_wrapper_reapplies_the_live_views_own_clock_condition():
    """The condition the wrapper re-applies is the one sql/ad4_13 gives
    v_opportunities, on the columns it carries through (resolution_date and
    the city's timezone)."""
    reconcile = (ROOT / "sql/ad4_13_reconcile.sql").read_text()
    body = reconcile[reconcile.index("create view v_opportunities as"):]
    body = body[:body.index(";")]
    assert re.search(r"m\.resolution_date\s*>=\s*\(now\(\)\s+at\s+time\s+zone\s+coalesce\(c\.timezone,\s*'UTC'\)\)::date", body, re.I)
    assert re.search(r"\bc\.timezone\b", body) and re.search(r"\bm\.resolution_date\b", body)
    assert re.search(r"order by .*score.* desc nulls last", body, re.I | re.S)


def test_the_refresh_carries_the_opportunities_first_and_never_blocks_a_reader():
    for text in (MIG, SQL):
        body = _refresh(text)
        assert "array['mv_opportunities', 'mv_prediction_ladder', 'mv_city_ladder_edges', " \
               "'mv_forecast_convergence_all', 'mv_city_hit_history']" in body
        assert "refresh materialized view concurrently" in body
    assert _refresh(MIG) == _refresh(SQL)


def test_the_strategies_read_the_live_definition():
    """signal_engine runs right behind the edge engine and before the
    pipeline refreshes the stored rows, so the stored copy would hand it the
    edges of the run before."""
    assert signal_engine.BOARD == "v_opportunities_live"
    src = (ROOT / "scripts/signal_engine.py").read_text()
    assert "rest_all(BOARD, " in src
    steps = [s.get("name") for s in yaml.safe_load(
        (ROOT / ".github/workflows/pipeline_intraday.yml").read_text())["jobs"]["intraday"]["steps"]]
    assert steps.index("Edges") < steps.index("Strategy signals") < steps.index("Refresh the stored page rows")


def test_no_script_reads_the_stored_opportunities():
    """Every Python and n8n read of v_opportunities is one of the stored
    copy's; a decision made on it would be up to half an hour behind."""
    for path in sorted((ROOT / "scripts").glob("*.py")) + sorted((ROOT / "n8n").glob("*.json")):
        code = "\n".join(l for l in path.read_text().splitlines() if not l.lstrip().startswith("#"))
        assert not re.search(r"""["']v_opportunities["']""", code), f"{path.name} reads the stored v_opportunities"


FRESH = ["resolution_verdicts", "band_probabilities", "derived_model_forecast", "research_captures",
         "weather_resolution_evidence", "prediction_checkpoints", "weather_resolution_attempts", "anomalies",
         "weather_forecast_models", "ingest_log", "fact_signal_outcome"]


def test_every_freshness_index_is_a_table_the_view_reads():
    listed = re.findall(r"'([a-z_]+)'", _block(MIG, "foreach t in array array[", "] loop"))
    assert listed == FRESH
    spec = {m.group(1) for m in re.finditer(r"^\s*\('([a-z_]+)',", AD4_39, re.M)}
    for t in FRESH:
        assert t in spec, f"{t} is not in data_freshness_spec, so v_data_freshness takes no max() of it"


def test_the_freshness_indexes_are_ad4_44s_own():
    """Same name and shape as ad4_44 builds, and skipped where an index
    already leads with the column, so a reinstall of ad4_44 builds no second
    copy - and ad4_44 leaves out the two tables the migration leaves out."""
    assert "'ad4_ix_fresh_' || t, t, col" in MIG
    assert "(%I desc nulls last)" in MIG
    assert "idx := format('ad4_ix_fresh_%s', r.table_name);" in AD4_44
    assert "(%I desc nulls last)" in AD4_44
    assert "a.attnum = i.indkey[0]" in MIG and "a.attnum = i.indkey[0]" in AD4_44
    assert "select s.ts_column into col from public.data_freshness_spec s where s.table_name = t;" in MIG
    for t in ("derived_band_day_volume", "markets"):
        assert t not in FRESH
    assert "continue when r.table_name in ('derived_band_day_volume', 'markets');" in AD4_44


def test_postgrest_is_told_about_the_new_live_view():
    """The strategies read v_opportunities_live through PostgREST, which
    answers PGRST205 for a relation its schema cache has not seen (Codex on
    #358)."""
    for text in (MIG, SQL):
        body = text[text.index("create view public.v_opportunities_live as %s"):]
        assert "notify pgrst, 'reload schema';" in body

