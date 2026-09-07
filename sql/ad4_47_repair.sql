-- ===========================================================================
-- ad4_47_repair.sql - RUN THIS ONE FILE. It fixes both live failures and
-- then tells you, by name, anything still genuinely missing.
--
-- Safe to run any time, as many times as you like. Changes no data.
--
--
-- WHAT WAS BROKEN, AND BOTH OF THESE ARE MY FAULT
--
-- 1. permission denied for function ad4_plural  (42501)
--
--    ad4_38_grants.sql revokes EXECUTE on every function in public from the
--    browser roles and then grants back a named list - a deliberate design,
--    because the anon key ships in the JavaScript bundle and everything it
--    can call is callable by anyone on the internet.
--
--    ad4_plural is on that list. ad4_region is on that list. band_contains is
--    on that list. And NONE OF THEM WERE GRANTED, because ad4_38 is file 38
--    and those functions are created by files 40, 41 and later. The grant
--    found no such function, and the handler for that case says - in a
--    comment I wrote - "not an error: re-running this file after that file
--    grants it". Nobody re-runs file 38 after file 41.
--
--    So on any database installed in the documented order, every view that
--    calls one of those functions has been refusing the browser since the
--    day ad4_38 was written. Synthesis calls ad4_plural in four places.
--
--    It survived every test because `select count(*)` never evaluates the
--    columns that call the function - only `select *` does, which is exactly
--    what the browser sends and exactly what no test sent.
--
-- 2. Could not find the table 'public.v_city_stats' in the schema cache
--    (PGRST205)
--
--    Not the same thing as "does not exist". PostgREST keeps its own cache of
--    the schema and it does not notice DDL on its own. ad4_13 drops and
--    recreates v_opportunities, and the cascade takes v_city_stats,
--    v_trade_plan, v_city_reasoning, v_city_day_plan, v_campaign_state and
--    v_band_ladder with it. ad4_13 rebuilds all six - correctly, as of the
--    last change - but PostgREST is still holding the OIDs of the ones that
--    were dropped, so the API says they are gone while Postgres has them.
--
--    One NOTIFY fixes it. Every file that changes a relation should have been
--    sending it; none were.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- 1. THE BROWSER'S FUNCTION GRANTS, RE-ASSERTED FROM THE LAST FILE.
--
--    The list lives here as well as in ad4_38 on purpose. ad4_38 sets the
--    policy - revoke everything, grant back what the UI calls - and it has to
--    run early, before the tables it protects carry anything. This runs LAST,
--    when every function actually exists, and asserts the same list against
--    reality. Neither is redundant: without ad4_38 the surface is wide open,
--    and without this half the list is never granted at all.
-- --------------------------------------------------------------------------
create or replace function ad4_grant_browser_functions()
returns integer language plpgsql as $ad4$
declare
  f text;
  r text;
  n int := 0;
  missing text[] := '{}';
begin
  foreach f in array array[
    -- RPCs the pages call directly
    'approve_signal', 'dismiss_signal', 'set_run_scope', 'set_strategy_enabled',
    'queue_backtest', 'backtest_readiness', 'upsert_deployment',
    'set_deployment_status', 'update_setting', 'run_scope',
    -- helpers called from INSIDE a view, which is the half that was missing.
    -- A view does not execute its functions as its owner unless they are
    -- SECURITY DEFINER, so the reader needs EXECUTE on every one of these or
    -- the whole select is refused.
    'band_contains', 'band_local_value', 'capacity_side', 'depth_usd',
    'ad4_num', 'ad4_plural', 'ad4_region', 'ad4_norm_levels',
    'ad4_synth_levels', 'ad4_raw_book_side', 'ad4_trades_ts_expr'
  ] loop
    if not exists (select 1 from pg_proc p join pg_namespace ns on ns.oid = p.pronamespace
                    where ns.nspname = 'public' and p.proname = f) then
      missing := missing || f;
      continue;
    end if;
    foreach r in array array['anon', 'authenticated'] loop
      if not exists (select 1 from pg_roles where rolname = r) then continue; end if;
      begin
        execute format('grant execute on function public.%I to %I', f, r);
        n := n + 1;
      exception when others then
        raise notice 'ad4_47: could not grant % to % (%)', f, r, sqlerrm;
      end;
    end loop;
  end loop;

  if array_length(missing, 1) > 0 then
    -- Named, not swallowed. A function on this list that does not exist means
    -- the file creating it has not been run, and the page using it will be
    -- red for a reason no error message on that page can explain.
    raise notice 'ad4_47: % function(s) on the browser list do not exist yet: % - run the SQL files that create them, then this one again',
      array_length(missing, 1), array_to_string(missing, ', ');
  end if;
  return n;
end
$ad4$;

do $ad4$
declare n int;
begin
  n := ad4_grant_browser_functions();
  raise notice 'ad4_47: % browser function grant(s) asserted.', n;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 2. TELL POSTGREST THE SCHEMA CHANGED.
--
--    PostgREST caches the schema and does not watch for DDL. Supabase wires
--    this NOTIFY to a reload; on a plain Postgres it is a no-op that costs
--    nothing. Without it, a view that was dropped and recreated answers
--    PGRST205 "could not find the table ... in the schema cache" while
--    sitting perfectly healthy in the database - which reads as a missing
--    file and sends you to re-run the wrong thing.
-- --------------------------------------------------------------------------
notify pgrst, 'reload schema';


-- --------------------------------------------------------------------------
-- 3. WHAT IS ACTUALLY STILL MISSING - by name, with the file that creates it.
--
--    After the two fixes above, anything still listed here is genuinely
--    absent from the database rather than absent from a cache, and the only
--    cure is running the file named beside it.
-- --------------------------------------------------------------------------
do $ad4$
declare
  r record;
  n int := 0;
begin
  raise notice '-----------------------------------------------------';
  for r in
    select * from (values
      ('v_city_stats',          'sql/ad4_17_city_stats.sql'),
      ('v_trade_plan',          'sql/ad4_34_trade_plan.sql'),
      ('v_city_day_plan',       'sql/ad4_34_trade_plan.sql'),
      ('v_city_reasoning',      'sql/ad4_23_reasoning.sql'),
      ('v_band_ladder',         'sql/ad4_13_reconcile.sql'),
      ('v_campaign_state',      'sql/ad4_41_campaigns.sql'),
      ('v_campaign_targets',    'sql/ad4_41_campaigns.sql'),
      ('v_synthesis_findings',  'sql/ad4_40_synthesis.sql'),
      ('v_learning_state',      'sql/ad4_40_synthesis.sql'),
      ('v_opportunities',       'sql/ad4_13_reconcile.sql'),
      ('v_latest_book',         'sql/ad4_13_reconcile.sql'),
      ('v_band_book',           'sql/ad4_13_reconcile.sql'),
      ('v_forecast_convergence','sql/ad4_31_predictive.sql'),
      ('v_prediction_ladder',   'sql/ad4_31_predictive.sql'),
      ('v_data_freshness',      'sql/ad4_39_freshness.sql'),
      ('v_forecast_audit',      'sql/ad4_43_forecast_audit.sql'),
      ('v_jit_state',           'sql/ad4_46_jit.sql')
    ) t(rel, file)
    where to_regclass('public.' || t.rel) is null
  loop
    raise warning 'MISSING  %  ->  run %', rpad(r.rel, 24), r.file;
    n := n + 1;
  end loop;

  if n = 0 then
    raise notice 'ad4_47: every relation the UI reads exists. If a page still says';
    raise notice '        "could not find the table ... in the schema cache", that is';
    raise notice '        PostgREST, not Postgres - the NOTIFY above has told it to';
    raise notice '        reload; give it a few seconds and refresh the page.';
  else
    raise notice 'ad4_47: % relation(s) missing - run the files above, then this one again.', n;
  end if;
  raise notice '-----------------------------------------------------';
end
$ad4$;


-- --------------------------------------------------------------------------
-- 4. AND PROVE IT, as the browser rather than as the owner.
--
--    `select count(*)` does not evaluate the columns of a view, so it passes
--    on a view whose functions the caller cannot execute. That is precisely
--    how the ad4_plural failure survived every check. This selects an actual
--    ROW as anon, which is what the browser does.
-- --------------------------------------------------------------------------
do $ad4$
declare
  v text;
  bad text[] := '{}';
  ok  int := 0;
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    raise notice 'ad4_47: no anon role here - skipping the browser check.';
    return;
  end if;
  for v in select unnest(array[
      'v_synthesis_findings', 'v_learning_state', 'v_city_stats', 'v_trade_plan',
      'v_opportunities', 'v_campaign_state', 'v_city_day_plan', 'v_city_reasoning',
      'v_forecast_convergence', 'v_data_freshness'])
  loop
    if to_regclass('public.' || v) is null then continue; end if;
    begin
      set local role anon;
      execute format('select * from public.%I limit 1', v);
      reset role;
      ok := ok + 1;
    exception when others then
      reset role;
      bad := bad || format('%s (%s)', v, sqlerrm);
    end;
  end loop;

  if array_length(bad, 1) > 0 then
    raise warning 'ad4_47: the browser role STILL cannot read % view(s): %',
      array_length(bad, 1), array_to_string(bad, '; ');
  else
    raise notice 'ad4_47: the browser role can read all % checked view(s). The pages will work.', ok;
  end if;
end
$ad4$;


-- ===========================================================================
-- 5. RLS AND search_path, asserted rather than assumed.
--
--    Two things a Supabase security advisor flags that are worth fixing, and
--    that no other file was asserting:
--
--    RLS ON EVERY TABLE. Not because the browser must be kept out of them -
--    it is granted SELECT on all of them deliberately - but because a table
--    that is the ONLY one without RLS is a table somebody forgot, and the
--    forgetting is the bug. derived_calibration_adjustment was the one of 48.
--
--    search_path ON EVERY SECURITY DEFINER FUNCTION. A SECURITY DEFINER
--    function runs with its OWNER's privileges; if its search_path is not
--    pinned, the CALLER decides which schema a bare table name resolves to.
--    A caller who can create a table in a schema earlier on their own path
--    can make a privileged function read or write THEIRS instead of the real
--    one. All 34 were unpinned. Pinning changes no behaviour - public,
--    pg_temp is what they already resolved to - it removes the redirection.
-- ===========================================================================
do $ad4$
declare r record; n int := 0; m int := 0;
begin
  for r in
    select c.relname
      from pg_class c join pg_namespace ns on ns.oid = c.relnamespace
     where ns.nspname = 'public' and c.relkind = 'r' and not c.relrowsecurity
  loop
    begin
      execute format('alter table public.%I enable row level security', r.relname);
      execute format('drop policy if exists anon_read on public.%I', r.relname);
      execute format('create policy anon_read on public.%I for select to anon using (true)', r.relname);
      execute format('drop policy if exists authenticated_read on public.%I', r.relname);
      execute format('create policy authenticated_read on public.%I for select to authenticated using (true)', r.relname);
      n := n + 1;
    exception when others then
      raise notice 'ad4_47: could not enable RLS on % (%)', r.relname, sqlerrm;
    end;
  end loop;

  for r in
    select p.oid::regprocedure::text as sig
      from pg_proc p join pg_namespace ns on ns.oid = p.pronamespace
     where ns.nspname = 'public' and p.prosecdef and p.proconfig is null
  loop
    begin
      execute format('alter function %s set search_path = public, pg_temp', r.sig);
      m := m + 1;
    exception when others then
      raise notice 'ad4_47: could not pin search_path on % (%)', r.sig, sqlerrm;
    end;
  end loop;

  raise notice 'ad4_47: RLS enabled on % table(s) that lacked it; search_path pinned on % function(s)', n, m;
end
$ad4$;
