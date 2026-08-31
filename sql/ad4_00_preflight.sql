-- ===========================================================================
-- AD4 SCHEMA PREFLIGHT  --  RUN THIS FIRST, BEFORE EVERY OTHER SQL FILE.
--
-- WHY THIS FILE EXISTS
-- --------------------
-- The base Phase 0 schema (ad4_schema.sql / ad4_functions*.sql) was applied
-- straight to Supabase before this repo existed and is not in the repo.
-- Every later file therefore used to *assume* a schema state it could not
-- see, and those assumptions were wrong in production (`strategies.universe`
-- and `strategies.capital_cap_pct` did not exist).
--
-- This file removes the assumption entirely. It guarantees, idempotently:
--   1. every TABLE any later file reads from or writes to exists;
--   2. every COLUMN any later file touches exists on it;
--   3. every UNIQUE/PK a later file's FK or ON CONFLICT depends on exists;
--   4. the Supabase-specific objects the later files use (`anon` role,
--      `supabase_realtime` publication, `log_ingest`) exist, so the same
--      files also run on a vanilla Postgres.
--
-- It NEVER drops, renames, retypes or deletes anything. It only adds what
-- is missing. Safe to run any number of times, in any order, against an
-- empty database or against the live one.
--
-- It finishes by RAISING NOTICE with the exact list of what it had to add,
-- so you can see whether your database was already complete.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- 0. Extensions. gen_random_uuid() is built in from Postgres 13; pgcrypto
--    is the fallback for older servers. Supabase already has both.
-- --------------------------------------------------------------------------
do $$
begin
  if not exists (select 1 from pg_proc where proname = 'gen_random_uuid') then
    begin
      create extension if not exists pgcrypto;
    exception when others then
      raise notice 'preflight: could not create pgcrypto (%). gen_random_uuid() must already exist.', sqlerrm;
    end;
  end if;
end $$;

-- --------------------------------------------------------------------------
-- 1. Supabase-specific objects the later files depend on.
--
--    On Supabase all three already exist and every branch below is a no-op.
--    On a plain Postgres (a local verification run, a self-hosted mirror)
--    they do not, and sql/ad4_rls.sql + sql/ad4_rpc.sql would fail on line
--    one. Creating them here is what makes the other files portable.
-- --------------------------------------------------------------------------
do $$
begin
  if not exists (select 1 from pg_roles where rolname = 'anon') then
    create role anon nologin noinherit;
    raise notice 'preflight: created role anon (was missing - normal outside Supabase)';
  end if;
  if not exists (select 1 from pg_roles where rolname = 'authenticated') then
    create role authenticated nologin noinherit;
  end if;
  if not exists (select 1 from pg_roles where rolname = 'service_role') then
    create role service_role nologin noinherit bypassrls;
  end if;
end $$;

do $$
begin
  if not exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    begin
      create publication supabase_realtime;
      raise notice 'preflight: created publication supabase_realtime (was missing - normal outside Supabase)';
    exception when insufficient_privilege then
      raise notice 'preflight: cannot create publication supabase_realtime (needs superuser). Realtime blocks in later files will skip themselves.';
    end;
  end if;
end $$;

-- --------------------------------------------------------------------------
-- 2. TABLES.
--
--    `create table if not exists` only - an existing table keeps whatever
--    shape it already has, and section 4 adds any columns it is missing.
--    Deliberately NO foreign keys here: the later files add the FKs they
--    actually need, and adding them here would make this file order- and
--    data-dependent for no gain.
-- --------------------------------------------------------------------------

-- ---- market universe ------------------------------------------------------
create table if not exists cities (
  city_key          text primary key,
  display_name      text,
  icao              text,
  station_name      text,
  timezone          text,
  unit              text default 'C',
  band_width        numeric,
  latitude          numeric,
  longitude         numeric,
  resolution_source text,
  status            text default 'active'
);

create table if not exists markets (
  market_id       uuid primary key default gen_random_uuid(),
  city_key        text,
  resolution_date date,
  unit            text,
  closed          boolean default false,
  event_slug      text,
  condition_id    text,
  rules_text      text,
  rules_fetched_at timestamptz,
  rules_changed_at timestamptz,
  created_at      timestamptz default now()
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

-- ---- market microstructure ------------------------------------------------
create table if not exists book_snapshots (
  snapshot_id  bigserial primary key,
  band_id      uuid,
  observed_at  timestamptz default now(),
  best_bid     numeric,
  best_ask     numeric,
  spread       numeric,
  market_state text,
  bid_levels   jsonb,
  ask_levels   jsonb
);

create table if not exists trades_observed (
  trade_id    bigserial primary key,
  city_key    text,
  band_id     uuid,
  token_id    text,
  side        text,
  price       numeric,
  size        numeric,
  observed_at timestamptz
);

-- ---- model output ---------------------------------------------------------
create table if not exists band_probabilities (
  prob_id             bigserial primary key,
  band_id             uuid,
  computed_at         timestamptz default now(),
  calibrated_prob     numeric,
  forecast_version    text,
  calibration_version text
);

create table if not exists model_versions (
  version_id  uuid primary key default gen_random_uuid(),
  kind        text,
  label       text,
  detail      jsonb,
  created_at  timestamptz default now()
);

-- ---- strategy + execution -------------------------------------------------
-- NOTE the 8 columns below are the CONFIRMED live production shape of
-- `strategies`. On the real database this create is a no-op and section 4
-- adds universe / regime_filter / capital_cap_pct / max_concurrent / extra.
create table if not exists strategies (
  strategy_id    text primary key,
  name           text,
  side           text,
  origin         text,
  config         jsonb,
  conflict_class text,
  enabled        boolean default false,
  created_at     timestamptz default now()
);

create table if not exists deployments (
  deployment_id uuid primary key default gen_random_uuid(),
  created_at    timestamptz default now()
);

create table if not exists signals (
  signal_id bigserial primary key,
  fired_at  timestamptz default now()
);

create table if not exists paper_trades (
  trade_id  bigserial primary key,
  opened_at timestamptz default now()
);

create table if not exists ledger (
  ledger_id   bigserial primary key,
  recorded_at timestamptz default now()
);

-- ---- backtest -------------------------------------------------------------
create table if not exists backtest_runs (
  run_id     uuid primary key default gen_random_uuid(),
  created_at timestamptz default now()
);

create table if not exists backtest_results (
  result_id bigserial primary key
);

create table if not exists backtest_trades (
  bt_trade_id bigserial primary key
);

-- ---- support tables -------------------------------------------------------
create table if not exists settings (
  key        text primary key,
  value      jsonb,
  updated_at timestamptz default now()
);

create table if not exists anomalies (
  anomaly_id  bigserial primary key,
  detected_at timestamptz default now(),
  band_id     uuid,
  kind        text,
  side        text,
  value       numeric,
  detail      jsonb,
  notified    boolean default false
);

create table if not exists ingest_log (
  log_id    bigserial primary key,
  job       text,
  status    text,
  rows      integer,
  detail    jsonb,
  logged_at timestamptz default now()
);

-- ---- weather --------------------------------------------------------------
create table if not exists weather_observations (
  obs_id      bigserial primary key,
  city_key    text,
  station     text,
  valid_at    timestamptz,
  temp_c      numeric,
  temp_f      numeric,
  dewpoint_c  numeric,
  humidity    numeric,
  wind_speed  numeric,
  wind_dir_deg numeric,
  precip      numeric,
  cloud_cover text,
  source      text
);

create table if not exists weather_forecasts (
  forecast_id    bigserial primary key,
  city_key       text,
  model          text,
  run_at         timestamptz,
  for_date       date,
  lead_days      int,
  forecast_max_c numeric,
  variables      jsonb,
  source         text
);

-- ---- derived --------------------------------------------------------------
create table if not exists derived_weather_peak (
  city_key        text not null,
  month           int  not null,
  peak_hour_local numeric,
  window_width_h  numeric,
  n_days          int,
  computed_at     timestamptz default now(),
  primary key (city_key, month)
);

create table if not exists derived_market_peak (
  city_key      text not null,
  month         int  not null,
  peak_hour_utc numeric,
  n_days        int,
  computed_at   timestamptz default now(),
  primary key (city_key, month)
);

create table if not exists derived_city_day_volume (
  city_key    text not null,
  trade_date  date not null,
  volume_usd  numeric,
  n_trades    int,
  computed_at timestamptz default now(),
  primary key (city_key, trade_date)
);

-- Band-level traded volume. Market volume is a first-class input across
-- AD4 (ranking, tradeability, calculator, goals) and city-day totals are
-- too coarse for it: a city can be busy while the specific band you want
-- to trade has never printed. Populated by refresh_derived() in
-- sql/ad4_rpc.sql from trades_observed.
create table if not exists derived_band_day_volume (
  band_id     uuid not null,
  city_key    text,
  trade_date  date not null,
  volume_usd  numeric,
  n_trades    int,
  computed_at timestamptz default now(),
  primary key (band_id, trade_date)
);

-- ---- Phase 1 (also created by ad4_phase1_tables.sql; harmless here) -------
create table if not exists derived_forecast_skill (
  city_key             text not null,
  computed_at          timestamptz not null default now(),
  lead_days            int not null,
  n_days               int,
  mae_c                numeric,
  bias_c               numeric,
  p90_abs_err_c        numeric,
  mae_bands            numeric,
  pct_within_one_band  numeric,
  band_width_c         numeric,
  primary key (city_key, computed_at, lead_days)
);

-- --------------------------------------------------------------------------
-- 3. COLUMNS.
--
--    Every column any of the 11 later files (or any scripts/*.py job that
--    writes through PostgREST) reads or writes, on a table it does not
--    itself create. Driven off a list rather than 200 hand-written ALTERs
--    so the verification block at the end can report exactly what was
--    missing rather than asserting it blindly.
--
--    `alter table ... add column if not exists` semantics, one row each:
--    (table, column, type-and-default). Nothing is ever retyped.
-- --------------------------------------------------------------------------
-- Session-scoped scratch table for the verification block at the end.
-- NOT `on commit drop`: the Supabase SQL editor and psql differ on whether
-- a multi-statement script is one transaction, and dropping it mid-file
-- would break section 8. Dropped explicitly at the end instead.
drop table if exists _ad4_preflight_added;
create temporary table _ad4_preflight_added (
  tbl text, col text, kind text
);

do $$
declare
  r record;
  n_added int := 0;
begin
  for r in
    select * from (values
      -- cities ------------------------------------------------------------
      ('cities','display_name','text'),
      ('cities','icao','text'),
      ('cities','station_name','text'),
      ('cities','timezone','text'),
      ('cities','unit','text'),
      ('cities','band_width','numeric'),
      ('cities','latitude','numeric'),
      ('cities','longitude','numeric'),
      ('cities','resolution_source','text'),
      ('cities','status','text'),

      -- markets -------------------------------------------------------------
      ('markets','city_key','text'),
      ('markets','resolution_date','date'),
      ('markets','unit','text'),
      ('markets','closed','boolean default false'),
      ('markets','event_slug','text'),
      ('markets','condition_id','text'),
      ('markets','settled_value','numeric'),
      ('markets','winning_band_id','uuid'),
      ('markets','resolution_verified_at','timestamptz'),
      ('markets','resolution_source_used','text'),
      ('markets','dispute_flag','boolean default false'),
      -- Written by the P0.5 Refresh Rules Text workflow. The rules say
      -- WHICH station and WHICH surface settles a market; if Polymarket
      -- edits that mid-life, every probability computed against the old
      -- station is measuring the wrong thing, and nothing else in the
      -- system would notice. rules_changed_at is stamped only when the
      -- text actually moves, so it means "when did the terms last change"
      -- rather than "when did we last poll".
      ('markets','rules_text','text'),
      ('markets','rules_fetched_at','timestamptz'),
      ('markets','rules_changed_at','timestamptz'),

      -- bands ---------------------------------------------------------------
      ('bands','market_id','uuid'),
      ('bands','band_lo','numeric'),
      ('bands','band_hi','numeric'),
      ('bands','open_low','boolean default false'),
      ('bands','open_high','boolean default false'),
      ('bands','band_label','text'),
      ('bands','token_yes','text'),
      ('bands','token_no','text'),

      -- book_snapshots ------------------------------------------------------
      ('book_snapshots','band_id','uuid'),
      ('book_snapshots','observed_at','timestamptz'),
      ('book_snapshots','best_bid','numeric'),
      ('book_snapshots','best_ask','numeric'),
      ('book_snapshots','spread','numeric'),
      ('book_snapshots','market_state','text'),
      ('book_snapshots','bid_levels','jsonb'),
      ('book_snapshots','ask_levels','jsonb'),

      -- trades_observed -----------------------------------------------------
      ('trades_observed','city_key','text'),
      ('trades_observed','band_id','uuid'),
      ('trades_observed','token_id','text'),
      ('trades_observed','side','text'),
      ('trades_observed','price','numeric'),
      ('trades_observed','size','numeric'),
      ('trades_observed','observed_at','timestamptz'),

      -- band_probabilities --------------------------------------------------
      ('band_probabilities','band_id','uuid'),
      ('band_probabilities','computed_at','timestamptz default now()'),
      ('band_probabilities','calibrated_prob','numeric'),
      ('band_probabilities','forecast_version','text'),
      ('band_probabilities','calibration_version','text'),
      ('band_probabilities','forecast_max_c','numeric'),
      ('band_probabilities','bias_applied_c','numeric'),
      ('band_probabilities','sigma_c','numeric'),
      ('band_probabilities','lead_days','int'),
      ('band_probabilities','lattice_applied','boolean default false'),
      ('band_probabilities','confidence','numeric'),
      ('band_probabilities','regime_label','text'),

      -- strategies ----------------------------------------------------------
      -- The two columns that actually failed in production, plus the three
      -- others scripts/strategies/base.py:StrategyConfig needs.
      ('strategies','name','text'),
      ('strategies','side','text'),
      ('strategies','origin','text'),
      ('strategies','config','jsonb'),
      ('strategies','conflict_class','text'),
      ('strategies','enabled','boolean default false'),
      ('strategies','created_at','timestamptz default now()'),
      ('strategies','universe','jsonb'),
      ('strategies','regime_filter','jsonb'),
      ('strategies','capital_cap_pct','numeric'),
      ('strategies','max_concurrent','int'),
      ('strategies','extra','jsonb'),

      -- deployments ---------------------------------------------------------
      ('deployments','name','text'),
      ('deployments','strategy_id','text'),
      ('deployments','target_kind','text'),
      ('deployments','target','jsonb'),
      ('deployments','status','text default ''draft'''),
      ('deployments','starts_at','timestamptz'),
      ('deployments','ends_at','timestamptz'),
      ('deployments','condition','text'),
      ('deployments','created_at','timestamptz default now()'),

      -- signals -------------------------------------------------------------
      ('signals','strategy_id','text'),
      ('signals','band_id','uuid'),
      ('signals','side','text'),
      ('signals','action','text'),
      ('signals','reason','text'),
      ('signals','price_at_fire','numeric'),
      ('signals','prob_at_fire','numeric'),
      ('signals','edge_at_fire','numeric'),
      ('signals','suggested_shares','numeric'),
      ('signals','confidence','numeric'),
      ('signals','regime_label','text'),
      ('signals','severity','text'),
      ('signals','dedupe_key','text'),
      ('signals','payload','jsonb'),
      ('signals','fired_at','timestamptz default now()'),
      ('signals','status','text default ''pending_approval'''),
      ('signals','city_key','text'),
      ('signals','ttl','interval'),

      -- paper_trades --------------------------------------------------------
      ('paper_trades','strategy_id','text'),
      ('paper_trades','band_id','uuid'),
      ('paper_trades','side','text'),
      ('paper_trades','action','text'),
      ('paper_trades','shares','numeric'),
      ('paper_trades','avg_fill_price','numeric'),
      ('paper_trades','quoted_price','numeric'),
      ('paper_trades','slippage_paid','numeric'),
      ('paper_trades','fee_paid','numeric'),
      ('paper_trades','gas_paid','numeric'),
      -- partial_fill / requested_shares are written by
      -- scripts/paper_engine.py:build_paper_trade_row and read by
      -- log_paper_trade() in sql/ad4_rpc.sql, but no earlier file ever
      -- added them. This is the class of gap this file exists to close.
      ('paper_trades','partial_fill','boolean default false'),
      ('paper_trades','requested_shares','numeric'),
      ('paper_trades','max_slippage_setting','numeric'),
      ('paper_trades','fill_quality','numeric'),
      ('paper_trades','legs_requested','int'),
      ('paper_trades','legs_filled','int'),
      ('paper_trades','approved_by_user','boolean default true'),
      ('paper_trades','cost_version','text'),
      ('paper_trades','forecast_version','text'),
      ('paper_trades','calibration_version','text'),
      ('paper_trades','regime_label','text'),
      ('paper_trades','opened_at','timestamptz'),
      ('paper_trades','closed_at','timestamptz'),
      ('paper_trades','exit_price','numeric'),
      ('paper_trades','gross_pnl','numeric'),
      ('paper_trades','net_pnl','numeric'),
      ('paper_trades','deployment_id','uuid'),

      -- ledger --------------------------------------------------------------
      ('ledger','stage','text'),
      ('ledger','strategy_id','text'),
      ('ledger','deployment_id','uuid'),
      ('ledger','band_id','uuid'),
      ('ledger','regime_label','text'),
      ('ledger','forecast_version','text'),
      ('ledger','calibration_version','text'),
      ('ledger','cost_version','text'),
      ('ledger','detail','jsonb'),
      ('ledger','recorded_at','timestamptz default now()'),

      -- backtest ------------------------------------------------------------
      ('backtest_runs','run_id','uuid default gen_random_uuid()'),
      ('backtest_runs','params','jsonb'),
      ('backtest_runs','status','text'),
      ('backtest_runs','created_at','timestamptz default now()'),
      ('backtest_runs','started_at','timestamptz'),
      ('backtest_runs','finished_at','timestamptz'),
      ('backtest_runs','error','text'),
      ('backtest_runs','label','text'),
      ('backtest_results','run_id','uuid'),
      ('backtest_results','scope','text'),
      ('backtest_results','data','jsonb'),
      ('backtest_trades','run_id','uuid'),
      ('backtest_trades','strategy_id','text'),
      ('backtest_trades','city_key','text'),
      ('backtest_trades','band_id','uuid'),
      ('backtest_trades','side','text'),
      ('backtest_trades','shares','numeric'),
      ('backtest_trades','avg_fill_price','numeric'),
      ('backtest_trades','gross_pnl','numeric'),
      ('backtest_trades','net_pnl','numeric'),
      ('backtest_trades','fee_paid','numeric'),
      ('backtest_trades','gas_paid','numeric'),
      ('backtest_trades','slippage_paid','numeric'),
      ('backtest_trades','model_prob','numeric'),
      ('backtest_trades','won','boolean'),
      ('backtest_trades','resolution_date','date'),
      ('backtest_trades','regime_label','text'),
      -- written by scripts/backtest/engine.py, missing from ad4_backtest.sql
      ('backtest_trades','legs_requested','int'),
      ('backtest_trades','legs_filled','int'),

      -- settings / anomalies / ingest_log ------------------------------------
      ('settings','value','jsonb'),
      ('settings','updated_at','timestamptz default now()'),
      ('anomalies','detected_at','timestamptz default now()'),
      ('anomalies','band_id','uuid'),
      ('anomalies','kind','text'),
      ('anomalies','side','text'),
      ('anomalies','value','numeric'),
      ('anomalies','detail','jsonb'),
      ('anomalies','notified','boolean default false'),
      ('ingest_log','job','text'),
      ('ingest_log','status','text'),
      ('ingest_log','rows','integer'),
      ('ingest_log','detail','jsonb'),
      ('ingest_log','logged_at','timestamptz default now()'),

      -- weather --------------------------------------------------------------
      ('weather_observations','station','text'),
      ('weather_observations','valid_at','timestamptz'),
      ('weather_observations','temp_c','numeric'),
      ('weather_observations','temp_f','numeric'),
      ('weather_observations','dewpoint_c','numeric'),
      ('weather_observations','humidity','numeric'),
      ('weather_observations','wind_speed','numeric'),
      ('weather_observations','wind_dir_deg','numeric'),
      ('weather_observations','precip','numeric'),
      ('weather_observations','cloud_cover','text'),
      ('weather_observations','source','text'),
      ('weather_observations','sky_condition','text'),
      ('weather_observations','sky_raw','text'),
      ('weather_observations','visibility_m','numeric'),
      ('weather_observations','pressure_hpa','numeric'),
      ('weather_forecasts','model','text'),
      ('weather_forecasts','run_at','timestamptz'),
      ('weather_forecasts','for_date','date'),
      ('weather_forecasts','lead_days','int'),
      ('weather_forecasts','forecast_max_c','numeric'),
      ('weather_forecasts','variables','jsonb'),
      ('weather_forecasts','source','text'),

      -- derived ---------------------------------------------------------------
      ('derived_weather_peak','peak_hour_local','numeric'),
      ('derived_weather_peak','window_width_h','numeric'),
      ('derived_weather_peak','n_days','int'),
      ('derived_weather_peak','computed_at','timestamptz default now()'),
      ('derived_market_peak','peak_hour_utc','numeric'),
      ('derived_market_peak','n_days','int'),
      ('derived_market_peak','computed_at','timestamptz default now()'),
      ('derived_city_day_volume','volume_usd','numeric'),
      ('derived_city_day_volume','n_trades','int'),
      ('derived_city_day_volume','computed_at','timestamptz default now()'),
      ('derived_band_day_volume','city_key','text'),
      ('derived_band_day_volume','volume_usd','numeric'),
      ('derived_band_day_volume','n_trades','int'),
      ('derived_band_day_volume','computed_at','timestamptz default now()'),
      ('derived_forecast_skill','n_days','int'),
      ('derived_forecast_skill','mae_c','numeric'),
      ('derived_forecast_skill','bias_c','numeric'),
      ('derived_forecast_skill','p90_abs_err_c','numeric'),
      ('derived_forecast_skill','mae_bands','numeric'),
      ('derived_forecast_skill','pct_within_one_band','numeric'),
      ('derived_forecast_skill','band_width_c','numeric')
    ) as t(tbl, col, def)
  loop
    if to_regclass('public.' || quote_ident(r.tbl)) is null then
      raise notice 'preflight: table % missing and not created here - skipping its columns', r.tbl;
      continue;
    end if;
    if not exists (
      select 1 from information_schema.columns
      where table_schema = 'public' and table_name = r.tbl and column_name = r.col
    ) then
      execute format('alter table public.%I add column %I %s', r.tbl, r.col, r.def);
      insert into _ad4_preflight_added values (r.tbl, r.col, 'column');
      n_added := n_added + 1;
    end if;
  end loop;
  raise notice 'preflight: % missing column(s) added', n_added;
end $$;

-- --------------------------------------------------------------------------
-- 4. UNIQUE / PRIMARY KEY guarantees.
--
--    Later files declare foreign keys (edges.band_id -> bands.band_id,
--    edges.prob_id -> band_probabilities.prob_id, ...) and use ON CONFLICT
--    (strategy_id) / (key) / (rule_id). Both need a unique index on the
--    referenced column. If the live table has the column but no unique
--    index on it, those statements fail - so ensure one, unless existing
--    duplicate data makes that impossible (in which case: report, don't
--    crash, and let the operator decide).
-- --------------------------------------------------------------------------
do $$
declare
  r record;
  v_idx_name text;
begin
  for r in
    select * from (values
      ('cities','city_key'),
      ('markets','market_id'),
      ('bands','band_id'),
      ('book_snapshots','snapshot_id'),
      ('band_probabilities','prob_id'),
      ('strategies','strategy_id'),
      ('settings','key')
    ) as t(tbl, col)
  loop
    if to_regclass('public.' || quote_ident(r.tbl)) is null then continue; end if;
    if not exists (
      select 1 from information_schema.columns
      where table_schema='public' and table_name=r.tbl and column_name=r.col
    ) then
      raise notice 'preflight: %.% does not exist - cannot ensure uniqueness', r.tbl, r.col;
      continue;
    end if;
    -- any unique index whose key is exactly this one column
    if exists (
      select 1
      from pg_index i
      join pg_class c on c.oid = i.indrelid
      join pg_namespace n on n.oid = c.relnamespace
      join pg_attribute a on a.attrelid = c.oid and a.attnum = i.indkey[0]
      where n.nspname = 'public' and c.relname = r.tbl
        and i.indisunique and i.indnatts = 1 and a.attname = r.col
    ) then
      continue;
    end if;
    v_idx_name := 'ad4_uq_' || r.tbl || '_' || r.col;
    begin
      execute format('create unique index if not exists %I on public.%I (%I)', v_idx_name, r.tbl, r.col);
      insert into _ad4_preflight_added values (r.tbl, r.col, 'unique index');
      raise notice 'preflight: added unique index on %.%', r.tbl, r.col;
    exception when others then
      raise notice 'preflight: COULD NOT make %.% unique (%) - later FKs/ON CONFLICT on it will fail. Resolve the duplicates, then re-run this file.', r.tbl, r.col, sqlerrm;
    end;
  end loop;
end $$;

-- --------------------------------------------------------------------------
-- 5. Composite uniques the Python ingest jobs upsert against.
--    scripts/ingest_observations.py upserts on (city_key, valid_at, source);
--    scripts/ingest_forecasts.py on (city_key, model, run_at, for_date).
--    PostgREST needs a matching unique constraint or every upsert 400s.
-- --------------------------------------------------------------------------
do $$
begin
  if to_regclass('public.weather_observations') is not null then
    begin
      create unique index if not exists ad4_uq_wx_obs
        on public.weather_observations (city_key, valid_at, source);
    exception when others then
      raise notice 'preflight: could not add weather_observations unique (city_key,valid_at,source): %', sqlerrm;
    end;
  end if;
  if to_regclass('public.weather_forecasts') is not null then
    begin
      create unique index if not exists ad4_uq_wx_fc
        on public.weather_forecasts (city_key, model, run_at, for_date);
    exception when others then
      raise notice 'preflight: could not add weather_forecasts unique (city_key,model,run_at,for_date): %', sqlerrm;
    end;
  end if;
end $$;

-- --------------------------------------------------------------------------
-- 6. log_ingest(). scripts/common.py:log_run() POSTs to this RPC after
--    every job. It lives in the base ad4_functions*.sql that is not in this
--    repo, so create it only if it is genuinely absent - never replace a
--    working production version with this one.
-- --------------------------------------------------------------------------
do $$
begin
  if not exists (
    select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
    where n.nspname = 'public' and p.proname = 'log_ingest'
  ) then
    execute $fn$
      create function log_ingest(p_job text, p_status text, p_rows int, p_detail jsonb)
      returns void language plpgsql security definer as $body$
      begin
        insert into ingest_log (job, status, "rows", detail, logged_at)
        values (p_job, p_status, p_rows, p_detail, now());
      end;
      $body$;
    $fn$;
    raise notice 'preflight: created log_ingest() (was missing)';
  end if;
end $$;

-- --------------------------------------------------------------------------
-- 7. Market-volume settings.
--
--    Market volume is an input everywhere in AD4 (ranking, tradeability,
--    calculator, goals). The thresholds below are PROVISIONAL Claude
--    placeholders with NO evidential basis, UI-settable, and labelled as
--    such exactly like every other provisional value in this schema.
--    Nothing here asserts what a "thin" market is - it states where the
--    number the UI uses comes from, so it can be replaced with a measured
--    one once AD4's own trades_observed history is long enough to measure.
-- --------------------------------------------------------------------------
insert into settings (key, value) values
  ('volume_thresholds', '{
     "thin_band_usd_24h": 500,
     "thin_city_usd_24h": 5000,
     "liquidity_half_saturation_usd": 2000,
     "lookback_hours": 24,
     "provisional": true, "origin": "claude_invented",
     "note": "NO evidential basis. thin_* only drive a UI warning label; liquidity_half_saturation_usd is the k in the saturating volume factor vol/(vol+k) used to rank opportunities - it discounts illiquid markets, it never inflates a liquid one. Replace with measured values once trades_observed has enough history."
   }'::jsonb)
on conflict (key) do nothing;

-- ===========================================================================
-- 8. VERIFICATION - prints exactly what this run had to add, then a full
--    inventory of every table/column it guarantees. Read the NOTICE output
--    in the Supabase SQL editor's "Messages" tab.
-- ===========================================================================
do $$
declare
  r record;
  n int;
begin
  select count(*) into n from _ad4_preflight_added;
  raise notice '=====================================================';
  raise notice 'AD4 PREFLIGHT COMPLETE';
  raise notice '=====================================================';
  if n = 0 then
    raise notice 'Nothing was missing - the database already had every table, column and unique key the other 11 files need.';
  else
    raise notice '% object(s) had to be added:', n;
    for r in select tbl, col, kind from _ad4_preflight_added order by kind, tbl, col loop
      raise notice '  + %  %.%', rpad(r.kind, 13), r.tbl, r.col;
    end loop;
  end if;
  raise notice '-----------------------------------------------------';
  raise notice 'Tables guaranteed to exist (name: column count):';
  for r in
    select c.relname as tbl, count(a.attname) as ncols
    from pg_class c
    join pg_namespace nsp on nsp.oid = c.relnamespace
    join pg_attribute a on a.attrelid = c.oid and a.attnum > 0 and not a.attisdropped
    where nsp.nspname = 'public' and c.relkind = 'r'
      and c.relname in (
        'cities','markets','bands','book_snapshots','trades_observed',
        'band_probabilities','model_versions','strategies','deployments',
        'signals','paper_trades','ledger','backtest_runs','backtest_results',
        'backtest_trades','settings','anomalies','ingest_log',
        'weather_observations','weather_forecasts','derived_weather_peak',
        'derived_market_peak','derived_city_day_volume','derived_band_day_volume',
        'derived_forecast_skill')
    group by c.relname order by c.relname
  loop
    raise notice '  %  %', rpad(r.tbl, 26), r.ncols;
  end loop;
  raise notice '-----------------------------------------------------';
  raise notice 'Next: run ad4_phase1_tables.sql, then sql/ad4_phase2.sql, then the rest in README order.';
end $$;

drop table if exists _ad4_preflight_added;
