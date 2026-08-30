-- ---------------------------------------------------------------------------
-- SELF-SUFFICIENCY GUARD (added by the final completion pass).
--
-- This file no longer assumes any prior schema state. Everything it reads
-- or writes below is created here if absent, so it runs standalone against
-- the live Supabase database, a fresh Postgres, or a half-migrated one.
-- sql/ad4_00_preflight.sql does the same job for the whole system at once
-- and should still be run first - this block is the belt to its braces.
-- Idempotent: only ever ADDS, never drops, renames or retypes.
-- ---------------------------------------------------------------------------
create table if not exists bands (
  band_id    uuid primary key default gen_random_uuid(),
  market_id  uuid,
  band_lo    numeric,
  band_hi    numeric,
  open_low   boolean default false,
  open_high  boolean default false,
  band_label text,
  token_yes  text,
  token_no   text
);
create table if not exists paper_trades (
  trade_id  bigserial primary key,
  opened_at timestamptz default now()
);
create table if not exists signals (
  signal_id bigserial primary key,
  fired_at  timestamptz default now()
);
create table if not exists ledger (
  ledger_id   bigserial primary key,
  recorded_at timestamptz default now()
);
create table if not exists settings (
  key        text primary key,
  value      jsonb,
  updated_at timestamptz default now()
);

do $$
declare r record;
begin
  for r in select * from (values
      ('bands','band_id'),
      ('settings','key')
  ) as t(tbl, col) loop
    if to_regclass('public.' || quote_ident(r.tbl)) is null then continue; end if;
    if exists (select 1 from pg_index i
               join pg_class c on c.oid = i.indrelid
               join pg_namespace n on n.oid = c.relnamespace
               join pg_attribute a on a.attrelid = c.oid and a.attnum = i.indkey[0]
               where n.nspname='public' and c.relname=r.tbl
                 and i.indisunique and i.indnatts = 1 and a.attname = r.col) then
      continue;
    end if;
    begin
      execute format('create unique index if not exists %I on public.%I (%I)',
                     'ad4_uq_' || r.tbl || '_' || r.col, r.tbl, r.col);
    exception when others then
      raise notice 'guard: could not make %.% unique: %', r.tbl, r.col, sqlerrm;
    end;
  end loop;
end $$;

do $$
declare r record;
begin
  for r in
    select * from (values
      ('settings','value','jsonb')
    ) as t(tbl, col, def)
  loop
    if to_regclass('public.' || quote_ident(r.tbl)) is null then continue; end if;
    if not exists (select 1 from information_schema.columns
                   where table_schema='public' and table_name=r.tbl and column_name=r.col) then
      execute format('alter table public.%I add column %I %s', r.tbl, r.col, r.def);
      raise notice 'guard: added %.%', r.tbl, r.col;
    end if;
  end loop;
end $$;

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
