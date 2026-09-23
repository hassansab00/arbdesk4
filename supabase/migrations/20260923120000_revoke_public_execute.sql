-- NO SECURITY DEFINER FUNCTION IS EXECUTABLE BY PUBLIC (plan v2, step P1.1).
--
-- Postgres gives EXECUTE to PUBLIC on every new function unless it is
-- revoked, and PUBLIC includes anon. sql/ad4_38_grants.sql revoked from anon
-- by name but never from PUBLIC, so anyone holding the site's anon key could
-- call these, and each runs with the owner's rights (live, 23 Sep:
-- pg_proc.proacl carried `=X/postgres` on ten SECURITY DEFINER functions in
-- public, and has_function_privilege('anon', ...) was true for all ten):
--
--   prune_trades, prune_exported_paper_trades     delete rows
--   paper_desk_reset, paper_desk_archive           wipe or hide a desk
--   paper_desk_create, paper_desk_update           make or re-policy a desk
--   refresh_observation_trust, recompute_capacity_city,
--   refresh_weather_peak_city                      recompute derived tables
--   paper_plan_status_from_order                   a trigger function
--
-- Every caller of those ten uses the service key (scripts/, and the board's
-- /api/paper-desk route, server-side), so none of them loses access here.
-- A trigger function needs no EXECUTE to fire.
--
-- Three parts:
--   1. Every SECURITY DEFINER function in public loses its PUBLIC grant.
--   2. The four destructive ones lose anon and authenticated too, and are
--      granted to service_role explicitly.
--   3. Postgres stops giving PUBLIC execute on functions created from now on,
--      so the next function a migration or sql/ file creates cannot silently
--      reopen this. Anything a browser role needs is granted by name.
--
-- The anon grants given BY NAME to the board's write RPCs (update_setting,
-- set_strategy_enabled, approve_signal, queue_backtest, ...) are not touched
-- here. They move behind authenticated API routes in P1.2. The read-only
-- allowlist below is what A2 (plan Appendix A) is measured against once
-- they do.
--
-- Idempotent: revokes and grants are no-ops when already in place.

-- 1. PUBLIC off every SECURITY DEFINER function in public.
do $$
declare f regprocedure;
begin
  for f in select p.oid::regprocedure
             from pg_proc p join pg_namespace n on n.oid = p.pronamespace
            where n.nspname = 'public' and p.prosecdef
  loop
    execute format('revoke execute on function %s from public', f);
  end loop;
end $$;

-- 2. The destructive four: service_role only. Guarded because the PGlite
-- harness creates some of them after the migrations (from sql/), and those
-- files now carry the same grants themselves.
do $$
declare sig text;
begin
  foreach sig in array array[
    'public.prune_trades(integer, boolean, timestamptz, bigint)',
    'public.prune_exported_paper_trades(integer, uuid[], boolean)',
    'public.paper_desk_reset(uuid)',
    'public.paper_desk_archive(uuid, boolean)']
  loop
    if to_regprocedure(sig) is not null then
      execute format('revoke execute on function %s from public, anon, authenticated', sig);
      execute format('grant execute on function %s to service_role', sig);
    end if;
  end loop;
end $$;

-- 3. From now on, a function is executable only by who it is granted to.
alter default privileges for role postgres revoke execute on functions from public;

-- The read-only SECURITY DEFINER functions the browser may call as anon.
-- Every other SECURITY DEFINER function anon can execute is a write, and P1.2
-- removes it. A view, so the acceptance query A2 reads the list rather than
-- restating it.
create or replace view public.v_anon_rpc_allowlist as
select * from (values
  ('backtest_readiness'::text, 'stable: reports whether a backtest window has data'),
  ('run_scope'::text,          'stable: reads which cities a run covers')
) as t(proname, why);
revoke all on public.v_anon_rpc_allowlist from public, anon, authenticated;
grant select on public.v_anon_rpc_allowlist to service_role;
