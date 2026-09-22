#!/usr/bin/env python3
"""Fill wind_u_mean / wind_v_mean on the cached history, from the repo archive.

WHY A BACKFILL AND NOT JUST A REFRESH. refresh_feature_cache recomputes
derived_city_day_features from weather_observations, and the retention prune
keeps about 90 days there. The cache itself goes back to 2025-07-21 - 22,294
city-days - because surviving the prune is the whole reason it exists. So a
refresh alone fills the wind vector on roughly 4,800 recent city-days and
leaves 17,000 permanently null, which is most of the history the fit trains on
and all of the seasons it has to generalise across.

The hourly series those 17,000 days were built from is not gone. It is in
data/archive/observations/*.csv.gz, in this repository, and it carries
wind_dir_deg - the column was written from the start, it was simply never
aggregated. This reads those files and computes the same quantity the SQL
does, for the days the database can no longer see.

THE TWO COMPUTATIONS MUST AGREE. sql/ad4_21_weather_features.sql sums
-speed*sin(bearing) and -speed*cos(bearing) over local hours 9 to 17 and
divides by the scalar wind run, requiring at least three paired readings.
This does the same, in the city's own timezone, and
tests/test_wind_has_a_direction.py holds the two forms to the same numbers on
the same input. A feature that means two different things depending on which
side computed it is worse than one that is missing.

NOTHING IS DELETED AND NOTHING ELSE IS TOUCHED. Rows are matched on the
(city_key, obs_date) that already exist in the cache and only the two wind
columns are sent, so a day the archive cannot reach keeps its null rather than
gaining an invented number, and no other feature can be disturbed.

  python scripts/backfill_wind_direction.py [--dry-run] [--since 2025-07-01]
"""
import argparse
import csv
import datetime as dt
import glob
import gzip
import math
import os
import sys
from zoneinfo import ZoneInfo

from common import get_cities, rest_all, upsert_replace, log_run

ARCHIVE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                       "data", "archive", "observations")

# Same window and same floor as sql/ad4_21_weather_features.sql. Stated twice
# on purpose - the SQL cannot import this and this cannot read the SQL, so the
# only thing holding them together is a test that runs both.
DAY_FROM, DAY_TO = 9, 17
MIN_PAIRED_READINGS = 3


def resultant(readings):
    """(u, v) for one city-day, or (None, None).

    `readings` is [(speed, bearing_degrees)] over the daytime window. Returns
    the speed-weighted resultant divided by the scalar wind run, so each
    component is the direction's share TIMES the day's directional constancy:
    1.0 is a steady wind from one quarter, 0.0 is a day that boxed the compass.

    Meteorological convention - the bearing is where the wind comes FROM, so
    the vector it blows TOWARD is (-sin, -cos).
    """
    su = sv = scalar = 0.0
    n = 0
    for speed, bearing in readings:
        if speed is None or bearing is None:
            continue
        rad = math.radians(bearing)
        su += -speed * math.sin(rad)
        sv += -speed * math.cos(rad)
        scalar += speed
        n += 1
    if n < MIN_PAIRED_READINGS or scalar <= 0:
        return None, None
    return round(su / scalar, 4), round(sv / scalar, 4)


def _num(x):
    try:
        v = float(x)
        return v if v == v else None
    except (TypeError, ValueError):
        return None


def scan_archive(zones, since=None):
    """{(city_key, obs_date): [(speed, bearing)]} over the daytime window."""
    files = sorted(glob.glob(os.path.join(ARCHIVE, "*.csv.gz")))
    if not files:
        raise SystemExit(f"no archive files under {ARCHIVE} - nothing to backfill from")
    out, rows_read, skipped_city = {}, 0, set()
    for path in files:
        with gzip.open(path, "rt", newline="") as fh:
            for rec in csv.DictReader(fh):
                rows_read += 1
                city = (rec.get("city_key") or "").strip()
                tz = zones.get(city)
                if tz is None:
                    skipped_city.add(city)
                    continue
                speed, bearing = _num(rec.get("wind_speed")), _num(rec.get("wind_dir_deg"))
                if speed is None or bearing is None:
                    continue
                ts = (rec.get("valid_at") or "").strip()
                if not ts:
                    continue
                try:
                    when = dt.datetime.fromisoformat(ts.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if when.tzinfo is None:
                    when = when.replace(tzinfo=dt.timezone.utc)
                local = when.astimezone(tz)
                if not (DAY_FROM <= local.hour <= DAY_TO):
                    continue
                day = local.date().isoformat()
                if since and day < since:
                    continue
                out.setdefault((city, day), []).append((speed, bearing))
        print(f"  read {os.path.basename(path)}")
    if skipped_city:
        print(f"  {len(skipped_city)} archived city/cities are not active and were "
              f"skipped: " + ", ".join(sorted(skipped_city)[:8]), file=sys.stderr)
    return out, rows_read


def cached_days():
    """(city_key, obs_date) already in the cache, and how many lack a vector.

    ONLY EXISTING ROWS ARE WRITTEN. A merge-duplicates write with no matching
    row is an INSERT, which would manufacture a city-day carrying two wind
    components and nothing else - a row that looks like a feature day and has
    no maximum in it.
    """
    rows = rest_all("derived_city_day_features",
                    [("select", "city_key,obs_date,wind_u_mean")],
                    order="city_key.asc,obs_date.asc", page_size=1000)
    have = {(r["city_key"], str(r["obs_date"])) for r in rows}
    missing = {(r["city_key"], str(r["obs_date"])) for r in rows
               if r.get("wind_u_mean") is None}
    return have, missing


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--since", help="earliest obs_date to fill (YYYY-MM-DD)")
    args = ap.parse_args()

    cities = get_cities(require_coords=False)
    zones = {}
    for c in cities:
        try:
            zones[c["city_key"]] = ZoneInfo(c.get("timezone") or "UTC")
        except Exception:
            zones[c["city_key"]] = ZoneInfo("UTC")

    have, missing = cached_days()
    print(f"cache: {len(have)} city-day(s), {len(missing)} without a wind vector")

    by_day, rows_read = scan_archive(zones, since=args.since)
    print(f"archive: {rows_read} row(s) -> {len(by_day)} city-day(s) with daytime wind")

    out, thin, not_cached = [], 0, 0
    for key, readings in sorted(by_day.items()):
        if key not in have:
            not_cached += 1
            continue
        if key not in missing:
            continue                      # already has one; never overwrite
        u, v = resultant(readings)
        if u is None:
            thin += 1
            continue
        out.append({"city_key": key[0], "obs_date": key[1],
                    "wind_u_mean": u, "wind_v_mean": v})

    cities_filled = len({r["city_key"] for r in out})
    print(f"\n{len(out)} city-day(s) to fill across {cities_filled} city/cities")
    print(f"  {thin} skipped: fewer than {MIN_PAIRED_READINGS} paired daytime readings")
    print(f"  {not_cached} archived day(s) are not in the cache at all")
    still_null = len(missing) - len(out)
    print(f"  {still_null} cached day(s) will still have no vector - the archive "
          f"does not reach them")

    if args.dry_run:
        print("\n--dry-run: nothing written")
        return 0
    if out:
        upsert_replace("derived_city_day_features", out, "city_key,obs_date")
    log_run("backfill_wind_direction", "ok", len(out),
            {"cities": cities_filled, "thin": thin, "not_cached": not_cached,
             "still_null": still_null, "archive_rows": rows_read})
    print(f"\nwrote {len(out)} row(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
