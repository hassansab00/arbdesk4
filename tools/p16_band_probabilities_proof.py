#!/usr/bin/env python3
"""Does every reader of band_probabilities return the same rows once the
prices no reader selects are gone? (plan v2 P1.6 phase 2, step 6; 29 Sep)

prune_band_probabilities deletes what v_prunable_band_probabilities offers for
markets dated before its cutoff. This proves the claim that makes that safe:
the views that read the table return exactly what they return today. It cannot
reach the database itself (no key in the sandbox), so it writes SQL, one file
per group of views. Each file, run through the Supabase SQL tool as one
statement batch:

  1. opens a REPEATABLE READ snapshot, so the live views and the copy read
     the same rows;
  2. builds a temp table named band_probabilities: the live table minus what
     v_prunable_band_probabilities offers below the cut (--cut-days, default
     7, far harsher than the 30-day keep, which today offers nothing);
  3. rebuilds each view as a temp view from its live definition, which then
     reads the copy (pg_temp is searched first), and checks through pg_depend
     that it does;
  4. returns, per view, the table's rows live and in the copy, the view's
     row count and the md5 of every row sorted, live and copy, and `same`.

Every `same` must be true and every `reads_the_copy` above zero. The list of
views is the table's direct readers (the query in --readers-sql lists them);
the thirteen views above them read the table only through these.

  python tools/p16_band_probabilities_proof.py --sql-dir <dir> [--cut-days 7]
"""
import argparse
import os

# The views that read band_probabilities directly (pg_depend, 29 Sep), one
# group per file so each stays inside the SQL tool's 60 s. v_data_freshness
# is the tenth: it counts the table and takes its newest computed_at, which
# the prune never offers.
GROUPS = [
    ["v_calibration_evidence", "v_probability_reliability", "v_latest_prob"],
    ["v_hit_ladders"],
    ["v_trajectory_evidence"],
    ["v_city_hit_history_live", "v_prediction_ladder_bands"],
    ["v_city_day_readiness", "v_city_day_execution_readiness"],
]

READERS_SQL = """select c.relname, c.relkind
  from pg_depend d join pg_rewrite r on r.oid = d.objid join pg_class c on c.oid = r.ev_class
 where d.refobjid = 'public.band_probabilities'::regclass and c.oid <> 'public.band_probabilities'::regclass
 group by 1, 2 order by 1;
"""


def proof_sql(views, cut_days):
    names = ", ".join(f"'{v}'" for v in views)
    rows = "\n  union all\n".join(
        f"""  select '{v}' as v,
         (select count(*) from public.{v}) as live_n,
         (select count(*) from pg_temp.{v}) as copy_n,
         (select md5(string_agg(t::text, E'\\n' order by t::text)) from public.{v} t)
           = (select md5(string_agg(t::text, E'\\n' order by t::text)) from pg_temp.{v} t) as same,
         (select count(*) from pg_depend d join pg_rewrite r on r.oid = d.objid
           where r.ev_class = 'pg_temp.{v}'::regclass
             and d.refobjid = 'pg_temp.band_probabilities'::regclass) as reads_the_copy"""
        for v in views)
    return f"""set transaction isolation level repeatable read;
create temp table _defs on commit drop as
select v, pg_get_viewdef(('public.' || v)::regclass, true) as def from unnest(array[{names}]) v;
create temp table band_probabilities on commit drop as
select p.* from public.band_probabilities p
 where p.prob_id not in (select g.prob_id from public.v_prunable_band_probabilities g
                          where g.resolution_date < current_date - {int(cut_days)});
create index on band_probabilities (band_id, computed_at);
analyze band_probabilities;
do $$ declare r record; begin
  for r in select v, def from _defs loop execute format('create temp view %I as %s', r.v, r.def); end loop; end $$;
select (select count(*) from public.band_probabilities) as live_rows,
       (select count(*) from pg_temp.band_probabilities) as copy_rows, x.*
  from (
{rows}
) x;
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--sql-dir", required=True)
    ap.add_argument("--cut-days", type=int, default=7)
    args = ap.parse_args()
    os.makedirs(args.sql_dir, exist_ok=True)
    with open(os.path.join(args.sql_dir, "0_readers.sql"), "w", encoding="utf-8") as fh:
        fh.write(READERS_SQL)
    for i, views in enumerate(GROUPS, 1):
        path = os.path.join(args.sql_dir, f"{i}_{views[0]}.sql")
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(proof_sql(views, args.cut_days))
        print(path)


if __name__ == "__main__":
    main()
