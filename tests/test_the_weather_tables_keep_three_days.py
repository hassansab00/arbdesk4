"""The weather tables keep three days (Fresh Supabase, part 2b, 9 Oct;
20261009090000_the_weather_tables_keep_three_days.sql).

Observations 32 -> 3 and forecasts 30 -> 3, wanted and floor alike. Each prune
is the body live before it (prune_observations from 20260929210000,
prune_forecasts from 20260929180000) with its floor changed and, for the
observations, one guard added: every whole day going must have its peak in
derived_city_day_peak. tests/database/weather-tables-keep-three-days.cjs runs
both on PGlite; this file holds the texts together.
"""
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "scripts"))
import archive_observations as ao  # noqa: E402

MIG = ROOT / "supabase" / "migrations"
NEW = MIG / "20261009090000_the_weather_tables_keep_three_days.sql"
VIEW = MIG / "20261009090100_the_feed_age_reads_the_cache.sql"
LIVE_OBS = MIG / "20260929210000_the_weather_tables_keep_thirty_days.sql"
LIVE_FC = MIG / "20260929180000_the_station_days_and_forecast_leads_outlast_the_keep.sql"
SQL = ROOT / "sql"


def _fn(path, name):
    text = path.read_text(encoding="utf-8")
    i = text.index(f"create or replace function public.{name}(")
    return text[i:text.index("to service_role;", i)]


def _without_floor(body):
    """The body with its floor block (any comment above it and the if ... end
    if) and the function comment's floor sentence taken out."""
    start = body.index("begin\n") + len("begin\n")
    end = body.index("end if;", body.index("p_keep_days <")) + len("end if;")
    return (body[:start] + body[end:]).replace(" Refuses under 3 days.", "")


PEAK_GUARD = re.compile(r"  -- \.\.\.and in derived_city_day_peak.*?'unkept_peak_days', v_unkept_peak\n    \);\n  end if;\n\n",
                        re.S)


def test_the_keeps():
    for name in ("observations", "forecasts"):
        assert (ao.TABLES[name]["keep_days"], ao.TABLES[name]["min_keep_days"]) == (3, 3), name


def test_the_observation_prune_changes_its_floor_and_adds_one_guard():
    new, live = _fn(NEW, "prune_observations"), _fn(LIVE_OBS, "prune_observations")
    assert re.search(r"if p_keep_days < 3 then", new) and re.search(r"if p_keep_days < 32 then", live)
    assert len(PEAK_GUARD.findall(new)) == 1, "the peak guard is not where it is expected"
    stripped = PEAK_GUARD.sub("", _without_floor(new))
    stripped = stripped.replace("  v_oldest timestamptz;\n  v_unkept_peak bigint;\n", "")
    stripped = stripped.replace(", and every whole day''s peak in derived_city_day_peak", "")
    assert stripped == _without_floor(live), "the migration changed more of prune_observations than the floor and the peak guard"


def test_the_forecast_prune_changes_its_floor_and_nothing_else():
    new, live = _fn(NEW, "prune_forecasts"), _fn(LIVE_FC, "prune_forecasts")
    assert re.search(r"if p_keep_days < 3 then", new) and re.search(r"if p_keep_days < 30 then", live)
    assert _without_floor(new) == _without_floor(live), "the migration changed more of prune_forecasts than its floor"


def test_the_install_files_refuse_under_three_too():
    obs = (SQL / "ad4_29_retention.sql").read_text(encoding="utf-8")
    fc = (SQL / "ad4_63_prune_forecasts.sql").read_text(encoding="utf-8")
    assert "if p_keep_days < 3 then" in obs and "p_keep_days < 32" not in obs
    assert "if p_keep_days < 3 then" in fc and "p_keep_days < 30" not in fc


def _query(text):
    """The peak guard's count query, schema prefixes and layout aside."""
    i = text.index("select count(*) into v_unkept_peak")
    q = text[i:text.index(");", text.index("from public.derived_city_day_peak" if "public.derived_city_day_peak" in text[i:]
                                             else "from derived_city_day_peak", i)) + 2]
    return " ".join(q.replace("public.", "").split())


def test_the_install_file_guards_the_same_days():
    assert _query((SQL / "ad4_29_retention.sql").read_text(encoding="utf-8")) == _query(NEW.read_text(encoding="utf-8"))


def test_the_guard_takes_whole_days_by_the_refreshs_rule():
    """refresh_city_day_hours keeps a peak for days from the first whole one:
    the local day of the oldest reading, the next if that day began before
    it. The guard must ask for exactly those, or it refuses every night (a
    stricter rule) or lets an uncached day go (a looser one)."""
    refresh = (SQL / "ad4_97_evidence_cache.sql").read_text(encoding="utf-8")
    assert "select min(valid_at) into v_oldest from weather_observations;" in refresh
    assert "if (v_first::timestamp at time zone v_tz) < v_oldest then" in refresh
    assert "where x.rk = 1 and x.d >= v_first" in refresh
    guard = _query(NEW.read_text(encoding="utf-8"))
    assert "::date::timestamp at time zone coalesce(c.timezone, 'UTC')) < v_oldest then 1 else 0 end as first_whole" in guard
    assert "where x.d >= x.first_whole" in guard and "o.temp_c is not null" in guard


def test_the_view_statement_is_ad4_71s_verbatim():
    ad4_71 = (SQL / "ad4_71_observation_health.sql").read_text(encoding="utf-8")
    opener = "create view v_city_observation_health as"
    i = ad4_71.index(opener)
    closer = "left join live_weather lw  on lw.city_key = t.city_key;"
    oh = ad4_71[i:ad4_71.index(closer, i) + len(closer)].replace(
        opener, "create or replace view public.v_city_observation_health as", 1)
    assert "$oh$" + oh + "$oh$" in VIEW.read_text(encoding="utf-8")
    assert "from derived_station_day_sources k" in oh


def test_the_migration_schedules_the_reclaims_ad4_66_does():
    text = NEW.read_text(encoding="utf-8")
    jobs = dict(re.findall(r"cron\.schedule\('(ad4_reclaim_weather_(?:forecasts|observations))', '([^']+)'", text))
    reclaim = (SQL / "ad4_66_reclaim_archived_tables.sql").read_text(encoding="utf-8")
    want = dict(re.findall(r"cron\.schedule\(\s*'(ad4_reclaim_weather_(?:forecasts|observations))'\s*,\s*'([^']+)'", reclaim))
    assert jobs == want == {"ad4_reclaim_weather_forecasts": "20 6 * * *",
                            "ad4_reclaim_weather_observations": "35 6 * * *"}
    # PGlite (tests/database/paper-contracts.cjs) has no cron schema.
    assert "if exists (select 1 from pg_namespace where nspname = 'cron') then" in text
