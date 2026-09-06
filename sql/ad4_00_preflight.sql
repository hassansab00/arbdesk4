-- ===========================================================================
-- AD4 SCHEMA PREFLIGHT  --  RUN THIS FIRST, BEFORE EVERY OTHER SQL FILE.
--
-- v4 - THE WHOLE FILE IS ONE SINGLE SQL STATEMENT.
--
-- Everything below sits inside one `do $ad4$ ... $ad4$;` block. That is
-- deliberate and it is the point of this version:
--
--   * one statement cannot be split by an editor;
--   * one statement cannot be handed to a different connection halfway;
--   * one statement cannot half-run - it either completes or rolls back
--     entirely, leaving the database exactly as it was.
--
-- Two earlier versions failed on the live Supabase SQL editor with
-- `relation "_ad4_preflight_added" does not exist`, because they kept a
-- scratch table alive across statement boundaries. There are no statement
-- boundaries left to cross.
--
-- WHY THIS FILE EXISTS
-- --------------------
-- The base Phase 0 schema (ad4_schema.sql / ad4_functions*.sql) was applied
-- straight to Supabase before this repo existed and is not in the repo.
-- Every later file therefore used to *assume* a schema state it could not
-- see, and those assumptions were wrong in production (`strategies.universe`
-- and `strategies.capital_cap_pct` did not exist).
--
-- This file removes the assumption. It guarantees, idempotently:
--   1. every TABLE any later file reads from or writes to exists;
--   2. every COLUMN any later file touches exists on it;
--   3. every UNIQUE/PK a later file's FK or ON CONFLICT depends on exists;
--   4. the Supabase-specific objects the later files use (`anon` role,
--      `supabase_realtime` publication, `log_ingest`) exist, so the same
--      files also run on a vanilla Postgres.
--
-- It NEVER drops, renames, retypes or deletes anything. It only adds what
-- is missing. Safe to run any number of times.
--
-- Read the result in the SQL editor's "Messages" tab. The first line tells
-- you which version ran - if it does not say v4, you pasted a stale copy.
-- ===========================================================================

do $ad4$
declare
  r          record;
  v_added    text[] := '{}';   -- 'kind|table|column', one per object added
  v_idx_name text;
  n_added    int := 0;
  n          int;
begin
  raise notice '=====================================================';
  raise notice 'AD4 PREFLIGHT  v5  (entire file = one statement)';
  raise notice '=====================================================';

  -- ==========================================================================
  -- 0. EXTENSIONS
  -- ==========================================================================
    if not exists (select 1 from pg_proc where proname = 'gen_random_uuid') then
      begin
        create extension if not exists pgcrypto;
      exception when others then
        raise notice 'preflight: could not create pgcrypto (%). gen_random_uuid() must already exist.', sqlerrm;
      end;
    end if;

  -- ==========================================================================
  -- 1. SUPABASE-SPECIFIC OBJECTS (no-ops on a real Supabase project)
  -- ==========================================================================
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
    if not exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
      begin
        create publication supabase_realtime;
        raise notice 'preflight: created publication supabase_realtime (was missing - normal outside Supabase)';
      exception when insufficient_privilege then
        raise notice 'preflight: cannot create publication supabase_realtime (needs superuser). Realtime blocks in later files will skip themselves.';
      end;
    end if;

  -- ==========================================================================
  -- 2. TABLES.  `create table if not exists` only - an existing table keeps
  --    whatever shape it has, and section 3 adds any columns it is missing.
  -- ==========================================================================
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
    cloud_cover numeric,   -- oktas 0-8, converted from the METAR sky-cover code
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

  -- ==========================================================================
  -- 3 + 4 + 8. COLUMNS, UNIQUE KEYS, AND THE REPORT
  -- ==========================================================================
    -- ======================= COLUMNS =======================
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
        -- NOTE: on the real Phase 0 database bid_levels/ask_levels are
        -- integer LEVEL COUNTS, not ladders - these two adds are no-ops
        -- there. The ladder lives in raw_book, with pre-aggregated depth
        -- in the *_usd_*c columns below. sql/ad4_13_reconcile.sql builds
        -- v_band_book to normalise whichever of them a database has.
        ('book_snapshots','bid_levels','jsonb'),
        ('book_snapshots','ask_levels','jsonb'),
        ('book_snapshots','raw_book','jsonb'),
        ('book_snapshots','mid','numeric'),
        ('book_snapshots','tradeable','boolean'),
        ('book_snapshots','snapshot_hour_utc','smallint'),
        ('book_snapshots','ask_usd_1c','numeric'),
        ('book_snapshots','bid_usd_1c','numeric'),
        ('book_snapshots','ask_usd_2c','numeric'),
        ('book_snapshots','bid_usd_2c','numeric'),
        ('book_snapshots','ask_usd_5c','numeric'),
        ('book_snapshots','bid_usd_5c','numeric'),
        ('book_snapshots','ask_usd_10c','numeric'),
        ('book_snapshots','bid_usd_10c','numeric'),
        ('book_snapshots','ask_usd_25c','numeric'),
        ('book_snapshots','bid_usd_25c','numeric'),
        ('book_snapshots','ask_total_usd','numeric'),
        ('book_snapshots','bid_total_usd','numeric'),
        ('book_snapshots','band_volume','numeric'),
        ('book_snapshots','band_volume_24hr','numeric'),

        -- trades_observed -----------------------------------------------------
        ('trades_observed','city_key','text'),
        ('trades_observed','band_id','uuid'),
        ('trades_observed','token_id','text'),
        ('trades_observed','side','text'),
        ('trades_observed','price','numeric'),
        ('trades_observed','size','numeric'),
        -- traded_at is the column the real ingest populates; observed_at
        -- is this file's own addition and is NULL on every existing row.
        -- Everything downstream coalesces across both - see
        -- ad4_trades_ts_expr() in sql/ad4_13_reconcile.sql.
        ('trades_observed','observed_at','timestamptz'),
        ('trades_observed','traded_at','timestamptz'),
        ('trades_observed','condition_id','text'),
        ('trades_observed','proxy_wallet','text'),
        ('trades_observed','ingested_at','timestamptz'),

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
        -- numeric on the real database: IEM's skyc1 is a METAR CODE (FEW/SCT/
        -- BKN/OVC) and scripts/ingest_observations.py converts it to oktas.
        ('weather_observations','cloud_cover','numeric'),
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

        -- COLUMNS THE PRODUCTION DATABASE HAS AND THIS FILE DID NOT CREATE.
        --
        -- Found by installing every SQL file in order onto an empty database
        -- and diffing the result against the live schema: fifty-odd columns
        -- existed only because an earlier hand-run migration put them there.
        -- Two consequences, and the second is the serious one:
        --
        --   sql/ad4_31 selected bands.band_index and failed to install at all
        --   on a clean database, which is a Predictive page that is red for a
        --   reason no error message names.
        --
        --   scripts/probability_engine.py WRITES band_probabilities.raw_prob
        --   and scripts/databank.py READS it, and nothing here created it. A
        --   rebuild from these files produces a schema the desk's own jobs
        --   cannot write to - which is the difference between a repo that
        --   documents a database and one that can restore it.
        --
        -- Only columns this repo's own code reads or writes are listed. Adding
        -- a column is idempotent and cannot change one that already exists, so
        -- this is safe on the live database and does nothing there.
        ('band_probabilities','raw_prob','numeric'),
        ('bands','band_index','int'),
        ('bands','condition_id','text'),
        ('markets','first_seen_at','timestamptz'),
        ('markets','last_seen_at','timestamptz'),
        ('markets','title','text'),
        ('markets','band_count','int'),
        ('markets','resolved_band_id','uuid'),
        ('markets','settled_at','timestamptz'),
        ('markets','settlement_source','text'),
        ('signals','approved','boolean'),
        ('signals','acted_on','boolean'),
        ('signals','category','text'),
        ('signals','book_snapshot_id','bigint'),
        ('signals','deployment_id','uuid'),
        ('paper_trades','close_price','numeric'),
        ('paper_trades','close_reason','text'),
        ('paper_trades','signal_id','bigint'),
        ('anomalies','city_key','text'),
        ('anomalies','severity','text'),
        ('model_versions','structural','boolean'),
        -- Integer LEVEL COUNTS, not the ladders. The ladder itself lives in
        -- book_snapshots.raw_book and is unpacked by ad4_raw_book_side();
        -- naming these the same thing has confused two readers already, so
        -- the comment stays with the columns.
        ('book_snapshots','bid_levels','int'),
        ('book_snapshots','ask_levels','int'),

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
        v_added := v_added || ('column|' || r.tbl || '|' || r.col);
        n_added := n_added + 1;
      end if;
    end loop;
    raise notice 'preflight: % missing column(s) added', n_added;

    -- ======================= UNIQUE / PRIMARY KEYS =======================
    -- Later files declare foreign keys (edges.band_id -> bands.band_id,
    -- edges.prob_id -> band_probabilities.prob_id, ...) and use ON CONFLICT
    -- (strategy_id) / (key) / (rule_id). Both need a unique index on the
    -- referenced column. If the live table has the column but no unique index
    -- on it, those statements fail - so ensure one, unless existing duplicate
    -- data makes that impossible (in which case: report, don't crash, and let
    -- the operator decide).
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
        v_added := v_added || ('unique index|' || r.tbl || '|' || r.col);
        raise notice 'preflight: added unique index on %.%', r.tbl, r.col;
      exception when others then
        raise notice 'preflight: COULD NOT make %.% unique (%) - later FKs/ON CONFLICT on it will fail. Resolve the duplicates, then re-run this file.', r.tbl, r.col, sqlerrm;
      end;
    end loop;

    -- ======================= VERIFICATION REPORT =======================
    -- Read this in the Supabase SQL editor's "Messages" tab.
    n := coalesce(array_length(v_added, 1), 0);
    raise notice '=====================================================';
    raise notice 'AD4 PREFLIGHT COMPLETE';
    raise notice '=====================================================';
    if n = 0 then
      raise notice 'Nothing was missing - the database already had every table, column and unique key the other 11 files need.';
    else
      raise notice '% object(s) had to be added:', n;
      for r in
        select split_part(x, '|', 1) as kind,
               split_part(x, '|', 2) as tbl,
               split_part(x, '|', 3) as col
        from unnest(v_added) x
        order by 1, 2, 3
      loop
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

  -- ==========================================================================
  -- 5. COMPOSITE UNIQUES the Python ingest jobs upsert against
  -- ==========================================================================
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

  -- ==========================================================================
  -- 6. log_ingest() - scripts/common.py:log_run() POSTs to this after every job
  -- ==========================================================================
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

  -- ==========================================================================
  -- 7. MARKET-VOLUME SETTINGS.  PROVISIONAL placeholders with NO evidential
  --    basis, UI-settable, labelled as such - same contract as every other
  --    provisional value in this schema.
  -- ==========================================================================
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

  raise notice '-----------------------------------------------------';
  raise notice 'Next: run ad4_phase1_tables.sql, then sql/ad4_phase2.sql, then the rest in README order.';
end
$ad4$;
