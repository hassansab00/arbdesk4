-- ===========================================================================
-- Task 12 - backtest job queue RPC + supporting columns.
--
-- Job-queue architecture per Revision A §6.2 (no Vercel functions): UI
-- calls queue_backtest (writes backtest_runs, status='queued'); GitHub
-- Actions polls on a schedule (.github/workflows/backtest.yml) and runs
-- scripts/backtest/runner.py, which reads params straight from Supabase
-- and writes results back. Immutable saved runs: queue_backtest always
-- INSERTs a new row - there is no "update and re-run" path.
--
-- SCHEMA ASSUMPTION: backtest_runs/backtest_results/backtest_trades exist
-- (spec §0.2: "schema exists, EMPTY") but their exact columns aren't
-- given anywhere in the spec. These idempotent alters add what
-- scripts/backtest/runner.py needs; verify against the live schema.
-- ===========================================================================

alter table backtest_runs add column if not exists run_id uuid default gen_random_uuid();
alter table backtest_runs add column if not exists params jsonb not null default '{}'::jsonb;
alter table backtest_runs add column if not exists status text not null default 'queued'
  check (status in ('queued','running','complete','failed'));
alter table backtest_runs add column if not exists created_at timestamptz not null default now();
alter table backtest_runs add column if not exists started_at timestamptz;
alter table backtest_runs add column if not exists finished_at timestamptz;
alter table backtest_runs add column if not exists error text;
alter table backtest_runs add column if not exists label text;
do $$ begin
  if not exists (select 1 from pg_constraint where conname = 'backtest_runs_run_id_key') then
    alter table backtest_runs add constraint backtest_runs_run_id_key unique (run_id);
  end if;
end $$;

alter table backtest_results add column if not exists run_id uuid references backtest_runs(run_id) on delete cascade;
alter table backtest_results add column if not exists scope text
  check (scope in ('headline','strategy','city','calibration','costs','distribution','signal_frequency','equity_curve'));
alter table backtest_results add column if not exists data jsonb;
create index if not exists idx_backtest_results_run on backtest_results (run_id, scope);

alter table backtest_trades add column if not exists run_id uuid references backtest_runs(run_id) on delete cascade;
alter table backtest_trades add column if not exists strategy_id text;
alter table backtest_trades add column if not exists city_key text;
alter table backtest_trades add column if not exists band_id uuid;
alter table backtest_trades add column if not exists side text;
alter table backtest_trades add column if not exists shares numeric;
alter table backtest_trades add column if not exists avg_fill_price numeric;
alter table backtest_trades add column if not exists gross_pnl numeric;
alter table backtest_trades add column if not exists net_pnl numeric;
alter table backtest_trades add column if not exists fee_paid numeric;
alter table backtest_trades add column if not exists gas_paid numeric;
alter table backtest_trades add column if not exists slippage_paid numeric;
alter table backtest_trades add column if not exists model_prob numeric;
alter table backtest_trades add column if not exists won boolean;
alter table backtest_trades add column if not exists resolution_date date;
alter table backtest_trades add column if not exists regime_label text;
create index if not exists idx_backtest_trades_run on backtest_trades (run_id);

create or replace function queue_backtest(p_params jsonb) returns uuid
language plpgsql security definer as $$
declare
  v_run_id uuid := gen_random_uuid();
begin
  insert into backtest_runs (run_id, params, status, created_at)
  values (v_run_id, p_params, 'queued', now());
  return v_run_id;
end;
$$;
