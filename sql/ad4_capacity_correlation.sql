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
create table if not exists weather_observations (
  obs_id       bigserial primary key,
  city_key     text,
  station      text,
  valid_at     timestamptz,
  temp_c       numeric,
  temp_f       numeric,
  dewpoint_c   numeric,
  humidity     numeric,
  wind_speed   numeric,
  wind_dir_deg numeric,
  precip       numeric,
  cloud_cover  text,
  source       text
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

do $$
declare r record;
begin
--    columns recompute_capacity() and recompute_correlation() read at run time
  for r in
    select * from (values
      ('book_snapshots','band_id','uuid'),
      ('book_snapshots','observed_at','timestamptz default now()'),
      ('book_snapshots','best_bid','numeric'),
      ('book_snapshots','best_ask','numeric'),
      ('book_snapshots','market_state','text'),
      ('book_snapshots','bid_levels','jsonb'),
      ('book_snapshots','ask_levels','jsonb'),
      ('bands','market_id','uuid'),
      ('markets','city_key','text'),
      ('weather_observations','city_key','text'),
      ('weather_observations','valid_at','timestamptz'),
      ('weather_observations','temp_c','numeric'),
      ('weather_forecasts','city_key','text'),
      ('weather_forecasts','for_date','date'),
      ('weather_forecasts','lead_days','int'),
      ('weather_forecasts','forecast_max_c','numeric')
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
-- Task 7 - capacity and correlation, as Postgres RPCs.
-- Run after sql/ad4_phase2.sql.
--
-- Both are genuine SQL implementations (not stubs - see
-- docs/architecture_deviations.md for which Task 7 RPCs are real vs.
-- placeholders): capacity is a GROUP BY over book_snapshots depth,
-- correlation of forecast ERRORS uses Postgres's own corr() aggregate.
--
-- SCHEMA ASSUMPTION: book_snapshots.bid_levels / ask_levels are jsonb
-- arrays of {"price": numeric, "size": numeric} - same assumption as
-- scripts/edge_engine.py, documented in docs/schema_assumptions.md.
-- ===========================================================================

create or replace function depth_usd(levels jsonb) returns numeric
language sql immutable as $$
  select coalesce(sum((l->>'price')::numeric * (l->>'size')::numeric), 0)
  from jsonb_array_elements(coalesce(levels, '[]'::jsonb)) l;
$$;

create or replace function capacity_side(levels jsonb, touch numeric, cap numeric, is_ask boolean)
returns numeric language sql immutable as $$
  select coalesce(sum((l->>'price')::numeric * (l->>'size')::numeric), 0)
  from jsonb_array_elements(coalesce(levels, '[]'::jsonb)) l
  where touch is not null
    and case when is_ask then (l->>'price')::numeric <= touch + cap
             else (l->>'price')::numeric >= touch - cap end;
$$;

-- --------------------------------------------------------------------------
-- CAPACITY - a curve, not a number (spec Task 7). usd_at_2c/5c/10c/usd_full
-- per city per snapshot hour, summed across that city's live bands, both
-- sides. Never collapse this to a single figure in the UI.
-- --------------------------------------------------------------------------
create or replace function recompute_capacity() returns integer
language plpgsql security definer as $$
declare
  v_rows integer;
begin
  with latest_per_band as (
    select distinct on (band_id) *
    from book_snapshots
    order by band_id, observed_at desc
  )
  insert into derived_capacity (city_key, computed_at, hour_utc, usd_at_2c, usd_at_5c, usd_at_10c, usd_full, live_bands)
  select
    m.city_key,
    now(),
    extract(hour from bs.observed_at)::int as hour_utc,
    sum(capacity_side(bs.ask_levels, bs.best_ask, 0.02, true) + capacity_side(bs.bid_levels, bs.best_bid, 0.02, false)),
    sum(capacity_side(bs.ask_levels, bs.best_ask, 0.05, true) + capacity_side(bs.bid_levels, bs.best_bid, 0.05, false)),
    sum(capacity_side(bs.ask_levels, bs.best_ask, 0.10, true) + capacity_side(bs.bid_levels, bs.best_bid, 0.10, false)),
    sum(depth_usd(bs.ask_levels) + depth_usd(bs.bid_levels)),
    count(*) filter (where bs.market_state = 'LIVE')
  from latest_per_band bs
  join bands b on b.band_id = bs.band_id
  join markets m on m.market_id = b.market_id
  group by m.city_key, extract(hour from bs.observed_at)::int;
  get diagnostics v_rows = row_count;
  return v_rows;
end;
$$;

-- --------------------------------------------------------------------------
-- CORRELATION - of forecast ERRORS, not temperatures (spec Task 7).
-- p_lookback_days/p_lead_days/min sample size are provisional - tune once
-- there's enough live history to matter.
-- --------------------------------------------------------------------------
create or replace function recompute_correlation(p_lookback_days int default 180, p_lead_days int default 1)
returns integer language plpgsql security definer as $$
declare
  v_rows integer;
begin
  with obs_daily as (
    select city_key, valid_at::date as d, max(temp_c) as obs_max
    from weather_observations
    where valid_at >= now() - (p_lookback_days || ' days')::interval
    group by city_key, valid_at::date
  ),
  errs as (
    select f.city_key, f.for_date, (f.forecast_max_c - o.obs_max) as err
    from weather_forecasts f
    join obs_daily o on o.city_key = f.city_key and o.d = f.for_date
    where f.lead_days = p_lead_days and f.forecast_max_c is not null
  ),
  pairs as (
    select a.city_key as city_a, b.city_key as city_b,
           corr(a.err, b.err) as err_corr, count(*) as n_days
    from errs a
    join errs b on a.for_date = b.for_date and a.city_key < b.city_key
    group by a.city_key, b.city_key
    having count(*) >= 20   -- provisional minimum sample
  )
  insert into derived_city_correlation (city_a, city_b, computed_at, n_days, err_corr)
  select city_a, city_b, now(), n_days, err_corr from pairs;
  get diagnostics v_rows = row_count;
  return v_rows;
end;
$$;
