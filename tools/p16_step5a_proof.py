#!/usr/bin/env python3
"""Do the station's days and the forecast standing at each lead survive the
weather tables being cut? (plan v2 P1.6 phase 2, step 5 part (a); 29 Sep)

20260929180000 rebuilds v_station_day_max on derived_station_day_sources and
both convergence views on v_forecast_latest (derived_forecast_latest before
the forecast table's oldest day). This proves, against the live database,
that the new definitions return exactly what the live views return - with
the caches empty, with them filled as the nightly refresh fills them, and
with the weather tables cut at --cut-days. It cannot reach the database (no
key in the sandbox), so it writes SQL, one file per check, each for the
Supabase SQL tool as one batch (under its 60 s):

  1_station.sql      BEFORE the migration. One REPEATABLE READ snapshot: the
                     live v_station_day_max and v_settlement_agreement; the
                     new v_station_day_max (sql/ad4_82) as a temp view over a
                     temp derived_station_day_sources, empty, then filled as
                     refresh_city_day_hours fills it, then over a copy of
                     weather_observations cut at --cut-days; the live
                     v_settlement_agreement rebuilt over each.
  2_convergence.sql  BEFORE the migration, the same for v_forecast_latest
                     (sql/ad4_31), v_forecast_convergence (the migration's
                     body) and v_forecast_convergence_all_live (sql/ad4_62),
                     with weather_forecasts cut by date.
  3_cut_after.sql    AFTER the migration and a refresh (select
                     refresh_city_day_hours(); select freeze_forecast_latest();):
                     the live views rebuilt from their own definitions over
                     cut copies of both tables, against the live views.
  4_reads.sql        AFTER: which relations each rebuilt view reads (a bare
                     name is the temp copy), so 3 is known to read the cut.

Every "_not_" count must be 0. Measured results go to docs/PLAN_PROGRESS.md.

  python tools/p16_step5a_proof.py --sql-dir <dir> [--cut-days 30]
"""
import argparse
import os
import re

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _between(src, opener, closer):
    i = src.index(opener)
    j = src.index(closer, i + len(opener))
    return src[i:j + len(closer)]


def station_view():
    body = _between(_src("sql/ad4_82_settlement_agreement.sql"),
                    "create or replace view v_station_day_max as", "group by 1, 2;")
    return body.replace("create or replace view v_station_day_max as", "create temp view v_station_day_max as", 1)


def latest_view():
    body = _between(_src("sql/ad4_31_predictive.sql"), "create or replace view v_forecast_latest as",
                    "'infinity'::date);")
    return body.replace("create or replace view v_forecast_latest as", "create temp view v_forecast_latest as", 1)


def convergence_view():
    mig = _src("supabase/migrations/20260929180000_the_station_days_and_forecast_leads_outlast_the_keep.sql")
    body = _between(mig, "create or replace view public.v_forecast_convergence\nwith (security_invoker = true)\nas",
                    "left join observed o on o.city_key = l.city_key and o.for_date = l.for_date;")
    body = body.replace("create or replace view public.v_forecast_convergence\nwith (security_invoker = true)\nas",
                        "create temp view v_forecast_convergence as", 1)
    return body.replace("public.v_forecast_latest", "v_forecast_latest")


def convergence_all_view():
    body = _between(_src("sql/ad4_62_settled_history_ungated.sql"), "create view v_forecast_convergence_all as",
                    "on o.city_key = l.city_key and o.for_date = l.for_date;")
    return body.replace("create view v_forecast_convergence_all as",
                        "create temp view v_forecast_convergence_all_live as", 1)


STATION_TABLE = """create temp table derived_station_day_sources (
  city_key text not null, obs_date date not null, source text not null, max_c numeric, max_f numeric,
  n_readings bigint not null, last_reading_at timestamptz not null, station text, computed_at timestamptz,
  primary key (city_key, obs_date, source)) on commit drop;"""

# refresh_city_day_hours' insert (sql/ad4_97), every city at once, over the
# live table: at the current snapshot every day is cached as it stands.
STATION_FILL = """insert into pg_temp.derived_station_day_sources
select o.city_key, (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date, coalesce(o.source, ''),
       max(o.temp_c), max(o.temp_f), count(*), max(o.valid_at), min(o.station), now()
  from public.weather_observations o join public.cities c on c.city_key = o.city_key
 where o.temp_c is not null
 group by 1, 2, 3;"""

LATEST_TABLE = """create temp table derived_forecast_latest (
  city_key text not null, for_date date not null, model text not null, lead_days int not null,
  forecast_max_c numeric not null, run_at timestamptz, frozen_at timestamptz,
  primary key (city_key, for_date, model, lead_days)) on commit drop;"""

# freeze_forecast_latest's insert (sql/ad4_97), over the live table.
LATEST_FILL = """insert into pg_temp.derived_forecast_latest
select distinct on (city_key, for_date, model, lead_days)
       city_key, for_date, model, lead_days, forecast_max_c, run_at, now()
  from public.weather_forecasts
 where forecast_max_c is not null and for_date < current_date
 order by city_key, for_date, model, lead_days, run_at desc;"""


def _diff(a, b, label):
    return (f"(select count(*) from (select * from {a} except all select * from {b}) x) as {label}_new_not_live,\n"
            f"       (select count(*) from (select * from {b} except all select * from {a}) x) as {label}_live_not_new")


def _reads(view):
    return (f"(select string_agg(distinct d.refobjid::regclass::text, ', ') from pg_depend d "
            f"join pg_rewrite w on w.oid = d.objid where w.ev_class = 'pg_temp.{view}'::regclass "
            f"and d.refclassid = 'pg_class'::regclass and d.refobjid <> w.ev_class) as {view}_reads")


def station_sql(cut):
    # A view is bound to the tables it names when it is created, so the new
    # view is built twice: over the live readings (empty, then filled cache),
    # and again once the cut copy exists (a bare name in *_reads is the copy).
    return f"""set transaction isolation level repeatable read;
create temp table _defs on commit drop as
select 'v_settlement_agreement'::text as v, pg_get_viewdef('public.v_settlement_agreement'::regclass, true) as def;
create temp table live_sdm on commit drop as select * from public.v_station_day_max;
create temp table live_sa on commit drop as select * from public.v_settlement_agreement;
{STATION_TABLE}
{station_view()}
create temp table new_empty on commit drop as select * from pg_temp.v_station_day_max;
{STATION_FILL}
create temp table new_filled on commit drop as select * from pg_temp.v_station_day_max;
drop view pg_temp.v_station_day_max;
create temp table weather_observations on commit drop as
select * from public.weather_observations where valid_at >= date_trunc('hour', now()) - interval '{cut} days';
create index on weather_observations (valid_at);
analyze weather_observations;
{station_view()}
create temp table new_cut on commit drop as select * from pg_temp.v_station_day_max;
do $$ declare r record; begin
  for r in select v, def from _defs loop execute format('create temp view %I as %s', r.v, r.def); end loop; end $$;
create temp table new_sa_cut on commit drop as select * from pg_temp.v_settlement_agreement;
select (select count(*) from live_sdm) as live_n, (select count(*) from new_cut) as cut_n,
       (select count(*) from public.weather_observations) as readings_live,
       (select count(*) from pg_temp.weather_observations) as readings_after_cut,
       (select min(valid_at) from pg_temp.weather_observations) as cut_at,
       (select count(*) from pg_temp.derived_station_day_sources) as cached_source_days,
       (select count(*) from live_sdm s where not exists (
          select 1 from pg_temp.weather_observations o join public.cities c using (city_key)
           where o.city_key = s.city_key and (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date = s.for_date))
         as days_only_the_cache_serves,
       {_reads('v_station_day_max')},
       {_reads('v_settlement_agreement')},
       {_diff('new_empty', 'live_sdm', 'empty')},
       {_diff('new_filled', 'live_sdm', 'filled')},
       {_diff('new_cut', 'live_sdm', 'cut')},
       {_diff('new_sa_cut', 'live_sa', 'agreement_cut')};
"""


def convergence_sql(cut):
    views = f"{latest_view()}\n{convergence_view()}\n{convergence_all_view()}"
    return f"""set transaction isolation level repeatable read;
create temp table live_conv on commit drop as select * from public.v_forecast_convergence;
create temp table live_all on commit drop as select * from public.v_forecast_convergence_all_live;
{LATEST_TABLE}
{views}
create temp table new_conv_empty on commit drop as select * from pg_temp.v_forecast_convergence;
create temp table new_all_empty on commit drop as select * from pg_temp.v_forecast_convergence_all_live;
{LATEST_FILL}
drop view pg_temp.v_forecast_convergence_all_live;
drop view pg_temp.v_forecast_convergence;
drop view pg_temp.v_forecast_latest;
create temp table weather_forecasts on commit drop as
select * from public.weather_forecasts where for_date >= current_date - {cut};
create index on weather_forecasts (for_date, city_key);
analyze weather_forecasts;
{views}
create temp table new_conv_cut on commit drop as select * from pg_temp.v_forecast_convergence;
create temp table new_all_cut on commit drop as select * from pg_temp.v_forecast_convergence_all_live;
select (select count(*) from live_conv) as conv_n, (select count(*) from live_all) as all_n,
       (select count(*) from pg_temp.derived_forecast_latest) as frozen_rows,
       (select count(*) from live_conv where for_date < current_date - {cut}) as conv_rows_only_frozen_serve,
       {_reads('v_forecast_latest')},
       {_reads('v_forecast_convergence')},
       {_diff('new_conv_empty', 'live_conv', 'conv_empty')},
       {_diff('new_all_empty', 'live_all', 'all_empty')},
       {_diff('new_conv_cut', 'live_conv', 'conv_cut')},
       {_diff('new_all_cut', 'live_all', 'all_cut')};
"""


AFTER_VIEWS = ["v_station_day_max", "v_settlement_agreement", "v_forecast_latest",
               "v_forecast_convergence", "v_forecast_convergence_all_live"]


def cut_after_sql(cut):
    names = ", ".join(f"'{v}'" for v in AFTER_VIEWS)
    return f"""set transaction isolation level repeatable read;
create temp table _defs on commit drop as
select o, v, pg_get_viewdef(('public.' || v)::regclass, true) as def
  from unnest(array[{names}]) with ordinality as t(v, o);
create temp table live_sdm on commit drop as select * from public.v_station_day_max;
create temp table live_sa on commit drop as select * from public.v_settlement_agreement;
create temp table live_conv on commit drop as select * from public.v_forecast_convergence;
create temp table live_all on commit drop as select * from public.v_forecast_convergence_all_live;
create temp table weather_observations on commit drop as
select * from public.weather_observations where valid_at >= date_trunc('hour', now()) - interval '{cut} days';
create temp table weather_forecasts on commit drop as
select * from public.weather_forecasts where for_date >= current_date - {cut};
create index on weather_observations (valid_at);
create index on weather_forecasts (for_date, city_key);
analyze weather_observations; analyze weather_forecasts;
do $$ declare r record; begin
  for r in select v, def from _defs order by o loop execute format('create temp view %I as %s', r.v, r.def); end loop; end $$;
create temp table cut_sdm on commit drop as select * from pg_temp.v_station_day_max;
create temp table cut_sa on commit drop as select * from pg_temp.v_settlement_agreement;
create temp table cut_conv on commit drop as select * from pg_temp.v_forecast_convergence;
create temp table cut_all on commit drop as select * from pg_temp.v_forecast_convergence_all_live;
select (select count(*) from live_sdm) as sdm_n, (select count(*) from live_conv) as conv_n,
       (select count(*) from live_all) as all_n,
       {_diff('cut_sdm', 'live_sdm', 'sdm')},
       {_diff('cut_sa', 'live_sa', 'agreement')},
       {_diff('cut_conv', 'live_conv', 'conv')},
       {_diff('cut_all', 'live_all', 'all')};
"""


def reads_sql():
    names = ", ".join(f"'{v}'" for v in AFTER_VIEWS)
    return f"""create temp table _defs as
select o, v, pg_get_viewdef(('public.' || v)::regclass, true) as def
  from unnest(array[{names}]) with ordinality as t(v, o);
create temp table weather_observations as select * from public.weather_observations limit 0;
create temp table weather_forecasts as select * from public.weather_forecasts limit 0;
do $$ declare r record; begin
  for r in select v, def from _defs order by o loop execute format('create temp view %I as %s', r.v, r.def); end loop; end $$;
select w.ev_class::regclass::text as view, string_agg(distinct d.refobjid::regclass::text, ', ') as reads
  from pg_depend d join pg_rewrite w on w.oid = d.objid
 where w.ev_class::regclass::text in ({names})
   and d.refobjid <> w.ev_class and d.classid = 'pg_rewrite'::regclass and d.refclassid = 'pg_class'::regclass
 group by 1;
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sql-dir", required=True)
    ap.add_argument("--cut-days", type=int, default=30)
    args = ap.parse_args()
    cut = int(args.cut_days)
    os.makedirs(args.sql_dir, exist_ok=True)
    for name, sql in (("1_station", station_sql(cut)), ("2_convergence", convergence_sql(cut)),
                      ("3_cut_after", cut_after_sql(cut)), ("4_reads", reads_sql())):
        path = os.path.join(args.sql_dir, f"{name}.sql")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(sql)
        print(path)


if __name__ == "__main__":
    main()
