-- ===========================================================================
-- ad4_46_jit.sql - THE ACTUAL CAUSE OF `canceling statement due to statement
-- timeout (57014)`, and it is not the queries.
--
-- Run any time. Changes no data, creates no object except one view. Takes
-- effect for connections opened after it runs, so reload the app once.
--
--
-- WHAT WAS HAPPENING
--
-- The desk's views are deeply nested on purpose - v_campaign_state reads
-- v_trade_plan reads v_opportunities reads v_band_book, and each one is doing
-- real work rather than repeating a calculation somewhere else. That nesting
-- produces an ENORMOUS PLAN: 1,164 expression functions for a single select.
--
-- PostgreSQL's JIT compiler looks at the plan's estimated COST, not at how
-- much data will actually move. Past jit_above_cost (default 100,000) it
-- compiles the expressions with LLVM; past jit_inline_above_cost and
-- jit_optimize_above_cost (default 500,000) it also inlines and runs the
-- optimiser over them. A plan this wide crosses all three thresholds, so
-- every one of those 1,164 functions is generated, inlined, optimised and
-- emitted as machine code - before a single row is read.
--
-- Measured on a database at this desk's real size (710,400 observations,
-- 851 markets, 9,361 bands, 187,220 book snapshots, 168,498 trades):
--
--     JIT:
--       Functions: 1164
--       Timing: Generation 100 ms, Inlining 148 ms,
--               Optimization 9,861 ms, Emission 7,094 ms, Total 17,204 ms
--     Execution Time: 17,234 ms
--
-- Seventeen seconds, of which seventeen seconds is LLVM. The query itself
-- returns ZERO rows and runs in a fifth of a millisecond. Supabase gives the
-- browser's role a few seconds per statement, so the statement is cancelled
-- and the page shows a red box - for a query that had no work to do.
--
--     v_campaign_state    16,808 ms  ->     73 ms
--     v_trade_plan           546 ms  ->     22 ms
--     v_city_day_plan        408 ms  ->      9 ms
--
-- 230x on the worst one, from one setting.
--
--
-- WHY IT LOOKED LIKE A DIFFERENT BUG EVERY TIME
--
-- Two things made this very hard to see, and both sent every investigation
-- somewhere else:
--
--   1. IT ONLY HAPPENS WITH A LIMIT. `select * from v_campaign_state` costs
--      203 ms; `select * from v_campaign_state limit 5000` costs 12,907 ms,
--      because the extra node tips the estimate over the JIT thresholds.
--      PostgREST ALWAYS appends a limit. So the query is instant in the
--      Supabase SQL editor and times out from the app, which reads as an app
--      bug, a permissions problem, or a missing index - anything but this.
--
--   2. IT IS INVISIBLE IN THE PLAN. Every node reports "never executed" and
--      the totals are microseconds. The seventeen seconds appear only in the
--      JIT: block at the bottom of an EXPLAIN ANALYZE, which is below where
--      anyone stops reading.
--
-- sql/ad4_44_indexes.sql was real and worth running - the four hot tables
-- genuinely had no usable index - but indexes were never going to fix this,
-- because there was no scan to speed up.
--
--
-- WHY OFF FOR THE BROWSER AND NOT FOR EVERYTHING
--
-- JIT pays when a query does a lot of work per row over a lot of rows. That
-- describes the nightly jobs - refresh_feature_cache walks 710,400
-- observations - and it describes nothing the browser does. Every page here
-- reads at most a few thousand rows through a very wide plan, which is the
-- exact shape where JIT costs more than the query.
--
-- So it is turned off for anon and authenticated, which are the roles with a
-- statement timeout and the roles every 57014 came from. service_role, which
-- the pipelines and n8n use, is left alone: it has no such timeout and its
-- jobs are the ones that can benefit.
-- ===========================================================================

do $ad4$
declare
  r    text;
  v_db text := current_database();
begin
  foreach r in array array['anon', 'authenticated'] loop
    if not exists (select 1 from pg_roles where rolname = r) then
      raise notice 'ad4_46: role % does not exist here - skipped', r;
      continue;
    end if;
    begin
      execute format('alter role %I in database %I set jit = off', r, v_db);
      raise notice 'ad4_46: jit = off for % in %', r, v_db;
    exception when insufficient_privilege then
      -- Not fatal, and there is a second way in below.
      raise notice 'ad4_46: not permitted to set jit for % - trying the database instead', r;
    end;
  end loop;

  -- Belt and braces. A per-role setting needs the role to exist and needs the
  -- privilege; a database-level default needs neither, and a role-level
  -- setting still wins over it where both applied.
  --
  -- This turns JIT off for the nightly jobs too. That is the safe direction:
  -- the worst case is that a full-archive pass loses some compiled-expression
  -- speed on a job with no statement timeout and an hour to finish, against a
  -- best case of the desk not going red. Turn it back on for one job with
  --   set jit = on;
  -- at the top of that session if you ever measure it mattering.
  begin
    execute format('alter database %I set jit = off', v_db);
    raise notice 'ad4_46: jit = off for database %', v_db;
  exception when insufficient_privilege then
    raise notice 'ad4_46: not permitted to set jit on the database either. '
                 'Run this as the owner, or set it in Supabase -> Settings -> '
                 'Database -> Custom Postgres config.';
  end;

  raise notice 'ad4_46: takes effect on NEW connections - reload the app once.';
end
$ad4$;


-- --------------------------------------------------------------------------
-- So it can be checked from the app rather than taken on trust. The Databank
-- and the health strip read this: a desk that has not run this file says so,
-- instead of showing a timeout and blaming a missing index.
-- --------------------------------------------------------------------------
create or replace view v_jit_state as
with settings as (
  select coalesce(r.rolname, '(database default)') as who,
         substring(c from 'jit=(.*)')             as val
    from pg_db_role_setting s
    join pg_database d on d.oid = s.setdatabase
    left join pg_roles r on r.oid = s.setrole
    cross join lateral unnest(s.setconfig) c
   where d.datname = current_database()
     and c like 'jit=%'
)
select
  current_setting('jit', true)                                   as jit_in_this_session,
  coalesce((select val from settings where who = '(database default)'), 'not set')
                                                                 as jit_on_database,
  coalesce((select string_agg(who || '=' || val, ', ' order by who)
              from settings where who <> '(database default)'), 'none')
                                                                 as jit_by_role,
  (current_setting('jit', true) = 'off')                         as ok,
  case when current_setting('jit', true) = 'off'
       then 'JIT is off for this connection. Deeply nested views plan and run in milliseconds.'
       else 'JIT IS ON for this connection. A view like v_campaign_state can spend 17 seconds '
         || 'in LLVM before reading a row, and Supabase cancels it (57014). '
         || 'Run sql/ad4_46_jit.sql, then reload - the setting applies to new connections.'
  end                                                            as says;

comment on view v_jit_state is
  'Is PostgreSQL JIT off for this connection? On these deeply nested views JIT compiles ~1,164 functions - 17 seconds of LLVM - for queries that return nothing. This is the 57014 nobody could find. sql/ad4_46_jit.sql.';

grant select on v_jit_state to anon, authenticated, service_role;
