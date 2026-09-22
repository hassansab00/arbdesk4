"""130,572 bearings, collected since the first day, spent on a compass glyph.

weather_observations.wind_dir_deg has existed since ad4_00. The IEM request
asks for it (`drct`), n8n P1.6 asks for it, both write it, and it is populated
on 130,572 of 144,601 rows - 90.3%. Then it stops dead: the `obs` CTE of
v_city_day_features never selected the column, so the daily aggregate had
nothing to work with, and the only consumer in the whole repo was
scripts/live_weather.py turning it into a compass point for the UI.

WHY IT MATTERS HERE. Downslope, offshore and onshore flow are different
weather out of the same pressure gradient. Measured on this repo's own
archive, the day's climb (morning_to_max_c) splits by resultant octant with a
spread of 8.08 C at Sao Paulo, 4.81 C at Cape Town and 4.16 C at Warsaw; on a
held-out OLS over 205 city-days the pair is worth about 0.135 C of MAE on
average and 0.26 to 0.70 C where the mechanism is strongest. Band widths here
are a degree or two.

A MEAN OF BEARINGS IS NOT A MEAN WIND. 350 and 10 degrees average to 180,
which is the opposite direction. The bearing has to be resolved into
components before anything is summed, and what is stored is the
speed-weighted resultant divided by the scalar wind run - so each component
is the direction's east/north share TIMES the day's directional constancy.
Dimensionless and bounded by [-1, 1], which is what lets one coefficient mean
the same thing in Denver and in Singapore; a raw u in knots would make the
term a proxy for how windy the city is rather than for where its wind is from.

THE COMPUTATION EXISTS FOUR TIMES - the observed SQL, the backfill, P1.4 and
P1.5 - because none of those can import from the others. The only thing
holding them together is this file.

AND BOTH SIDES LANDED TOGETHER, which is the part that is easy to get wrong.
The observed side is backfillable from data/archive/observations; the forecast
side is not, and neither P1.4 nor P1.5 was fetching a bearing. Offering the
feature to the fit with only one side carrying it is the silent failure
BASE_FEATURES' own comment describes: forecast_city skips any row missing a
feature the fit kept, so the city stops producing forward predictions with no
error and no note. It cost six cities exactly that in September.
"""

import json
import math
import pathlib
import re

import pytest

import backfill_wind_direction as bw
import weather_model as wm


ROOT = pathlib.Path(__file__).resolve().parents[1]
N8N = ROOT / "n8n"


# ---------------------------------------------------------------------------
# the quantity itself
# ---------------------------------------------------------------------------
def test_a_steady_wind_has_constancy_one():
    """Every hour from the same quarter: the resultant is the unit vector."""
    u, v = bw.resultant([(10.0, 90.0)] * 6)          # from the east
    assert u == pytest.approx(-1.0, abs=1e-4)        # blowing toward the west
    assert v == pytest.approx(0.0, abs=1e-4)
    assert math.hypot(u, v) == pytest.approx(1.0, abs=1e-4)


def test_a_day_that_boxed_the_compass_has_constancy_zero():
    """Four quarters, equal speed. The scalar wind run is large and the
    resultant is nothing - which is the distinction the ratio exists to make."""
    u, v = bw.resultant([(10.0, 0.0), (10.0, 90.0), (10.0, 180.0), (10.0, 270.0)])
    assert math.hypot(u, v) == pytest.approx(0.0, abs=1e-4)


def test_opposite_bearings_do_not_average_to_a_third_direction():
    """The regression this whole shape exists for: mean(350, 10) is 180."""
    u, v = bw.resultant([(10.0, 350.0), (10.0, 10.0)] * 3)
    # Both are northerlies, so the resultant blows very nearly due south.
    assert v < -0.9 and abs(u) < 0.05
    naive = (350.0 + 10.0) / 2
    assert naive == 180.0, "the arithmetic that would have been wrong"


def test_the_resultant_is_weighted_by_speed():
    """One hour of gale from the north and five of calm from the south is a
    northerly day. An unweighted mean would call it southerly."""
    u, v = bw.resultant([(40.0, 0.0)] + [(2.0, 180.0)] * 5)
    assert v < 0, "the 40-knot hour has to dominate"


def test_a_thin_day_gets_no_vector():
    """Two points make a resultant out of nothing much over a nine-hour
    window. 96.9% of city-days clear the floor of three."""
    assert bw.resultant([(10.0, 90.0), (10.0, 90.0)]) == (None, None)
    assert bw.resultant([]) == (None, None)
    assert bw.resultant([(10.0, 90.0)] * 3) != (None, None)


def test_a_calm_day_gets_no_vector_rather_than_a_divide_by_zero():
    assert bw.resultant([(0.0, 90.0)] * 6) == (None, None)


def test_a_missing_bearing_or_speed_is_not_counted():
    """Unpaired readings must not inflate n toward the floor."""
    assert bw.resultant([(10.0, 90.0), (10.0, None), (None, 90.0)]) == (None, None)


def test_the_components_stay_inside_the_unit_circle():
    """Bounded is the property that lets one coefficient travel between
    cities. Anything outside means the normalisation is wrong."""
    for readings in ([(3.0, 12.0), (9.0, 200.0), (4.0, 77.0), (1.0, 355.0)],
                     [(22.0, 180.0), (3.0, 0.0), (7.0, 271.0)],
                     [(5.5, 45.0)] * 9):
        u, v = bw.resultant(readings)
        assert u is not None and math.hypot(u, v) <= 1.0 + 1e-9


# ---------------------------------------------------------------------------
# the same quantity, four times, in three languages
# ---------------------------------------------------------------------------
OBS_SQL = (ROOT / "sql/ad4_21_weather_features.sql").read_text(encoding="utf-8")


def _js(template, node):
    doc = json.loads((N8N / template).read_text(encoding="utf-8"))
    for n in doc["nodes"]:
        if n["name"] == node:
            return n["parameters"]["jsCode"]
    raise AssertionError(f"{node} is not in {template}")


def test_the_observed_sql_resolves_the_bearing_before_summing():
    assert "sin(radians(wind_dir_deg))" in OBS_SQL
    assert "cos(radians(wind_dir_deg))" in OBS_SQL
    assert "-wind_speed * sin(radians(wind_dir_deg))" in OBS_SQL, (
        "the sign carries the meteorological convention: the bearing is where "
        "the wind comes FROM"
    )
    assert "avg(wind_dir_deg)" not in OBS_SQL, "a mean of bearings is not a mean wind"


def test_the_observed_sql_divides_by_the_scalar_run_not_the_count():
    """Dividing by wd_n would give a speed-weighted vector in knots; dividing
    by the scalar run gives directional constancy, which is bounded."""
    assert "d.wd_su / d.wd_scalar" in OBS_SQL
    assert "d.wd_sv / d.wd_scalar" in OBS_SQL


def test_every_side_uses_the_same_daytime_window():
    """09-17 local, the same window cloud_mean and wind_mean already use.
    A different window on one side is a different feature wearing its name."""
    assert (bw.DAY_FROM, bw.DAY_TO) == (9, 17)
    assert "local_hour between 9 and 17\n                and wind_dir_deg" in OBS_SQL
    for template, node in (("P1.5_open_meteo.template.json", "Build rows"),
                           ("P1.4_nws_gridpoint.template.json", "Build rows")):
        js = _js(template, node)
        assert "hour >= 9 && hour <= 17" in js or "h >= 9 && h <= 17" in js


def test_every_side_uses_the_same_floor_of_three_readings():
    assert bw.MIN_PAIRED_READINGS == 3
    assert "d.wd_n >= 3" in OBS_SQL
    assert "d.wdN >= 3" in _js("P1.5_open_meteo.template.json", "Build rows")
    assert "n < 3" in _js("P1.4_nws_gridpoint.template.json", "Build rows")


@pytest.mark.parametrize("template", ["P1.5_open_meteo.template.json",
                                      "P1.4_nws_gridpoint.template.json"])
def test_the_forecast_feeds_resolve_the_bearing_the_same_way(template):
    js = _js(template, "Build rows")
    assert "Math.sin(rad)" in js and "Math.cos(rad)" in js
    assert "-w * Math.sin(rad)" in js, "same sign convention as the SQL"
    assert "wind_u_mean" in js and "wind_v_mean" in js


def test_the_feeds_actually_ask_for_a_bearing():
    """They had not been. api.weather.gov has served windDirection in every
    gridpoint response all along and nothing requested it."""
    assert "wind_direction_10m" in _js("P1.5_open_meteo.template.json", "Build requests")
    assert "'windDirection'" in _js("P1.4_nws_gridpoint.template.json", "Build rows")


# ---------------------------------------------------------------------------
# both sides, which is the thing that is easy to get wrong
# ---------------------------------------------------------------------------
def test_it_is_offered_to_the_fit():
    assert "wind_u_mean" in wm.CANDIDATE_FEATURES
    assert "wind_v_mean" in wm.CANDIDATE_FEATURES
    assert not (set(("wind_u_mean", "wind_v_mean")) & set(wm.BASE_FEATURES)), (
        "a base feature is MANDATORY - a city with a stuck wind vane would "
        "lose every day it ever had"
    )


def test_it_is_read_from_both_sides():
    """The invariant the repo already holds for every other feature, stated
    again here because this is the one that had to land on two sides at once."""
    assert "wind_u_mean" in wm.FIT_COLUMNS and "wind_v_mean" in wm.FIT_COLUMNS
    assert "wind_u_mean" in wm.FORECAST_COLUMNS and "wind_v_mean" in wm.FORECAST_COLUMNS


def test_the_forecast_table_and_view_both_carry_it():
    ddl = (ROOT / "sql/ad4_24_nws_gridpoint.sql").read_text(encoding="utf-8")
    assert "wind_u_mean" in ddl and "wind_v_mean" in ddl
    migrations = sorted((ROOT / "supabase/migrations").glob("*.sql"))
    latest = None
    for path in migrations:
        text = path.read_text(encoding="utf-8")
        for m in re.finditer(r"create or replace view v_forecast_features as(.*?);",
                             text, re.S):
            latest = m.group(1)
    assert latest, "no migration defines v_forecast_features"
    assert "wind_u_mean" in latest and "wind_v_mean" in latest, (
        "the view is what predict_forward reads; a column on the table that "
        "the view does not select is still invisible"
    )


def test_the_cached_table_carries_it_on_an_existing_database():
    """`create table if not exists` never widens a table that already exists,
    so the DDL alone reaches a fresh install and nothing else. ad4_00's column
    registry is the mechanism that adds them to the live cache."""
    pre = (ROOT / "sql/ad4_00_preflight.sql").read_text(encoding="utf-8")
    assert "('derived_city_day_features','wind_u_mean','numeric')" in pre
    assert "('derived_city_day_features','wind_v_mean','numeric')" in pre
    assert "('weather_forecast_features','wind_u_mean','numeric')" in pre


def test_the_refresh_twins_are_still_identical():
    """refresh_feature_cache is defined in ad4_28 and ad4_29, byte for byte,
    on purpose. Editing one and not the other makes run order load-bearing."""
    def fn(path):
        s = (ROOT / path).read_text(encoding="utf-8")
        i = s.index("create or replace function refresh_feature_cache")
        return s[i:s.index("$ad4$;", i) + 6]
    a = fn("sql/ad4_28_feature_cache.sql")
    b = fn("sql/ad4_29_retention.sql")
    assert a == b, "the twins have drifted"
    assert "wind_u_mean" in a


def test_the_refresh_never_blanks_a_backfilled_day():
    """refresh_feature_cache sees only what weather_observations still holds -
    about 90 days - while the cache goes back 14 months. A plain assignment
    would wipe every backfilled vector outside the retention window on its
    first run, which is most of the history the fit trains on."""
    s = (ROOT / "sql/ad4_28_feature_cache.sql").read_text(encoding="utf-8")
    assert ("wind_u_mean = coalesce(excluded.wind_u_mean, "
            "derived_city_day_features.wind_u_mean)") in s


def test_the_backfill_only_touches_the_two_wind_columns():
    """Anything else in the payload would let a recomputed feature overwrite
    a cached one from an archive that may be older than the cache."""
    src = (ROOT / "scripts/backfill_wind_direction.py").read_text(encoding="utf-8")
    m = re.search(r'out\.append\(\{(.*?)\}\)', src, re.S)
    assert m
    keys = set(re.findall(r'"([a-z_]+)":', m.group(1)))
    assert keys == {"city_key", "obs_date", "wind_u_mean", "wind_v_mean"}


def test_the_backfill_never_invents_a_city_day():
    """A merge-duplicates write with no matching row is an INSERT, which would
    manufacture a feature day carrying two wind components and no maximum."""
    src = (ROOT / "scripts/backfill_wind_direction.py").read_text(encoding="utf-8")
    assert "if key not in have:" in src
    assert "upsert_replace(" in src and '"city_key,obs_date"' in src


def test_the_backfill_never_overwrites_a_vector_that_is_already_there():
    src = (ROOT / "scripts/backfill_wind_direction.py").read_text(encoding="utf-8")
    assert "if key not in missing:" in src
