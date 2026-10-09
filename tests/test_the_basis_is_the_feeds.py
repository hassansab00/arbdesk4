"""The running maximum's basis is counted over the feed it came from (4 Oct).

A review of #297 found two things still counting every source after the
maximum became the settlement feed's: the basis ('series' for one routine
report beside many NWS readings) and the health flag that called a warmer
NWS or model reading "impossible". tests/database/observation-health.cjs runs
both on the real SQL."""
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = ("supabase", "migrations", "20261004150000_the_basis_is_the_feeds.sql")


def _read(*p):
    return open(os.path.join(ROOT, *p)).read()


def _stmt(text, opener, closer):
    i = text.index(opener)
    return text[i:text.index(closer, i + len(opener)) + len(closer)]


def test_the_migration_carries_the_shipped_statements_verbatim():
    mig = _read(*MIGRATION)
    ad4_71 = _read("sql", "ad4_71_observation_health.sql")
    timing = _read("sql", "ad4_live_weather_timing.sql")
    oh = (_stmt(ad4_71, "create view v_city_observation_health as",
                "left join live_weather lw  on lw.city_key = t.city_key;")
          .replace("create view v_city_observation_health as",
                   "create or replace view public.v_city_observation_health as", 1))
    fn = _stmt(timing, "create or replace function public.refresh_live_weather_timing()", "\nend;\n$$;")
    # The view is re-issued by the NEWEST migration that carries it: since
    # 9 Oct its feed age reads the station cache for a city the readings no
    # longer hold (Fresh Supabase, part 2b). This one's view is ad4_71's but
    # for that one CTE.
    newest = _read("supabase", "migrations", "20261009090100_the_feed_age_reads_the_cache.sql")
    assert "$oh$" + oh + "$oh$" in newest
    feed = _stmt(oh, "feed as (", "\n),\n")
    old_feed = ("feed as (\n  select t.city_key,\n         (select max(o.valid_at)\n"
                "            from weather_observations o\n           where o.city_key = t.city_key\n"
                "             and o.temp_c is not null)                                  as newest_reading\n"
                "  from tz t\n),\n")
    shipped = mig[mig.index("$oh$") + 4:mig.index("$oh$", mig.index("$oh$") + 4)]
    assert old_feed in shipped
    comment = oh[:oh.index("feed as (")]
    comment = comment[comment.rindex("),\n") + 3:]
    assert shipped.replace(old_feed, comment + feed) == oh, "the 9 Oct view changed more than the feed age"
    assert "$fn$" + fn + "$fn$" in mig


def test_both_sides_count_the_settlement_readings_when_the_maximum_is_theirs():
    ad4_71 = _read("sql", "ad4_71_observation_health.sql")
    timing = _read("sql", "ad4_live_weather_timing.sql")
    assert ("case when s.settlement_max_today_c is not null then s.settlement_readings_today\n"
            "         else coalesce(d.readings_today, 0) end::integer") in ad4_71
    assert ("case when f.settle_max_c is not null then f.settle_readings\n"
            "                                    else f.readings_today end") in timing


def test_only_a_reading_the_maximum_is_built_from_can_be_impossible():
    ad4_71 = _read("sql", "ad4_71_observation_health.sql")
    flag = ad4_71[:ad4_71.index("as stored_max_below_latest")]
    flag = flag[flag.rindex("((sd.settlement_max_due_c is not null"):]
    assert "lw.source_kind is not distinct from 'station'" in flag
    assert "s.settlement_max_today_c is null" in flag
    # ...and a stale stored maximum still shows beside settlement readings,
    # once the refresh has had time to take them in (pg_cron every 10 min)
    assert "sd.settlement_max_due_c > lw.running_max_c" in flag
    assert "o.observed_at <= now() - interval '15 minutes'" in ad4_71
