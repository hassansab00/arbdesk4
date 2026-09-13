-- ===========================================================================
-- ad4_50_index_dedupe.sql - THE DESK WAS PAYING TO STORE EVERY INDEX TWICE.
--
-- Safe to run any time. Drops no data and no constraint. Every index it drops
-- has an identical or superset twin that stays, so no query plan changes.
--
--
-- WHAT WAS WRONG - AND IT WAS MINE
--
-- ad4_44_indexes.sql creates its indexes with
--
--     create index if not exists ad4_ix_obs_city_time on weather_observations ...
--
-- and `if not exists` tests THE NAME. This database already carried the same
-- index under the name it was born with - idx_obs_city_time - so the guard
-- passed and Postgres built a second, identical copy. Six times.
--
-- Measured here before the cleanup: 555 MB of database, of which the heap of
-- the three big tables was 156 MB and their indexes were 338 MB.
--
--     weather_observations   heap  60 MB   indexes 123 MB   (6 indexes)
--     trades_observed        heap  53 MB   indexes 121 MB   (7 indexes)
--     weather_forecasts      heap  43 MB   indexes  94 MB   (7 indexes)
--
-- The free tier stops at 500 MB. The desk was over it on duplicate indexes
-- alone, having deleted nothing and collected nothing it did not need.
--
--
-- THE RULES THIS FILE FOLLOWS
--
--   1. Never drop a primary key, or an index backing a constraint. If one
--      of a duplicate pair backs a constraint, the OTHER one goes.
--   2. Of two interchangeable indexes, drop the physically larger. They hold
--      the same entries, so the difference is bloat.
--   3. An index whose columns are a strict PREFIX of a longer index is
--      redundant FOR LOOKUPS - the longer one answers every query the shorter
--      one does. It is NOT redundant if it is UNIQUE. See below.
--   4. DESC vs ASC does not make two indexes different: Postgres reads a
--      btree in either direction.
--   5. A UNIQUE index is never dropped, under any rule. See below.
--   6. Anything not covered by 1-5 is left alone. This file removes
--      duplicates, it does not decide which indexes the desk needs.
--
--
-- RULE 5, AND WHY IT COST SEVEN DAYS OF PEAK-HOUR DATA
--
-- Rule 3 was written about lookup cost and applied to every index, and a
-- UNIQUE index is not only a lookup structure. It is a CONSTRAINT, and it is
-- the only thing that makes `on conflict (those columns)` plannable.
--
-- derived_weather_peak carries a primary key on
--
--     (city_key, month, computed_at)
--
-- and ad4_00_preflight builds a unique index on
--
--     (city_key, month)
--
-- because refresh_weather_peak() upserts with `on conflict (city_key, month)`.
-- By rule 3 the short one is a strict prefix of the long one, so this file
-- dropped it. Both statements are individually true and the conclusion is
-- wrong: the three-column key permits two rows for the same city and month
-- with different timestamps, which is exactly what the two-column key exists
-- to forbid, and a prefix of a key is not a key.
--
-- What that produced was not a slow query. It was
--
--     42P10: there is no unique or exclusion constraint matching the
--     ON CONFLICT specification
--
-- every time refresh_weather_peak() ran - and scripts/capacity.py caught it,
-- printed one line to stderr and reported the job green. derived_weather_peak
-- went unwritten for seven days while live_weather.minutes_to_peak,
-- v_city_stats, v_trade_timing and strategy s7 all quietly read the stale
-- copy. sql/ad4_56 puts the index back; this file is why it had to.
--
-- The same reasoning applies to rule 1's tie-break: given two indexes on
-- IDENTICAL columns, one unique and one not, the unique one is the survivor
-- even when it is the larger. Dropping it would remove a constraint, which
-- this file promises never to do.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- 1. Normalise an index definition down to its key columns, so that
--
--       (city_key, valid_at DESC)
--       (city_key, valid_at)
--
--    compare equal, and INCLUDE columns do not hide a duplicate.
-- --------------------------------------------------------------------------
-- ad4_index_keys() is defined by sql/ad4_44_indexes.sql, which runs before
-- this file and is the file that must stop creating duplicates in the first
-- place. This one removes the ones already built.
do $ad4$ begin
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'public' and p.proname = 'ad4_index_keys') then
    raise exception 'ad4_50 needs ad4_index_keys() - run sql/ad4_44_indexes.sql first';
  end if;
end $ad4$;


-- --------------------------------------------------------------------------
-- 2. Drop the duplicates.
-- --------------------------------------------------------------------------
do $ad4$
declare
  r          record;
  v_dropped  int := 0;
  v_freed    bigint := 0;
  v_before   bigint;
begin
  select pg_database_size(current_database()) into v_before;

  for r in
    with ix as (
      select c.relname                                   as tbl,
             i.relname                                   as idx,
             i.oid                                       as idx_oid,
             x.indisprimary,
             x.indisunique,
             pg_relation_size(i.oid)                     as bytes,
             ad4_index_keys(pg_get_indexdef(i.oid))      as keys,
             exists (select 1 from pg_constraint con where con.conindid = i.oid)
                                                         as is_constraint
        from pg_index x
        join pg_class i     on i.oid = x.indexrelid
        join pg_class c     on c.oid = x.indrelid
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'public'
         and x.indpred is null           -- never touch a partial index
         and x.indisvalid
    ),
    ranked as (
      select ix.*,
             row_number() over (
               partition by tbl, keys
               -- The survivor, in order: a constraint or PK, then anything
               -- UNIQUE, then the smallest. Unique outranks size because the
               -- two are not interchangeable - one of them forbids duplicate
               -- rows and the other only finds them faster.
               order by (indisprimary or is_constraint) desc,
                        indisunique desc,
                        bytes asc, idx
             ) as rn,
             count(*) over (partition by tbl, keys) as copies
        from ix
    )
    select tbl, idx, bytes, keys from ranked
     where copies > 1 and rn > 1
       and not indisprimary and not is_constraint
       and not indisunique          -- rule 5: a unique index is a constraint
     order by bytes desc
  loop
    execute format('drop index if exists public.%I', r.idx);
    raise notice 'ad4_50: dropped duplicate %.% (%) - an identical index remains',
                 r.tbl, r.idx, pg_size_pretty(r.bytes);
    v_dropped := v_dropped + 1;
    v_freed := v_freed + r.bytes;
  end loop;

  -- ---- prefix-redundant: (a,b) when (a,b,c) also exists ------------------
  for r in
    with ix as (
      select c.relname as tbl, i.relname as idx, i.oid as idx_oid,
             x.indisprimary, x.indisunique, pg_relation_size(i.oid) as bytes,
             ad4_index_keys(pg_get_indexdef(i.oid)) as keys,
             exists (select 1 from pg_constraint con where con.conindid = i.oid)
               as is_constraint
        from pg_index x
        join pg_class i     on i.oid = x.indexrelid
        join pg_class c     on c.oid = x.indrelid
        join pg_namespace n on n.oid = c.relnamespace
       where n.nspname = 'public' and x.indpred is null and x.indisvalid
    )
    select s.tbl, s.idx, s.bytes, l.idx as covered_by
      from ix s
      join ix l
        on l.tbl = s.tbl
       and l.idx <> s.idx
       and l.keys like s.keys || ',%'      -- s's keys are a strict prefix
     where not s.indisprimary and not s.is_constraint
       -- RULE 5. A prefix of a longer index answers the same LOOKUPS, and
       -- enforces a STRICTLY STRONGER constraint: unique on (a,b) forbids
       -- rows that unique on (a,b,c) allows. Dropping it silently deletes a
       -- constraint and breaks `on conflict (a,b)`. This one line is the
       -- whole of the derived_weather_peak outage described at the top.
       and not s.indisunique
     order by s.bytes desc
  loop
    execute format('drop index if exists public.%I', r.idx);
    raise notice 'ad4_50: dropped %.% (%) - covered by the longer index %',
                 r.tbl, r.idx, pg_size_pretty(r.bytes), r.covered_by;
    v_dropped := v_dropped + 1;
    v_freed := v_freed + r.bytes;
  end loop;

  raise notice 'ad4_50: % duplicate index(es) dropped, % reclaimed',
               v_dropped, pg_size_pretty(v_freed);
end
$ad4$;


-- --------------------------------------------------------------------------
-- 3. An index nothing has ever read is storage with no return.
--
--    REPORTED, NOT DROPPED. pg_stat_user_indexes counts since the last stats
--    reset, so "0 scans" can mean "young", and a unique index earns its keep
--    by enforcing the constraint whether or not a query ever reads it. This
--    lists candidates for a human to decide on; it removes nothing.
-- --------------------------------------------------------------------------
create or replace view v_index_never_used as
select s.relname                                as table_name,
       s.indexrelname                           as index_name,
       pg_size_pretty(pg_relation_size(s.indexrelid)) as size,
       pg_relation_size(s.indexrelid)           as bytes,
       s.idx_scan                               as times_used,
       pg_get_indexdef(s.indexrelid)            as definition
  from pg_stat_user_indexes s
  join pg_index x on x.indexrelid = s.indexrelid
 where s.schemaname = 'public'
   and s.idx_scan = 0
   and not x.indisunique
   and not x.indisprimary
   and pg_relation_size(s.indexrelid) > 1024 * 1024
 order by pg_relation_size(s.indexrelid) desc;

comment on view v_index_never_used is
  'Non-unique indexes over 1 MB that no query has read since the statistics were last reset. Candidates for dropping, not a verdict: a young index has had no chance to be used yet. Check the count is still zero after a few days of normal traffic before removing one.';

grant select on v_index_never_used to anon, authenticated;


-- --------------------------------------------------------------------------
-- 4. Squeeze the bloat out of what remains.
--
--    Two indexes holding identical entries measured 38 MB and 22 MB, so the
--    larger was carrying ~40% dead space. REINDEX rebuilds it compactly.
--    Done last, because dropping the duplicates first means less to rebuild.
-- --------------------------------------------------------------------------
do $ad4$
declare
  r       record;
  v_was   bigint;
  v_now   bigint;
  v_saved bigint := 0;
begin
  for r in
    select i.relname as idx, pg_relation_size(i.oid) as bytes
      from pg_index x
      join pg_class i     on i.oid = x.indexrelid
      join pg_class c     on c.oid = x.indrelid
      join pg_namespace n on n.oid = c.relnamespace
     where n.nspname = 'public'
       and x.indisvalid
       and pg_relation_size(i.oid) > 8 * 1024 * 1024     -- only the big ones
     order by pg_relation_size(i.oid) desc
  loop
    v_was := r.bytes;
    begin
      execute format('reindex index public.%I', r.idx);
    exception when others then
      raise notice 'ad4_50: could not reindex % - %', r.idx, sqlerrm;
      continue;
    end;
    select pg_relation_size(('public.' || quote_ident(r.idx))::regclass) into v_now;
    if v_was > v_now then
      v_saved := v_saved + (v_was - v_now);
      raise notice 'ad4_50: reindexed % - % -> %', r.idx,
                   pg_size_pretty(v_was), pg_size_pretty(v_now);
    end if;
  end loop;
  raise notice 'ad4_50: % more reclaimed by rebuilding bloated indexes',
               pg_size_pretty(v_saved);
end
$ad4$;


-- --------------------------------------------------------------------------
-- 5. What it came to.
-- --------------------------------------------------------------------------
do $ad4$
declare v_size bigint;
begin
  select pg_database_size(current_database()) into v_size;
  raise notice 'ad4_50: database is now %', pg_size_pretty(v_size);
  if v_size > 500 * 1024 * 1024 then
    raise notice 'ad4_50: still over the 500 MB free tier. Next places to look, in order:';
    raise notice '  select * from v_index_never_used;        -- indexes nothing has read';
    raise notice '  select * from v_archive_inventory;       -- which feed is growing fastest';
    raise notice '  Deleting history is the LAST resort and nobody should do it silently.';
  end if;
end
$ad4$;

notify pgrst, 'reload schema';
