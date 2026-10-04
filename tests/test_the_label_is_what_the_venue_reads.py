"""The station label is the maximum of the feed the venue settles on (4 Oct).

derived_city_day_features.max_c, which station_mos, station_correction, the
weekly weather fit and the S10 files all train on, was the maximum over every
reading. From 5 Sep the US cities also carry NWS five-minute readings, whose
maximum runs warm of the routine reports the venue settles on (source 'IEM').
v_city_day_features now takes the settlement feed's maximum first, and
data/repairs/2026-10-04-settlement-max records the cached rows it recomputed.
These tests hold the migration to the shipped view, the source literal to
obs_primary_source(), and the repair record's numbers to its own files."""
import hashlib
import os
import re
from decimal import Decimal

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
MIGRATION = ("supabase", "migrations", "20261004130000_the_label_is_what_the_venue_reads.sql")
REPAIR = os.path.join(ROOT, "data", "repairs", "2026-10-04-settlement-max")

# The dump's columns, as tools/repair_day_features.py writes them.
COLS = ["city_key", "obs_date", "n_obs", "max_c", "min_c", "diurnal_range_c", "prev_max_c",
        "delta_max_c", "morning_temp_c", "morning_dewpoint_c", "dewpoint_depression_c",
        "morning_humidity", "morning_pressure_hpa", "morning_to_max_c", "cloud_mean", "cloud_max",
        "wind_mean", "wind_max", "precip_total", "pressure_change_24h_hpa", "wind_u_mean",
        "wind_v_mean", "computed_at"]
LABEL = {"max_c", "diurnal_range_c", "morning_to_max_c", "prev_max_c", "delta_max_c"}


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


def test_max_c_is_the_settlement_feed_first_and_the_old_value_stays_visible():
    ad4_21 = _read("sql", "ad4_21_weather_features.sql")
    assert "coalesce(max(temp_c) filter (where source = 'IEM'), max(temp_c)) as max_c," in ad4_21
    assert "max(temp_c)                                       as max_c_all_sources," in ad4_21
    assert "d.max_c_all_sources," in ad4_21
    assert re.search(r"then 'settlement_feed' else 'all_sources' end as max_c_source", ad4_21)
    # the source column has to reach the aggregate
    assert "o.cloud_cover, o.pressure_hpa, o.wind_dir_deg, o.source" in ad4_21


def test_the_literal_is_obs_primary_source():
    """ad4_21 and ad4_71 install before ad4_82 defines obs_primary_source(),
    so they name the feed as a literal. It has to be the same one."""
    ad4_82 = _read("sql", "ad4_82_settlement_agreement.sql")
    m = re.search(r"function obs_primary_source\(\) returns text\s+language sql immutable "
                  r"set search_path = '' as \$\$ select '([A-Za-z]+)'::text \$\$;", ad4_82)
    assert m, "obs_primary_source() changed shape; re-check the literal in ad4_21, ad4_71 and ad4_live_weather_timing"
    feed = m.group(1)
    assert f"filter (where source = '{feed}')" in _read("sql", "ad4_21_weather_features.sql")
    assert f"where r.source = '{feed}'" in _read("sql", "ad4_71_observation_health.sql")
    assert f"from today where source = '{feed}'" in _read("sql", "ad4_live_weather_timing.sql")


def _dump(name):
    rows = {}
    for line in open(os.path.join(REPAIR, name)).read().splitlines():
        f = line.split("|")
        assert len(f) == len(COLS), line
        rows[(f[0], f[1])] = dict(zip(COLS, f))
    return rows


def _num(v):
    return None if v == "~" else Decimal(v)


def test_the_dumps_are_the_ones_the_database_hashed():
    readme = open(os.path.join(REPAIR, "README.md")).read()
    for name, n in (("cache_before.txt", 405), ("cache_after.txt", 412)):
        body = open(os.path.join(REPAIR, name)).read()
        assert body.endswith("\n")
        digest = hashlib.md5(body[:-1].encode()).hexdigest()
        assert digest in readme, name
        assert body.count("\n") == n


def test_the_repair_changed_the_label_and_nothing_else_on_settled_active_days():
    before, after = _dump("cache_before.txt"), _dump("cache_after.txt")
    assert set(before) <= set(after)
    changed = []
    for k in sorted(before):
        city, day = k
        diff = [c for c in COLS[2:-1] if _num(before[k][c]) != _num(after[k][c])]
        if not diff:
            continue
        # dc is retired: the nightly refresh skips it, so this one also brought
        # in readings it had never seen (README). 2 to 4 Oct were still
        # receiving readings after the nightly refresh ran.
        if city == "dc" or day >= "2026-10-02":
            continue
        assert set(diff) <= LABEL, (k, diff)
        assert before[k]["n_obs"] == after[k]["n_obs"], k
        if "max_c" in diff:
            changed.append(_num(after[k]["max_c"]) - _num(before[k]["max_c"]))
    # the README's numbers
    assert len(changed) == 152
    assert max(changed) < 0, "a settlement maximum can only be at or below the all-source maximum"
    assert min(changed) == Decimal("-2.11")
    assert sum(1 for d in changed if d <= Decimal("-0.6")) == 60
    assert sum(1 for d in changed if d <= Decimal("-1")) == 21


def test_no_settled_day_before_the_five_minute_feed_moved():
    before, after = _dump("cache_before.txt"), _dump("cache_after.txt")
    for k in before:
        if k[1] < "2026-09-05" and k[0] != "dc":
            assert before[k]["max_c"] == after[k]["max_c"], k
