-- ===========================================================================
-- Task 13a - Row Level Security.
--
-- The browser holds only the anon key (Revision A §6.3). Every table/view
-- the frontend reads gets an anon-read policy; every write goes through a
-- security definer RPC (sql/ad4_rpc.sql), never a direct table write from
-- the browser. The service key stays in n8n Config nodes and GitHub
-- Actions secrets only - never shipped to the frontend.
--
-- Run after every other sql/ad4_*.sql file (needs the tables/views they
-- create). Idempotent: `enable row level security` is a no-op if already
-- enabled, and policies are dropped-and-recreated by name.
--
-- SELF-SUFFICIENCY: this file no longer assumes any table or function it
-- names actually exists. Every loop below skips (with a NOTICE) anything
-- missing instead of aborting the whole script - so a database that is a
-- few tables behind still gets RLS on everything it does have, and the
-- NOTICE tells you exactly what was skipped. sql/ad4_00_preflight.sql
-- should have created them all already.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- Helper: apply the same "anon read, service role read/write" pattern to
-- a list of tables without repeating the four lines eleven times.
-- --------------------------------------------------------------------------
do $$
declare
  t text;
  anon_read_tables text[] := array[
    'cities', 'markets', 'bands', 'book_snapshots', 'band_probabilities',
    'edges', 'derived_forecast_skill', 'derived_weather_peak', 'derived_market_peak',
    'derived_city_day_volume', 'derived_band_day_volume',
    'derived_capacity', 'derived_city_correlation',
    'strategies', 'deployments', 'signals', 'paper_trades', 'ledger',
    'backtest_runs', 'backtest_results', 'backtest_trades',
    'cost_params', 'anomaly_rules', 'strategy_conflicts', 'weather_observations',
    'weather_forecasts', 'live_weather', 'weather_events', 'model_versions', 'ingest_log'
  ];
begin
  foreach t in array anon_read_tables loop
    if to_regclass('public.' || quote_ident(t)) is null then
      raise notice 'rls: table % does not exist - skipped (run sql/ad4_00_preflight.sql)', t;
      continue;
    end if;
    execute format('alter table %I enable row level security', t);
    execute format('drop policy if exists anon_read on %I', t);
    execute format('create policy anon_read on %I for select to anon using (true)', t);
  end loop;
end $$;

-- --------------------------------------------------------------------------
-- settings: readable by anon EXCEPT nothing sensitive lives here (bankroll,
-- risk limits, toggles - all meant to be visible in the UI that edits
-- them), but WRITES must go through RPCs only (see below), never a direct
-- UPDATE from the browser using the anon key.
-- --------------------------------------------------------------------------
do $$ begin
  if to_regclass('public.settings') is null then
    raise notice 'rls: settings does not exist - skipped';
  else
    alter table settings enable row level security;
    drop policy if exists anon_read on settings;
    create policy anon_read on settings for select to anon using (true);
  end if;
end $$;

-- --------------------------------------------------------------------------
-- anomalies: readable (the UI surfaces them), never anon-writable.
-- --------------------------------------------------------------------------
do $$ begin
  if to_regclass('public.anomalies') is null then
    raise notice 'rls: anomalies does not exist - skipped';
  else
    alter table anomalies enable row level security;
    drop policy if exists anon_read on anomalies;
    create policy anon_read on anomalies for select to anon using (true);
  end if;
end $$;

-- --------------------------------------------------------------------------
-- trades_observed: large, historical, read-only market data - anon read.
-- --------------------------------------------------------------------------
do $$ begin
  if to_regclass('public.trades_observed') is null then
    raise notice 'rls: trades_observed does not exist - skipped';
  else
    alter table trades_observed enable row level security;
    drop policy if exists anon_read on trades_observed;
    create policy anon_read on trades_observed for select to anon using (true);
  end if;
end $$;

-- --------------------------------------------------------------------------
-- Views inherit the RLS of their underlying tables automatically in
-- Postgres/PostgREST as long as the views are not `security definer` -
-- no separate policy needed for v_latest_book / v_latest_prob /
-- v_latest_edge / v_opportunities, since every table they read from
-- already has an anon_read policy above.
-- --------------------------------------------------------------------------

-- --------------------------------------------------------------------------
-- No policy at all = no access. These are service-key-only by omission,
-- not by an explicit deny policy (RLS with zero policies blocks all
-- access to non-owners by default) - documented here so it's clear this
-- is deliberate:
--
--   (nothing currently needs to be fully hidden from anon - every table
--   above is safe to read; the boundary AD4 actually needs is WRITE, not
--   READ. That boundary is enforced by the explicit REVOKE below, not by
--   the read-only policies above: a `for select` policy does not stop an
--   INSERT from a role that holds the INSERT privilege. Verified against
--   the live database, where anon held writes on ~40 tables while every
--   policy here said `for select`.)
-- --------------------------------------------------------------------------

-- --------------------------------------------------------------------------
-- Grants: RLS policies only take effect once the role actually has the
-- underlying SQL privilege too. Supabase's `anon` role needs USAGE on the
-- schema and SELECT on these tables - and must be stripped of everything
-- else first, because privileges granted before this file ever ran are
-- still in force until something takes them away.
--
-- Function execution is explicitly NOT blanket-granted - only the RPCs
-- the frontend is actually meant to call directly (Revision A §6.2/§6.4:
-- calc_recommendation, log_paper_trade, approve_signal, close_position,
-- queue_backtest) get anon EXECUTE. Everything else (settle_markets,
-- recompute_capacity, recompute_correlation, refresh_derived,
-- build_morning_brief, build_eod_report, compute_edges, evaluate_signals)
-- stays service_role-only, called by n8n/GitHub Actions with the service
-- key - an anon user must never be able to trigger settlement, force a
-- recompute, or forge a morning brief.
-- --------------------------------------------------------------------------
-- REVOKE BEFORE GRANT. This file used to only ever add privileges, which
-- is why the write boundary was open for so long: Phase 0 had run a
-- `grant all in schema public to anon` at some point, and a grant that is
-- never revoked never goes away. An anon_read policy does not save you -
-- RLS gates rows, the GRANT gates the verb, and anon held INSERT/UPDATE/
-- DELETE/TRUNCATE on ~40 tables while every policy here said "for select".
do $$
declare r text;
begin
  foreach r in array array['anon','authenticated'] loop
    if not exists (select 1 from pg_roles where rolname = r) then
      raise notice 'rls: role % does not exist here - skipped', r;
      continue;
    end if;
    execute format('revoke insert, update, delete, truncate, references, trigger on all tables in schema public from %I', r);
    execute format('revoke update on all sequences in schema public from %I', r);
    begin
      execute format('alter default privileges in schema public revoke insert, update, delete, truncate on tables from %I', r);
    exception when others then
      raise notice 'rls: could not alter default privileges for % (%)', r, sqlerrm;
    end;
    execute format('grant usage on schema public to %I', r);
    execute format('grant select on all tables in schema public to %I', r);
    execute format('grant usage, select on all sequences in schema public to %I', r);
  end loop;
  execute 'revoke insert, update, delete, truncate on all tables in schema public from public';
end $$;

revoke execute on all functions in schema public from anon, public;

do $$
declare
  sig text;
  anon_execute text[] := array[
    'calc_recommendation(jsonb)',
    'log_paper_trade(jsonb)',
    'approve_signal(bigint)',
    'dismiss_signal(bigint)',
    -- paper_trades.trade_id is uuid on the real database and bigserial on
    -- one this repo built from scratch; resolve it rather than assume.
    'close_position(' || coalesce((select data_type from information_schema.columns
       where table_schema = 'public' and table_name = 'paper_trades'
         and column_name = 'trade_id'), 'bigint') || ', numeric, text)',
    'queue_backtest(jsonb)',
    'update_setting(text, jsonb)',
    'upsert_deployment(jsonb)',
    'set_deployment_status(uuid, text)'
  ];
begin
  foreach sig in array anon_execute loop
    begin
      execute format('grant execute on function %s to anon', sig);
    exception when undefined_function then
      raise notice 'rls: function %  does not exist - grant skipped. Run sql/ad4_rpc.sql (and ad4_backtest.sql) before this file.', sig;
    end;
  end loop;
end $$;
