-- ===========================================================================
-- Tasks 9-11 - columns paper_engine.py / signals.py / settlement.py write,
-- beyond what Task 2's ad4_phase2.sql already added to paper_trades.
-- All idempotent (add column if not exists) - safe to run regardless of
-- what the base schema already has. See docs/schema_assumptions.md.
-- ===========================================================================

-- paper_trades: lifecycle fields beyond Task 2's five additions
alter table paper_trades add column if not exists strategy_id text;
alter table paper_trades add column if not exists band_id uuid references bands(band_id);
alter table paper_trades add column if not exists side text check (side in ('YES','NO'));
alter table paper_trades add column if not exists action text check (action in ('ENTER','EXIT'));
alter table paper_trades add column if not exists shares numeric;
alter table paper_trades add column if not exists avg_fill_price numeric;
alter table paper_trades add column if not exists quoted_price numeric;
alter table paper_trades add column if not exists slippage_paid numeric;
alter table paper_trades add column if not exists fee_paid numeric;
alter table paper_trades add column if not exists gas_paid numeric;
alter table paper_trades add column if not exists cost_version text;
alter table paper_trades add column if not exists forecast_version text;
alter table paper_trades add column if not exists calibration_version text;
alter table paper_trades add column if not exists regime_label text;
alter table paper_trades add column if not exists opened_at timestamptz;
alter table paper_trades add column if not exists closed_at timestamptz;
alter table paper_trades add column if not exists exit_price numeric;
alter table paper_trades add column if not exists gross_pnl numeric;
alter table paper_trades add column if not exists net_pnl numeric;
comment on column paper_trades.gross_pnl is 'Never shown outside the cost-analysis view. Every other P&L surface shows net_pnl.';

-- signals: every fired signal, approved or not (the unbiased evidence set)
alter table signals add column if not exists strategy_id text;
alter table signals add column if not exists band_id uuid references bands(band_id);
alter table signals add column if not exists side text;
alter table signals add column if not exists action text;
alter table signals add column if not exists reason text;
alter table signals add column if not exists price_at_fire numeric;
alter table signals add column if not exists prob_at_fire numeric;
alter table signals add column if not exists edge_at_fire numeric;
alter table signals add column if not exists suggested_shares numeric;
alter table signals add column if not exists confidence numeric;
alter table signals add column if not exists regime_label text;
alter table signals add column if not exists severity text check (severity in ('low','medium','high','critical'));
alter table signals add column if not exists dedupe_key text;
alter table signals add column if not exists payload jsonb;
alter table signals add column if not exists fired_at timestamptz default now();
alter table signals add column if not exists status text default 'pending_approval'
  check (status in ('pending_approval','filled','unfilled','dismissed'));
alter table signals add column if not exists city_key text;
alter table signals add column if not exists ttl interval;
create unique index if not exists idx_signals_dedupe on signals (dedupe_key, fired_at)
  where dedupe_key is not null;
create index if not exists idx_signals_severity_time on signals (severity, fired_at desc);

-- ledger: append-only chain, signal -> decision -> order -> fill -> mark -> exit|settlement
alter table ledger add column if not exists stage text
  check (stage in ('signal','decision','order','fill','mark','exit','settlement'));
alter table ledger add column if not exists strategy_id text;
alter table ledger add column if not exists deployment_id uuid;
alter table ledger add column if not exists band_id uuid references bands(band_id);
alter table ledger add column if not exists regime_label text;
alter table ledger add column if not exists forecast_version text;
alter table ledger add column if not exists calibration_version text;
alter table ledger add column if not exists cost_version text;
alter table ledger add column if not exists detail jsonb;
alter table ledger add column if not exists recorded_at timestamptz default now();
create index if not exists idx_ledger_time on ledger (recorded_at desc);
comment on table ledger is
  'Append-only. Forecast edge and microstructure edge are attributed separately, end to end - never merged into one composite score.';

-- settings additions this task needs
insert into settings (key, value) values
  ('auto_approve', '{"value": false, "origin":"hassan", "note":"off by default - signals await approval until Hassan turns this on"}'::jsonb)
on conflict (key) do nothing;
