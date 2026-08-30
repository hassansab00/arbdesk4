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
create table if not exists markets (
  market_id       uuid primary key default gen_random_uuid(),
  city_key        text,
  resolution_date date,
  unit            text,
  closed          boolean default false,
  event_slug      text,
  condition_id    text
);
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
      ('markets','market_id'),
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
--    every column settle_markets() touches at run time
  for r in
    select * from (values
      ('markets','closed','boolean default false'),
      ('bands','market_id','uuid'),
      ('settings','value','jsonb'),
      ('paper_trades','band_id','uuid'),
      ('paper_trades','side','text'),
      ('paper_trades','shares','numeric'),
      ('paper_trades','avg_fill_price','numeric'),
      ('paper_trades','fee_paid','numeric'),
      ('paper_trades','gas_paid','numeric'),
      ('paper_trades','strategy_id','text'),
      ('paper_trades','opened_at','timestamptz'),
      ('paper_trades','closed_at','timestamptz'),
      ('paper_trades','exit_price','numeric'),
      ('paper_trades','gross_pnl','numeric'),
      ('paper_trades','net_pnl','numeric'),
      ('paper_trades','regime_label','text'),
      ('paper_trades','forecast_version','text'),
      ('paper_trades','calibration_version','text'),
      ('paper_trades','cost_version','text'),
      ('ledger','stage','text'),
      ('ledger','strategy_id','text'),
      ('ledger','band_id','uuid'),
      ('ledger','regime_label','text'),
      ('ledger','forecast_version','text'),
      ('ledger','calibration_version','text'),
      ('ledger','cost_version','text'),
      ('ledger','detail','jsonb'),
      ('ledger','recorded_at','timestamptz default now()')
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
-- Task 11 - settlement RPC + supporting columns.
-- scripts/settlement.py does the external fetch-and-verify (Postgres can't
-- make outbound HTTP calls); this RPC does the atomic DB-side write once a
-- settlement value has been verified. Genuine SQL, not a stub - see
-- docs/architecture_deviations.md for which Task 11 pieces are real vs why.
-- ===========================================================================

alter table markets add column if not exists settled_value numeric;
alter table markets add column if not exists winning_band_id uuid references bands(band_id);
alter table markets add column if not exists resolution_verified_at timestamptz;
alter table markets add column if not exists resolution_source_used text;
alter table markets add column if not exists dispute_flag boolean default false;

insert into settings (key, value) values
  ('settlement_verified', '{"value": false, "origin":"claude_invented", "note":"Gates auto-settlement. Set true only after docs/settlement_verification.md spot-check passes with real network access - the resolution-source parser in scripts/settlement.py has not been confirmed against a live page from this build session (sandboxed, no weather.gov egress)."}'::jsonb)
on conflict (key) do nothing;

create or replace function settle_markets(
  p_market_id uuid, p_settled_value numeric, p_winning_band_id uuid,
  p_source text, p_verified_at timestamptz
) returns integer language plpgsql security definer as $$
declare
  v_trade record;
  v_exit_price numeric;
  v_gross numeric;
  v_net numeric;
  v_count integer := 0;
begin
  update markets
  set closed = true, settled_value = p_settled_value, winning_band_id = p_winning_band_id,
      resolution_verified_at = p_verified_at, resolution_source_used = p_source
  where market_id = p_market_id;

  for v_trade in
    select pt.* from paper_trades pt
    join bands b on b.band_id = pt.band_id
    where b.market_id = p_market_id and pt.closed_at is null
  loop
    v_exit_price := case
      when v_trade.band_id = p_winning_band_id then (case when v_trade.side = 'YES' then 1.0 else 0.0 end)
      else (case when v_trade.side = 'YES' then 0.0 else 1.0 end)
    end;
    v_gross := v_trade.shares * (v_exit_price - v_trade.avg_fill_price);
    -- Settlement itself is free (fee curve -> 0 at p=0/p=1); net P&L only
    -- carries costs already paid at entry (fee_paid/gas_paid), never a
    -- second charge here.
    v_net := v_gross - coalesce(v_trade.fee_paid, 0) - coalesce(v_trade.gas_paid, 0);

    update paper_trades
    set closed_at = now(), exit_price = v_exit_price, gross_pnl = v_gross, net_pnl = v_net
    where paper_trades.strategy_id = v_trade.strategy_id and paper_trades.band_id = v_trade.band_id
      and paper_trades.opened_at = v_trade.opened_at;

    insert into ledger (stage, strategy_id, band_id, regime_label, forecast_version,
                         calibration_version, cost_version, detail, recorded_at)
    values ('settlement', v_trade.strategy_id, v_trade.band_id, v_trade.regime_label,
            v_trade.forecast_version, v_trade.calibration_version, v_trade.cost_version,
            jsonb_build_object('exit_price', v_exit_price, 'gross_pnl', v_gross, 'net_pnl', v_net,
                                'settled_value', p_settled_value, 'source', p_source),
            now());

    v_count := v_count + 1;
  end loop;

  return v_count;
end;
$$;

-- Thin compatibility stub for Revision A's n8n `evaluate_signals`/
-- `compute_edges` naming (§7.6/§7.7) - the real implementations are
-- scripts/edge_engine.py and scripts/signals.py (Python), run via GitHub
-- Actions. See docs/architecture_deviations.md.
create or replace function compute_edges() returns void language plpgsql as $$
begin
  raise notice 'compute_edges: no-op - edges are computed by scripts/edge_engine.py via GitHub Actions, not this RPC. See docs/architecture_deviations.md.';
end;
$$;

create or replace function evaluate_signals() returns void language plpgsql as $$
begin
  raise notice 'evaluate_signals: no-op - signals are evaluated by scripts/signals.py via GitHub Actions, not this RPC. See docs/architecture_deviations.md.';
end;
$$;
