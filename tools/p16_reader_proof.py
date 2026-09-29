#!/usr/bin/env python3
"""Does scripts/weather_history.py return what the database would?
(plan v2 P1.6 phase 2, step 2; 29 Sep)

The reader answers a job's forecast read from the database plus the archive
for the dates the database was cut below. This proves each part against the
live database. It cannot reach the database itself (no key in the sandbox),
so it writes SQL: each query carries the fingerprints computed here and
returns only what differs. Run each through the Supabase SQL tool; an empty
result is the proof.

  1. The files hold the database's rows. Per for_date, a count and an md5 of
     the columns the jobs read, from the mirror (data/mirror/weather_forecasts,
     the newest copy of each key), against the table.
  2. The archive and the mirror agree on the days both hold (25-30 Jul),
     the archive being what the reader reads below the cut.
  3. The jobs' own reads, as they will be at a 30-day keep: the reader with
     the database cut at --cut and the mirror standing in for the archive
     below it, against the same read made on the whole table - skill's
     v_forecast_issued read and regime's history read, per city, in the
     order the job asks for.
  4. v_forecast_issued's five columns, computed by with_issued from the
     files, against the view, per for_date.
  5. text_key orders city keys, models and sources as the database does.

  python tools/p16_reader_proof.py --sql-dir <dir> [--cut 2026-08-30] [--today 2026-09-29]
"""
import argparse
import csv
import datetime as dt
import glob
import gzip
import hashlib
import json
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "scripts"))
import weather_history as wh  # noqa: E402

EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc)


def micros(ts):
    return (wh._ts(ts) - EPOCH) // dt.timedelta(microseconds=1)


def num(v):
    """numeric as trim_scale(round(x, 6))::text writes it."""
    if v is None:
        return "null"
    s = f"{float(v):.6f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def mirror_rows(table):
    """The newest copy of every key the mirror holds (a row re-ingested later
    is copied again with its new observed_at)."""
    out = {}
    for path in sorted(glob.glob(os.path.join(ROOT, "data", "mirror", table, "*.csv.gz"))):
        with gzip.open(path, "rt", newline="") as fh:
            for r in csv.DictReader(fh):
                row = {k: wh._typed(k, v) for k, v in r.items()}
                k = (row["city_key"], row["model"], micros(row["run_at"]), row["for_date"])
                if k not in out or micros(row["observed_at"]) >= micros(out[k]["observed_at"]):
                    out[k] = row
    return list(out.values())


def day_line(r):
    return "|".join([r["city_key"], r["model"], str(micros(r["run_at"])), r["for_date"],
                     "null" if r["lead_days"] is None else str(r["lead_days"]), num(r["forecast_max_c"]),
                     r["source"]])


def day_oline(r):
    return "|".join([r["city_key"], r["model"], str(micros(r["run_at"])), r["for_date"],
                     str(micros(r["observed_at"]))])


def md5(lines):
    return hashlib.md5("\n".join(lines).encode()).hexdigest()


def per_day(rows):
    by = {}
    for r in rows:
        by.setdefault(r["for_date"], []).append(r)
    return {d: [len(rs), md5(sorted(day_line(r) for r in rs)), md5(sorted(day_oline(r) for r in rs))]
            for d, rs in sorted(by.items())}


DAY_SQL = """-- 1. the mirror's rows per for_date against {table} ({lo} to {hi}).
-- Rows back: dates where the count or a fingerprint differs. Empty = equal.
with mine as (select key::date as for_date, (value->>0)::int as n, value->>1 as h, value->>2 as oh
                from json_each('{mine}'::json)),
l as (
  select for_date, concat_ws('|', city_key, model, ((extract(epoch from run_at) * 1000000)::bigint)::text, for_date::text,
         coalesce(lead_days::text, 'null'), coalesce(trim_scale(round(forecast_max_c, 6))::text, 'null'), source) as line,
         concat_ws('|', city_key, model, ((extract(epoch from run_at) * 1000000)::bigint)::text, for_date::text,
         ((extract(epoch from observed_at) * 1000000)::bigint)::text) as oline
  from {table} where for_date between '{lo}' and '{hi}'),
db as (select for_date, count(*)::int as n, md5(string_agg(line, E'\\n' order by line collate "C")) as h,
              md5(string_agg(oline, E'\\n' order by oline collate "C")) as oh from l group by for_date)
select coalesce(db.for_date, mine.for_date) as for_date, db.n as db_n, mine.n as files_n,
       db.h = mine.h as same_values, db.oh = mine.oh as same_observed_at
  from db full join mine using (for_date)
 where db.n is distinct from mine.n or db.h is distinct from mine.h or db.oh is distinct from mine.oh
 order by 1;
"""

READ_SQL = """-- 3. {what}: the reader at a {cut} cut against the whole table.
-- Rows back: cities whose read differs (count or md5 of the rows in the job's
-- order). Empty = equal.
with mine as (select key as city_key, (value->>0)::int as n, value->>1 as h from json_each('{mine}'::json)),
db as (select city_key, count(*)::int as n, md5(string_agg({line}, E'\\n' order by {order})) as h
         from {table} where {where} group by city_key)
select coalesce(db.city_key, mine.city_key) as city_key, db.n as db_n, mine.n as reader_n, db.h = mine.h as same
  from db full join mine using (city_key)
 where db.n is distinct from mine.n or db.h is distinct from mine.h
 order by 1;
"""


ISSUED_SQL = """-- 4. v_forecast_issued's five columns, computed by weather_history.with_issued
-- from the mirror's rows, per for_date against the view ({lo} to {hi}).
-- Rows back: dates that differ. Empty = equal.
with mine as (select key::date as for_date, (value->>0)::int as n, value->>1 as h from json_each('{mine}'::json)),
db as (select for_date, count(*)::int as n,
              md5(string_agg(concat_ws('|', city_key, model, ((extract(epoch from run_at) * 1000000)::bigint)::text,
                  coalesce(((extract(epoch from issued_at) * 1000000)::bigint)::text, 'null'), issued_at_source,
                  coalesce(issued_local_date::text, 'null'), coalesce(issued_lead_days::text, 'null'),
                  coalesce(same_day_issue::text, 'null')), E'\\n' order by city_key collate "C", model collate "C", run_at)) as h
         from v_forecast_issued where for_date between '{lo}' and '{hi}' group by for_date)
select coalesce(db.for_date, mine.for_date) as for_date, db.n as db_n, mine.n as files_n, db.h = mine.h as same
  from db full join mine using (for_date)
 where db.n is distinct from mine.n or db.h is distinct from mine.h
 order by 1;
"""

ORDER_SQL = """-- 5. weather_history.text_key sorts city keys, models and sources as the
-- database does (en_US.UTF-8). One row; every column true = equal.
select (select array_agg(city_key order by city_key) from cities) = {cities}::text[] as cities,
       (select array_agg(m order by m) from (select distinct model m from weather_forecasts
                                              union select distinct model from weather_forecast_models) x)
         = {models}::text[] as models,
       (select array_agg(s order by s) from (select distinct source s from weather_forecasts
                                              union select distinct source from weather_forecast_models) x)
         = {sources}::text[] as sources;
"""


def issued_line(r):
    return "|".join([r["city_key"], r["model"], str(micros(r["run_at"])),
                     "null" if r["issued_at"] is None else str(micros(r["issued_at"])), r["issued_at_source"],
                     r["issued_local_date"] or "null",
                     "null" if r["issued_lead_days"] is None else str(r["issued_lead_days"]),
                     json.dumps(r["same_day_issue"])])


def pg_array(values):
    return "array[" + ",".join("'" + v.replace("'", "''") + "'" for v in values) + "]"


def skill_line(r):
    return "|".join([r["for_date"], str(r["lead_days"]), num(r["forecast_max_c"]), r["model"],
                     str(micros(r["run_at"])), json.dumps(r["same_day_issue"])])


SKILL_SQL_LINE = ("concat_ws('|', for_date::text, lead_days::text, coalesce(trim_scale(round(forecast_max_c, 6))::text, 'null'), "
                  "model, ((extract(epoch from run_at) * 1000000)::bigint)::text, coalesce(same_day_issue::text, 'null'))")


def station_line(r):
    return "|".join([r["city_key"], r["model"], r["for_date"], str(r["lead_days"]), num(r["forecast_max_c"])])


STATION_SQL_LINE = ("concat_ws('|', city_key, model, for_date::text, lead_days::text, "
                    "coalesce(trim_scale(round(forecast_max_c, 6))::text, 'null'))")


def regime_line(r):
    return "|".join([r["for_date"], str(r["lead_days"]), num(r["forecast_max_c"]), r["model"]])


REGIME_SQL_LINE = ("concat_ws('|', for_date::text, lead_days::text, "
                   "coalesce(trim_scale(round(forecast_max_c, 6))::text, 'null'), model)")


def simulated(name, params, order, db_rows, archive_rows, cut, zones):
    """weather_history.read with the database cut at `cut`: rows below it come
    only from `archive_rows`, as they will once the archive holds them."""
    wh.reset()
    wh._boundary[wh.SOURCES[name]["dataset"]] = {"before": cut, "file": "p16-proof"}
    wh._zones.update(zones)
    held = [r for r in db_rows if r["for_date"] >= cut]
    base = wh.SOURCES[name]

    def rest_all_fn(path, p, order=None, page_size=None):
        select, filters, _, _ = wh.parse_filters(p)
        rows = [wh.with_issued(dict(r), zones.get(r["city_key"])) if base["issued"] else dict(r) for r in held]
        rows = wh.sort_rows([r for r in rows if wh.matches(r, filters)], order)
        return [{c: r.get(c) for c in select} for r in rows] if select else rows

    real_archived, real_check = wh.archived, wh.check_checkout
    wh.archived = lambda dataset, lo, before, root=wh.ROOT: [
        r for r in archive_rows if (lo is None or r["for_date"] >= lo) and r["for_date"] < before]
    wh.check_checkout = lambda cut, root=wh.ROOT: None
    try:
        return wh.read(name, params, rest_all_fn=rest_all_fn, order=order, page_size=1000)
    finally:
        wh.archived, wh.check_checkout = real_archived, real_check
        wh.reset()


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--sql-dir", required=True)
    ap.add_argument("--cut", default="2026-08-30")
    ap.add_argument("--today", default="2026-09-29")
    ap.add_argument("--station-as-of", default="2026-09-28")
    ap.add_argument("--timezones", required=True, help="JSON {city_key: timezone} from the cities table")
    a = ap.parse_args()
    os.makedirs(a.sql_dir, exist_ok=True)
    zones = json.load(open(a.timezones))
    fc = mirror_rows("weather_forecasts")
    print(f"mirror: {len(fc):,} weather_forecasts keys")

    # 1. files against the table, for every for_date the mirror should hold whole
    lo, hi = "2026-07-31", "2026-09-22"
    days = {d: v for d, v in per_day(fc).items() if lo <= d <= hi}
    open(os.path.join(a.sql_dir, "1_days.sql"), "w").write(
        DAY_SQL.format(table="weather_forecasts", lo=lo, hi=hi, mine=json.dumps(days, separators=(",", ":"))))
    # ...and weather_forecast_models, whose mirror began on 24 Sep: a date is
    # whole in it only if every row for it was written, or rewritten, since.
    fm = mirror_rows("weather_forecast_models")
    mdays = per_day(fm)
    open(os.path.join(a.sql_dir, "1b_model_days.sql"), "w").write(
        DAY_SQL.format(table="weather_forecast_models", lo=min(mdays), hi=max(mdays),
                       mine=json.dumps(mdays, separators=(",", ":"))))
    print(f"1. {len(days)} for_dates of weather_forecasts ({sum(v[0] for v in days.values()):,} rows); "
          f"weather_forecast_models: {len(mdays)} for_dates ({len(fm):,} keys) in the mirror")

    # 4. v_forecast_issued's columns for those rows
    issued = {}
    for r in fc:
        if lo <= r["for_date"] <= hi:
            issued.setdefault(r["for_date"], []).append(wh.with_issued(r, zones.get(r["city_key"])))
    ih = {}
    for d, rs in sorted(issued.items()):
        rs.sort(key=lambda r: (r["city_key"], r["model"], micros(r["run_at"])))
        ih[d] = [len(rs), md5([issued_line(r) for r in rs])]
    open(os.path.join(a.sql_dir, "4_issued.sql"), "w").write(
        ISSUED_SQL.format(lo=lo, hi=hi, mine=json.dumps(ih, separators=(",", ":"))))

    # 5. the order of text
    open(os.path.join(a.sql_dir, "5_order.sql"), "w").write(ORDER_SQL.format(
        cities=pg_array(sorted(zones, key=wh.text_key)),
        models=pg_array(sorted({r["model"] for r in fc} | {r["model"] for r in fm}, key=wh.text_key)),
        sources=pg_array(sorted({r["source"] for r in fc} | {r["source"] for r in fm}, key=wh.text_key))))

    # 2. the archive against the mirror on the days both hold
    arch = [r for f, t, p in wh.archive_files("forecasts") for r in wh.file_rows(p)]
    both = sorted({r["for_date"] for r in arch} & {r["for_date"] for r in fc})
    a_days, m_days = per_day([r for r in arch if r["for_date"] in both]), per_day([r for r in fc if r["for_date"] in both])
    print(f"2. archive vs mirror on {both[0]}..{both[-1]} ({len(both)} days):")
    for d in both:
        av, mv = a_days[d], m_days[d]
        print(f"   {d}: archive {av[0]} rows, mirror {mv[0]}; values {'equal' if av[1] == mv[1] else 'DIFFER'}, "
              f"observed_at {'equal' if av[2] == mv[2] else 'differ'}")

    # 3. the jobs' reads at a 30-day keep
    today = dt.date.fromisoformat(a.today)
    cities = sorted({r["city_key"] for r in fc})
    first_outcome = "2026-08-24"
    skill, regime = {}, {}
    for c in cities:
        p = [("select", "for_date,lead_days,forecast_max_c,model,run_at,same_day_issue"), ("city_key", f"eq.{c}"),
             ("for_date", f"gte.{first_outcome}"), ("for_date", f"lte.{today - dt.timedelta(days=8)}")]
        rows = simulated("v_forecast_issued", p, "for_date.asc,lead_days.asc,model.asc,run_at.asc", fc, fc, a.cut, zones)
        if rows:
            skill[c] = [len(rows), md5([skill_line(r) for r in rows])]
        p = [("select", "for_date,lead_days,forecast_max_c,model"), ("city_key", f"eq.{c}"),
             ("for_date", f"gte.{today - dt.timedelta(days=60)}"), ("for_date", f"lte.{today - dt.timedelta(days=8)}")]
        rows = simulated("weather_forecasts", p, "for_date.asc,lead_days.asc,model.asc,run_at.asc", fc, fc, a.cut, zones)
        if rows:
            regime[c] = [len(rows), md5([regime_line(r) for r in rows])]
    end = today - dt.timedelta(days=8)
    open(os.path.join(a.sql_dir, "3_skill.sql"), "w").write(READ_SQL.format(
        what=f"skill's read (v_forecast_issued, {first_outcome} to {end})", cut=a.cut,
        mine=json.dumps(skill, separators=(",", ":")), line=SKILL_SQL_LINE,
        order="for_date, lead_days, model, run_at", table="v_forecast_issued",
        where=f"for_date between '{first_outcome}' and '{end}'"))
    open(os.path.join(a.sql_dir, "3_regime.sql"), "w").write(READ_SQL.format(
        what=f"regime's history (weather_forecasts, {today - dt.timedelta(days=60)} to {end})", cut=a.cut,
        mine=json.dumps(regime, separators=(",", ":")), line=REGIME_SQL_LINE,
        order="for_date, lead_days, model, run_at", table="weather_forecasts",
        where=f"for_date between '{today - dt.timedelta(days=60)}' and '{end}'"))
    # station_correction.load_fit_forecasts, the night of --station-as-of (the
    # mirror holds every model row up to the day before it)
    import station_correction as sc
    s_as_of = dt.date.fromisoformat(a.station_as_of)
    s_cut = (s_as_of - dt.timedelta(days=30)).isoformat()
    captured = {}

    def capture(path, params, order=None, page_size=None):
        captured.update(path=path, params=params, order=order)
        return []
    sc.load_fit_forecasts(capture, s_as_of)
    rows = simulated(captured["path"], captured["params"], captured["order"], fm, fm, s_cut, zones)
    station = {}
    for r in rows:
        station.setdefault(r["city_key"], []).append(station_line(r))
    station = {c: [len(v), md5(v)] for c, v in station.items()}
    _, s_filters, _, _ = wh.parse_filters(captured["params"])
    s_lo = wh.lowest_date(s_filters)
    open(os.path.join(a.sql_dir, "3_station.sql"), "w").write(READ_SQL.format(
        what=f"station_correction.load_fit_forecasts on {s_as_of} ({captured['params']})", cut=s_cut,
        mine=json.dumps(station, separators=(",", ":")), line=STATION_SQL_LINE,
        order="for_date, model, lead_days, run_at", table="weather_forecast_models",
        where=(f"source = '{sc.FIT_SOURCE}' and lead_days <= {max(sc.LEADS)} and for_date >= '{s_lo}' "
               f"and for_date < '{s_as_of}' and forecast_max_c is not null")))
    print(f"   station correction on {s_as_of} (cut {s_cut}): {len(station)} cities, "
          f"{sum(v[0] for v in station.values()):,} rows")
    print(f"3. skill: {len(skill)} cities, {sum(v[0] for v in skill.values()):,} rows; "
          f"regime: {len(regime)} cities, {sum(v[0] for v in regime.values()):,} rows")
    print(f"SQL written to {a.sql_dir}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
