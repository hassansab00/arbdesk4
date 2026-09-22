-- ===========================================================================
-- 83 ERRORS FROM THE ADVISOR. ONE IS A DEFECT, EIGHTY-TWO ARE THE DESIGN.
--
-- Supabase's linter reported, on 2026-09-22:
--
--     ERROR  82  security_definer_view
--     ERROR   1  rls_disabled_in_public          derived_model_promotion
--     WARN   32  function_search_path_mutable
--     WARN   44  anon/authenticated security definer function executable
--     WARN    1  materialized_view_in_api        mv_venue_band_resolution
--     INFO   16  rls_enabled_no_policy
--
-- THE EIGHTY-TWO ARE LOAD-BEARING, and turning them off would blank the app.
-- Eight tables carry per-user RLS - desk_members, paper_accounts,
-- paper_activity, paper_orders, paper_position_settlements, paper_positions,
-- paper_trade_plans, research_captures - and every one of their policies is
-- granted to `authenticated` alone. The web app never signs in: web/lib
-- constructs one client from NEXT_PUBLIC_SUPABASE_ANON_KEY and there is no
-- signIn, signUp, getSession or setSession anywhere in web/. Every read is
-- `anon`.
--
-- So `security_invoker = on` on v_paper_desks, v_strategy_desk_board,
-- v_paper_desk_integrity, v_data_freshness or v_prunable_resolution_evidence
-- would apply an authenticated-only policy to an anonymous caller and return
-- zero rows. The desk pages would go blank. The definer view IS the
-- mechanism by which a single-operator, anon-key app reads its own per-user
-- tables, and the access boundary is Vercel's deployment protection rather
-- than Postgres RLS.
--
-- THAT IS A PRECONDITION, NOT A PERMANENT TRUTH. It holds while there is one
-- operator and no sign-in. The moment the app gains an auth flow, or a second
-- person gets a login, those five views start serving one user another
-- user's desk - and the lint stops being noise. That is why this is written
-- down here and asserted in tests rather than dismissed in a commit message.
--
-- WHAT THIS MIGRATION ACTUALLY CHANGES - only what is free or genuinely wrong:
--
--   1. derived_model_promotion is the one public table with RLS off. Every
--      sibling - derived_capacity, derived_forecast_skill - has RLS on with
--      an `anon_read` SELECT policy of `true`. This gives it the same, so
--      nothing that can read it today reads any less.
--
--   2. Thirty-two functions run with a mutable search_path, four of them
--      SECURITY DEFINER: recompute_capacity_city, refresh_feature_cache,
--      refresh_weather_peak_city and should_run. A definer function with an
--      unpinned path can be made to resolve an unqualified name against a
--      schema the caller controls. None of the thirty-two references cron,
--      auth, storage or extensions, so pinning costs nothing and removes the
--      class.
--
--      Done as a loop over what is actually unpinned rather than a list of
--      names, so re-running covers anything added since.
--
-- Idempotent: enabling RLS twice is a no-op, the policy is created only if
-- absent, and the loop skips functions already pinned.
-- ===========================================================================

-- 1 ------------------------------------------------------------------------
do $ad4$
begin
  if to_regclass('public.derived_model_promotion') is null then
    raise notice 'derived_model_promotion absent - nothing to secure';
    return;
  end if;

  execute 'alter table public.derived_model_promotion enable row level security';

  if not exists (select 1 from pg_policies
                  where schemaname = 'public'
                    and tablename  = 'derived_model_promotion'
                    and policyname = 'anon_read') then
    execute 'create policy anon_read on public.derived_model_promotion '
            'for select to anon using (true)';
  end if;

  if not exists (select 1 from pg_policies
                  where schemaname = 'public'
                    and tablename  = 'derived_model_promotion'
                    and policyname = 'authenticated_read') then
    execute 'create policy authenticated_read on public.derived_model_promotion '
            'for select to authenticated using (true)';
  end if;
end
$ad4$;

-- 2 ------------------------------------------------------------------------
do $ad4$
declare
  r record;
  v_n int := 0;
begin
  for r in
    select p.oid,
           quote_ident(n.nspname) || '.' || quote_ident(p.proname) as fq,
           pg_get_function_identity_arguments(p.oid) as args
      from pg_proc p
      join pg_namespace n on n.oid = p.pronamespace
     where n.nspname = 'public'
       and p.prokind in ('f', 'p')
       and not exists (select 1 from unnest(coalesce(p.proconfig, '{}')) c
                        where c like 'search_path=%')
  loop
    -- public first so every unqualified name resolves exactly as it did;
    -- extensions because Supabase installs pgcrypto and friends there;
    -- pg_temp deliberately absent, which is the whole point for a definer.
    begin
      execute format('alter function %s(%s) set search_path = public, extensions',
                     r.fq, r.args);
      v_n := v_n + 1;
    exception when others then
      raise notice 'could not pin search_path on %(%): %', r.fq, r.args, sqlerrm;
    end;
  end loop;
  raise notice 'pinned search_path on % function(s)', v_n;
end
$ad4$;


-- 3 ------------------------------------------------------------------------
-- Two functions added earlier today granted EXECUTE to anon and authenticated
-- out of habit, and both are server-side only: book_as_of is called by the
-- backtest runner and storage_pressure by the archive job, each with the
-- service key. Nothing in web/ calls either. Granting them to anon added two
-- findings to the very list this migration exists to shorten.
--
-- REVOKING FROM anon AND authenticated IS NOT ENOUGH. Postgres grants EXECUTE
-- to PUBLIC on every new function, and anon inherits it - measured here, the
-- first revoke left has_function_privilege('anon', ...) still true.
do $ad4$
begin
  if to_regprocedure('public.book_as_of(uuid[], timestamptz)') is not null then
    revoke execute on function public.book_as_of(uuid[], timestamptz)
      from public, anon, authenticated;
    grant execute on function public.book_as_of(uuid[], timestamptz) to service_role;
  end if;
  if to_regprocedure('public.storage_pressure()') is not null then
    revoke execute on function public.storage_pressure() from public, anon, authenticated;
    grant execute on function public.storage_pressure() to service_role;
  end if;
end
$ad4$;
