#!/usr/bin/env python3
"""Do the market prices the record reads survive edges keeping two days?
(plan v2 P1.6 phase 3, step 3.1; 29 Sep)

20260929220000 copies each band's YES edge at its eve and pre-day cutoffs
into derived_edge_marks, makes v_hit_ladders and v_city_hit_history_live take
the frozen price first, and makes v_prunable_edge_history hold back every mark
not copied yet. This proves, against the live database, that the new
definitions return exactly what the live views return - with the marks table
empty, filled as freeze_edge_marks fills it, and with edges cut at --cut-days
by the new prunable view. It cannot reach the database (no key in the
sandbox), so it writes SQL for the Supabase SQL tool, one batch per file
(each under its 60 s):

  1_ladders.sql  BEFORE the migration. One REPEATABLE READ snapshot: the live
                 v_hit_ladders; the new one (sql/ad4_88) as a temp view over a
                 temp derived_edge_marks (sql/ad4_80), empty, then filled by
                 freeze_edge_marks' own query (sql/ad4_97), then over a copy
                 of edges cut by the new v_prunable_edge_history (sql/ad4_80).
  2_history.sql  BEFORE, the same for v_city_hit_history_live (sql/ad4_85's
                 v_city_hit_history, which sql/ad4_89 serves as _live).
  3_after.sql    AFTER the migration and `select freeze_edge_marks()`: both
                 live views rebuilt from their own definitions over edges cut
                 by the live prunable view, against the live views.

Every "_not_" count must be 0. Measured results go to docs/PLAN_PROGRESS.md.

  python tools/p16_step31_proof.py --sql-dir <dir> [--cut-days 2]
"""
import argparse
import os

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _src(rel):
    with open(os.path.join(ROOT, rel), encoding="utf-8") as fh:
        return fh.read()


def _between(src, opener, closer):
    i = src.index(opener)
    j = src.index(closer, i + len(opener))
    return src[i:j + len(closer)]


def _temp(sql):
    """A definition that reads whatever pg_temp holds first: edges and
    derived_edge_marks are named bare, so a temp copy shadows the table."""
    return (sql.replace("public.derived_edge_marks", "derived_edge_marks")
               .replace("public.edges", "edges"))


def marks_table():
    return _between(_src("sql/ad4_80_prune_edge_history.sql"),
                    "create table if not exists public.derived_edge_marks", "primary key (band_id, mark)\n);") \
        .replace("create table if not exists public.derived_edge_marks", "create temp table derived_edge_marks", 1) \
        .replace("primary key (band_id, mark)\n);", "primary key (band_id, mark)\n) on commit drop;", 1)


def marks_view():
    body = _between(_src("sql/ad4_80_prune_edge_history.sql"),
                    "create or replace view public.v_edge_marks_live as", "limit 1) x;")
    return _temp(body.replace("create or replace view public.v_edge_marks_live as",
                              "create temp view v_edge_marks_live as", 1))


def prunable_view():
    body = _between(_src("sql/ad4_80_prune_edge_history.sql"),
                    "create or replace view v_prunable_edge_history as",
                    "and u.edge_id is null;")
    body = body.replace("create or replace view v_prunable_edge_history as",
                        "create temp view v_prunable_edge_history as", 1)
    return _temp(body).replace("public.v_edge_marks_live", "v_edge_marks_live")


def freeze_insert():
    body = _between(_src("sql/ad4_97_evidence_cache.sql"),
                    "insert into derived_edge_marks", "on conflict (band_id, mark) do nothing;")
    return body.replace("insert into derived_edge_marks", "insert into pg_temp.derived_edge_marks", 1) \
               .replace("from v_edge_marks_live m", "from pg_temp.v_edge_marks_live m", 1) \
               .replace("select 1 from derived_edge_marks d", "select 1 from pg_temp.derived_edge_marks d", 1)


def ladders_view():
    body = _between(_src("sql/ad4_88_hit_tournament.sql"),
                    "create or replace view public.v_hit_ladders as", "and k.cutoff_at = d.cutoff_at;")
    return _temp(body.replace("create or replace view public.v_hit_ladders as",
                              "create temp view v_hit_ladders as", 1))


def history_view():
    body = _between(_src("sql/ad4_85_city_hit_history.sql"),
                    "create view v_city_hit_history as", "order by m.for_date desc, m.city_key;")
    return body.replace("create view v_city_hit_history as", "create temp view v_city_hit_history_live as", 1)


def _cut(cut_days):
    return f"""create temp table edges on commit drop as
select e.* from public.edges e
 where not (e.computed_at < now() - interval '{cut_days} days'
            and exists (select 1 from pg_temp.v_prunable_edge_history p where p.edge_id = e.edge_id));
create index on edges (band_id, side, computed_at desc);
analyze edges;"""


def _compare(live, stage):
    return f"""(select count(*) from (select * from {stage} except all select * from {live}) x) as {stage}_new_not_live,
       (select count(*) from (select * from {live} except all select * from {stage}) x) as {stage}_live_not_new"""


def before(name, view_sql, view_name, cut_days):
    live = f"live_{name}"
    return f"""set transaction isolation level repeatable read;
create temp table {live} on commit drop as select * from public.{view_name};
{marks_table()}
{marks_view()};
{view_sql};
create temp table {name}_empty on commit drop as select * from pg_temp.{view_name};
{freeze_insert()}
create temp table {name}_filled on commit drop as select * from pg_temp.{view_name};
{prunable_view()};
{_cut(cut_days)}
drop view pg_temp.{view_name};
{view_sql};
create temp table {name}_cut on commit drop as select * from pg_temp.{view_name};
select (select count(*) from {live}) as live_rows,
       (select count(*) from pg_temp.derived_edge_marks) as marks_frozen,
       (select count(*) from pg_temp.derived_edge_marks where market_price is not null) as marks_priced,
       (select count(*) from public.edges) as edges_live,
       (select count(*) from pg_temp.edges) as edges_after_cut,
       (select count(*) from public.v_prunable_edge_history where computed_at < now() - interval '{cut_days} days') as old_view_offers,
       (select count(*) from pg_temp.v_prunable_edge_history where computed_at < now() - interval '{cut_days} days') as new_view_offers,
       (select count(*) from pg_temp.v_edge_marks_live m where not exists (
          select 1 from pg_temp.derived_edge_marks d where d.band_id = m.band_id and d.mark = m.mark and d.cutoff_at = m.cutoff_at)) as marks_not_frozen,
       (select string_agg(distinct d.refobjid::regclass::text, ', ') from pg_depend d join pg_rewrite w on w.oid = d.objid
         where w.ev_class = 'pg_temp.{view_name}'::regclass and d.refclassid = 'pg_class'::regclass and d.refobjid <> w.ev_class) as cut_view_reads,
       {_compare(live, f"{name}_empty")},
       {_compare(live, f"{name}_filled")},
       {_compare(live, f"{name}_cut")};
"""


def after(cut_days):
    return f"""set transaction isolation level repeatable read;
create temp table _defs on commit drop as
select v, pg_get_viewdef(('public.' || v)::regclass, true) as def
  from unnest(array['v_hit_ladders', 'v_city_hit_history_live']) v;
create temp table live_hl on commit drop as select * from public.v_hit_ladders;
create temp table live_hh on commit drop as select * from public.v_city_hit_history_live;
create temp table edges on commit drop as
select e.* from public.edges e
 where not (e.computed_at < now() - interval '{cut_days} days'
            and exists (select 1 from public.v_prunable_edge_history p where p.edge_id = e.edge_id));
create index on edges (band_id, side, computed_at desc);
analyze edges;
do $$ declare r record; begin
  for r in select v, def from _defs loop execute format('create temp view %I as %s', r.v, r.def); end loop; end $$;
create temp table cut_hl on commit drop as select * from pg_temp.v_hit_ladders;
create temp table cut_hh on commit drop as select * from pg_temp.v_city_hit_history_live;
select (select count(*) from public.derived_edge_marks) as marks_frozen,
       (select count(*) from public.edges) as edges_live,
       (select count(*) from pg_temp.edges) as edges_after_cut,
       (select count(*) from public.derived_edge_marks k
         where not exists (select 1 from pg_temp.edges e where e.edge_id = k.edge_id)) as marks_only_the_table_serves,
       (select string_agg(distinct d.refobjid::regclass::text, ', ') from pg_depend d join pg_rewrite w on w.oid = d.objid
         where w.ev_class in ('pg_temp.v_hit_ladders'::regclass, 'pg_temp.v_city_hit_history_live'::regclass)
           and d.refclassid = 'pg_class'::regclass and d.refobjid <> w.ev_class) as cut_views_read,
       {_compare("live_hl", "cut_hl")},
       {_compare("live_hh", "cut_hh")};
"""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--sql-dir", required=True)
    ap.add_argument("--cut-days", type=int, default=2)
    a = ap.parse_args()
    os.makedirs(a.sql_dir, exist_ok=True)
    files = {
        "1_ladders.sql": before("hl", ladders_view(), "v_hit_ladders", a.cut_days),
        "2_history.sql": before("hh", history_view(), "v_city_hit_history_live", a.cut_days),
        "3_after.sql": after(a.cut_days),
    }
    for name, sql in files.items():
        with open(os.path.join(a.sql_dir, name), "w", encoding="utf-8") as fh:
            fh.write(sql)
        print(os.path.join(a.sql_dir, name), len(sql))


if __name__ == "__main__":
    main()
