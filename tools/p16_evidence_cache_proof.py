#!/usr/bin/env python3
"""Do the trajectory evidence and the hit tournament's forecasts survive the
weather tables being cut? (plan v2 P1.6 phase 2, step 6 part (b); 29 Sep)

v_trajectory_evidence reads weather_observations for whole days and
derived_city_day_hours before them; v_hit_forecasts reads the forecast tables
for the days both hold and derived_hit_forecasts before. This proves the claim
that makes step 5 safe: cut the weather tables at --cut-days and both views
return exactly what they return today. It cannot reach the database (no key in
the sandbox), so it writes SQL, one file per view. Each, run through the
Supabase SQL tool as one batch (under its 60 s):

  1. opens a REPEATABLE READ snapshot and materializes the live view;
  2. builds temp tables named after the weather tables, holding only what a
     prune at --cut-days would leave (observations from the hour --cut-days
     back, forecasts from that date), indexed as the live ones are;
  3. rebuilds the view (and, for the forecasts, v_forecast_issued and
     v_hit_forecasts_live under it) as temp views from the live definitions,
     which then read the cut copies (pg_temp is searched first);
  4. returns both counts, the rows in each and not the other (EXCEPT ALL),
     and how many rows only the caches can serve at that cut.

Both differences must be 0. Run it after the nightly refresh (or after
`select refresh_city_day_hours(); select freeze_hit_forecasts();`). Measured
29 Sep at a 30-day and a 13-day cut: 0 and 0 each (docs/PLAN_PROGRESS.md).

  python tools/p16_evidence_cache_proof.py --sql-dir <dir> [--cut-days 30]
"""
import argparse
import os

TRAJECTORY = """set transaction isolation level repeatable read;
create temp table _defs on commit drop as
select v, pg_get_viewdef(('public.' || v)::regclass, true) as def from unnest(array['v_trajectory_evidence']) v;
create temp table real_traj as select * from public.v_trajectory_evidence;
create temp table weather_observations as
select * from public.weather_observations where valid_at >= date_trunc('hour', now()) - interval '{cut} days';
analyze weather_observations;
do $$ declare r record; begin
  for r in select v, def from _defs loop execute format('create temp view %I as %s', r.v, r.def); end loop; end $$;
create temp table cut_traj as select * from pg_temp.v_trajectory_evidence;
select (select count(*) from pg_temp.weather_observations) as readings_after_cut,
       (select min(valid_at) from pg_temp.weather_observations) as cut_at,
       (select count(*) from real_traj) as real_n, (select count(*) from cut_traj) as cut_n,
       (select count(*) from (select * from cut_traj except all select * from real_traj) x) as cut_not_real,
       (select count(*) from (select * from real_traj except all select * from cut_traj) x) as real_not_cut,
       (select count(*) from real_traj
         where local_date <= (select min(valid_at)::date from pg_temp.weather_observations)) as rows_on_cut_or_older_days;
"""

HITS = """set transaction isolation level repeatable read;
create temp table _defs on commit drop as
select o, v, pg_get_viewdef(('public.' || v)::regclass, true) as def
  from unnest(array['v_forecast_issued', 'v_hit_forecasts_live', 'v_hit_forecasts']) with ordinality as t(v, o);
create temp table real_hits as select * from public.v_hit_forecasts;
create temp table weather_forecasts as select * from public.weather_forecasts where for_date >= current_date - {cut};
create temp table weather_forecast_models as select * from public.weather_forecast_models where for_date >= current_date - {cut};
create index on weather_forecasts (for_date, city_key);
create index on weather_forecast_models (city_key, for_date);
analyze weather_forecasts; analyze weather_forecast_models;
do $$ declare r record; begin
  for r in select v, def from _defs order by o loop execute format('create temp view %I as %s', r.v, r.def); end loop; end $$;
create temp table cut_hits as select * from pg_temp.v_hit_forecasts;
select (select count(*) from real_hits) as real_n, (select count(*) from cut_hits) as cut_n,
       (select count(*) from (select * from cut_hits except all select * from real_hits) x) as cut_not_real,
       (select count(*) from (select * from real_hits except all select * from cut_hits) x) as real_not_cut,
       (select count(*) from real_hits where for_date < current_date - {cut}) as rows_only_frozen_serves;
"""

# Which relations each rebuilt view reads. A name that prints bare is the temp
# copy (pg_temp is searched first); the public table it shadows would print
# schema-qualified. Empty copies, so it is instant.
READS = """create temp table _defs as
select o, v, pg_get_viewdef(('public.' || v)::regclass, true) as def
  from unnest(array['v_forecast_issued', 'v_hit_forecasts_live', 'v_hit_forecasts', 'v_trajectory_evidence'])
       with ordinality as t(v, o);
create temp table weather_forecasts as select * from public.weather_forecasts limit 0;
create temp table weather_forecast_models as select * from public.weather_forecast_models limit 0;
create temp table weather_observations as select * from public.weather_observations limit 0;
do $$ declare r record; begin
  for r in select v, def from _defs order by o loop execute format('create temp view %I as %s', r.v, r.def); end loop; end $$;
select w.ev_class::regclass::text as view, string_agg(distinct d.refobjid::regclass::text, ', ') as reads
  from pg_depend d join pg_rewrite w on w.oid = d.objid
 where w.ev_class::regclass::text in ('v_forecast_issued', 'v_hit_forecasts_live', 'v_hit_forecasts', 'v_trajectory_evidence')
   and w.ev_class::regclass::text not like 'public.%'
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
    for name, sql in (("1_trajectory", TRAJECTORY.format(cut=cut)), ("2_hit_forecasts", HITS.format(cut=cut)),
                      ("3_reads", READS)):
        path = os.path.join(args.sql_dir, f"{name}.sql")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(sql)
        print(path)


if __name__ == "__main__":
    main()
