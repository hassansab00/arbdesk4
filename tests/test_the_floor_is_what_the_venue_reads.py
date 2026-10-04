"""The pricing floor is the maximum of the feed the venue settles on (4 Oct).

The US cities carry the station's routine reports (source 'IEM', what the venue
reads) and NWS five-minute readings, whose maximum runs warm of them. On
24 Sep - 2 Oct 19 of 450 US checkpoint calls had a floor above the winning
bucket, every one from a non-IEM reading (Houston 1 Oct: floor 33 C, winner
88-90 F, the engine's probability on it 0.0006). The SQL (ad4_71's views,
ad4_live_weather_timing's stored maximum) takes the settlement feed first;
probability_engine.measured_floor is the second lock.
tests/database/observation-health.cjs runs the SQL on a Houston-like city."""
import os
import re

import probability_engine as pe

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = os.path.join(ROOT, "supabase", "migrations", "20261004120000_the_floor_is_what_the_venue_reads.sql")


def _read(*p):
    return open(os.path.join(ROOT, *p)).read()


def _stmt(text, opener, closer):
    i = text.index(opener)
    j = text.index(closer, i + len(opener))
    return text[i:j + len(closer)]


def test_the_migration_carries_the_shipped_statements_verbatim():
    mig = _read("supabase", "migrations", os.path.basename(MIGRATION))
    ad4_71 = _read("sql", "ad4_71_observation_health.sql")
    timing = _read("sql", "ad4_live_weather_timing.sql")
    pairs = [
        (_stmt(ad4_71, "create view v_city_observation_health as", "left join live_weather lw  on lw.city_key = t.city_key;")
         .replace("create view v_city_observation_health as", "create or replace view public.v_city_observation_health as", 1)),
        (_stmt(ad4_71, "create view v_city_running_max as", "from v_city_observation_health h;")
         .replace("create view v_city_running_max as", "create or replace view public.v_city_running_max as", 1)),
        _stmt(timing, "create or replace function public.refresh_live_weather_timing()", "\nend;\n$$;"),
    ]
    for p in pairs:
        assert p in mig, p.splitlines()[0]


def test_both_halves_take_the_settlement_feed_first():
    ad4_71 = _read("sql", "ad4_71_observation_health.sql")
    timing = _read("sql", "ad4_live_weather_timing.sql")
    assert "where r.source = 'IEM'" in ad4_71
    assert re.search(r"coalesce\(h\.settlement_max_today_c,\s+greatest\(", ad4_71)
    assert "from today where source = 'IEM'" in timing
    assert "when c.settle_max_c is not null then c.settle_max_c" in timing
    # the old rule stays visible, and which one answered is named
    assert "as all_sources_max_c" in ad4_71 and "as running_max_source" in ad4_71


def test_the_floor_is_the_settlement_maximum_when_there_is_one():
    houston = {"running_max_c": 33.0, "observed_max_today_c": 33.0, "live_source_kind": "station",
               "settlement_max_today_c": 31.67}
    assert pe.measured_floor(houston) == 31.67
    # a view that regressed (running_max_c from every source) still cannot reach a price
    assert pe.measured_floor(dict(houston, running_max_c=34.0)) == 31.67
    # no settlement reading today: the rules as before
    assert pe.measured_floor({"running_max_c": 25.0, "observed_max_today_c": 25.0,
                              "live_source_kind": "station", "settlement_max_today_c": None}) == 25.0
    assert pe.measured_floor({"running_max_c": 23.2, "observed_max_today_c": 21.0,
                              "live_source_kind": "model"}) == 21.0
    assert pe.measured_floor({"running_max_c": None, "live_source_kind": "station"}) is None


def test_every_floor_reader_asks_for_the_settlement_maximum():
    for path in (("scripts", "probability_engine.py"), ("scripts", "tick.py")):
        src = _read(*path)
        i = src.index('"v_city_running_max"')
        assert "settlement_max_today_c" in src[i:i + 400], path
