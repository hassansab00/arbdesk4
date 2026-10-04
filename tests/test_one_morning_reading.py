"""One morning reading, chosen the same way every time (4 Oct).

v_city_day_features took the reading nearest 08:00 by the whole local hour
alone, so every 08:xx reading tied and Postgres took any of them; the cached
morning features (weather_model's morning_temp_c, dewpoint_depression_c,
morning_humidity, pressure terms) moved between refreshes. Now: the settlement
feed's reading first, then the nearest by the minute, then the earlier.
data/repairs/2026-10-04-morning-reading records the cached rows it recomputed;
tests/database/feature-cache-cut-day.cjs runs the rule on the real view."""
import hashlib
import os
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = ("supabase", "migrations", "20261004160000_one_morning_reading.sql")
REPAIR = os.path.join(ROOT, "data", "repairs", "2026-10-04-morning-reading")
COLS = ["city_key", "obs_date", "n_obs", "max_c", "min_c", "diurnal_range_c", "prev_max_c",
        "delta_max_c", "morning_temp_c", "morning_dewpoint_c", "dewpoint_depression_c",
        "morning_humidity", "morning_pressure_hpa", "morning_to_max_c", "cloud_mean", "cloud_max",
        "wind_mean", "wind_max", "precip_total", "pressure_change_24h_hpa", "wind_u_mean",
        "wind_v_mean", "computed_at"]
MORNING = {"morning_temp_c", "morning_dewpoint_c", "dewpoint_depression_c", "morning_humidity",
           "morning_pressure_hpa", "morning_to_max_c", "pressure_change_24h_hpa"}


def _read(*p):
    return open(os.path.join(ROOT, *p)).read()


def test_the_migration_carries_the_shipped_view_verbatim():
    ad4_21 = _read("sql", "ad4_21_weather_features.sql")
    i = ad4_21.index("create or replace view v_city_day_features as")
    end = "left join morning m on m.city_key = d.city_key and m.obs_date = d.obs_date;"
    stmt = ad4_21[i:ad4_21.index(end, i) + len(end)]
    stmt = stmt.replace("create or replace view v_city_day_features as",
                        "create or replace view public.v_city_day_features as", 1)
    assert "$v$" + stmt + "$v$" in _read(*MIGRATION)


def test_the_morning_reading_has_one_order():
    ad4_21 = _read("sql", "ad4_21_weather_features.sql")
    morning = ad4_21[ad4_21.index("morning as ("):ad4_21.index("\nselect\n  d.city_key")]
    assert "order by city_key, obs_date, abs(local_hour - 8)" not in morning
    assert ("order by city_key, obs_date,\n"
            "           (source = 'IEM') desc,\n"
            "           abs(extract(epoch from (local_ts - (obs_date + time '08:00')))),\n"
            "           local_ts") in morning
    assert "(o.valid_at at time zone coalesce(c.timezone, 'UTC'))                    as local_ts" in ad4_21


def _dump(name):
    rows = {}
    for line in open(os.path.join(REPAIR, name)).read().splitlines():
        f = line.split("|")
        assert len(f) == len(COLS), line
        rows[(f[0], f[1])] = dict(zip(COLS, f))
    return rows


def test_the_dumps_are_the_ones_the_database_hashed():
    readme = open(os.path.join(REPAIR, "README.md")).read()
    for name, n in (("cache_before.txt", 1580), ("cache_after.txt", 1584)):
        body = open(os.path.join(REPAIR, name)).read()
        assert body.endswith("\n")
        assert hashlib.md5(body[:-1].encode()).hexdigest() in readme, name
        assert body.count("\n") == n


def test_only_the_morning_columns_moved_on_days_whose_readings_did_not():
    before, after = _dump("cache_before.txt"), _dump("cache_after.txt")
    assert set(before) <= set(after)
    changed = 0
    for k in before:
        num = lambda v: None if v == "~" else Decimal(v)
        diff = [c for c in COLS[2:-1] if num(before[k][c]) != num(after[k][c])]
        if not diff:
            continue
        if before[k]["n_obs"] != after[k]["n_obs"]:
            continue          # readings arrived after the nightly refresh (README)
        assert set(diff) <= MORNING, (k, diff)
        changed += 1
    assert changed == 722
