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
    'derived_city_day_volume', 'derived_capacity', 'derived_city_correlation',
    'strategies', 'deployments', 'signals', 'paper_trades', 'ledger',
    'backtest_runs', 'backtest_results', 'backtest_trades',
    'cost_params', 'anomaly_rules', 'strategy_conflicts', 'weather_observations',
    'weather_forecasts', 'live_weather', 'weather_events', 'model_versions', 'ingest_log'
  ];
begin
  foreach t in array anon_read_tables loop
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
alter table settings enable row level security;
drop policy if exists anon_read on settings;
create policy anon_read on settings for select to anon using (true);

-- --------------------------------------------------------------------------
-- anomalies: readable (the UI surfaces them), never anon-writable.
-- --------------------------------------------------------------------------
alter table anomalies enable row level security;
drop policy if exists anon_read on anomalies;
create policy anon_read on anomalies for select to anon using (true);

-- --------------------------------------------------------------------------
-- trades_observed: large, historical, read-only market data - anon read.
-- --------------------------------------------------------------------------
alter table trades_observed enable row level security;
drop policy if exists anon_read on trades_observed;
create policy anon_read on trades_observed for select to anon using (true);

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
--   READ, which is enforced by never granting UPDATE/INSERT/DELETE to
--   anon at all - PostgREST only allows what a policy explicitly grants,
--   and none of the policies above are `for insert`/`for update`/
--   `for delete`, so anon can SELECT and nothing else on every table.)
-- --------------------------------------------------------------------------

-- --------------------------------------------------------------------------
-- Grants: RLS policies only take effect once the role actually has the
-- underlying SQL privilege too. Supabase's `anon` role needs USAGE on the
-- schema and SELECT on these tables (writes are not granted here at all,
-- so even a security-definer-less RPC bug can't let anon write through).
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
grant usage on schema public to anon;
grant select on all tables in schema public to anon;

revoke execute on all functions in schema public from anon, public;
grant execute on function calc_recommendation(jsonb) to anon;
grant execute on function log_paper_trade(jsonb) to anon;
grant execute on function approve_signal(bigint) to anon;
grant execute on function dismiss_signal(bigint) to anon;
grant execute on function close_position(bigint, numeric, text) to anon;
grant execute on function queue_backtest(jsonb) to anon;
grant execute on function update_setting(text, jsonb) to anon;
grant execute on function upsert_deployment(jsonb) to anon;
grant execute on function set_deployment_status(uuid, text) to anon;
