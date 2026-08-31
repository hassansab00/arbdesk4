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

do $$
declare r record;
begin
  for r in select * from (values
      ('cities','city_key')
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

-- AD4 Phase 1 - skill measurement table. Run in the Supabase SQL editor.
-- DERIVED: recomputed daily, keeps history via computed_at. Never frozen.

create table if not exists derived_forecast_skill (
  city_key             text not null references cities(city_key),
  computed_at          timestamptz not null default now(),
  lead_days            int not null,
  n_days               int,
  mae_c                numeric,   -- mean absolute error, degrees C
  bias_c               numeric,   -- mean SIGNED error = the station bias term
  p90_abs_err_c        numeric,
  mae_bands            numeric,   -- MAE expressed in that city's band widths
  pct_within_one_band  numeric,
  band_width_c         numeric,
  primary key (city_key, computed_at, lead_days)
);

comment on column derived_forecast_skill.bias_c is
  'Mean signed error. Non-zero means the grid forecast systematically misses this station - the correctable, proprietary part.';
comment on column derived_forecast_skill.mae_bands is
  'MAE / band width. Under 1 favours concentration strategies; over 2 favours intraday and arbitrage.';

create index if not exists idx_skill_latest on derived_forecast_skill (city_key, lead_days, computed_at desc);
