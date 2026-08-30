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

do $$
declare r record;
begin
  for r in
    select * from (values
      ('weather_observations','city_key','text'),
      ('weather_observations','valid_at','timestamptz'),
      ('weather_observations','temp_c','numeric'),
      ('weather_observations','source','text')
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
-- Task 13c - live weather schema (Revision A §8.2).
-- Run after ad4_phase2.sql.
-- ===========================================================================

-- Sky condition and live state per station
alter table weather_observations add column if not exists sky_condition text;
alter table weather_observations add column if not exists sky_raw text;
alter table weather_observations add column if not exists visibility_m numeric;
alter table weather_observations add column if not exists pressure_hpa numeric;

comment on column weather_observations.sky_condition is
  'Normalised: CLEAR | PARTLY_CLOUDY | CLOUDY | OVERCAST | RAIN | SNOW | FOG | STORM. Parsed from METAR skyc1 + present weather.';

-- Live per-city state, one row per city, updated every poll
create table if not exists live_weather (
  city_key            text primary key references cities(city_key),
  updated_at          timestamptz not null default now(),
  observed_at         timestamptz,
  temp_c              numeric,
  temp_f              numeric,
  feels_like_c        numeric,
  humidity            numeric,
  wind_speed_kt       numeric,
  wind_dir_deg        numeric,
  wind_dir_compass    text,
  pressure_hpa        numeric,
  visibility_m        numeric,
  sky_condition       text,
  precip_1h           numeric,
  running_max_c       numeric,
  running_max_at      timestamptz,
  running_min_c       numeric,
  temp_change_1h      numeric,
  temp_change_3h      numeric,
  trend               text,          -- RISING | FALLING | FLAT
  minutes_to_peak     int,           -- from derived_weather_peak
  peak_window_state   text,          -- BEFORE | INSIDE | AFTER
  day_decided         boolean default false,
  local_time          timestamptz,
  local_date          date
);
comment on column live_weather.day_decided is
  'TRUE once temperature has turned down from running max and cannot be exceeded. This is the OBSERVED trade cutoff - not a clock time.';

-- Detected events, drives notifications
create table if not exists weather_events (
  event_id      bigserial primary key,
  detected_at   timestamptz not null default now(),
  city_key      text not null references cities(city_key),
  kind          text not null,
  severity      text not null check (severity in ('low','medium','high','critical')),
  temp_c        numeric,
  change_c      numeric,
  window_min    int,
  detail        jsonb,
  notified      boolean not null default false
);
create index if not exists idx_wx_events_time on weather_events (detected_at desc);
create index if not exists idx_wx_events_unnotified on weather_events (notified) where notified = false;

-- --------------------------------------------------------------------------
-- Thresholds are PROVISIONAL and UI-settable (spec §8.2) - do not
-- hardcode in scripts/live_weather.py, read from here.
-- --------------------------------------------------------------------------
insert into settings (key, value) values
  ('weather_alerts', '{
     "spike_c_per_hour": 2.0, "drop_c_per_hour": 2.0,
     "day_decided_consecutive_observations": 3, "day_decided_min_drop_c": 0.5,
     "stale_station_hours": 3,
     "provisional": true, "origin": "claude_invented",
     "note": "NO evidential basis. A 1C jump means something different in Denver than in Singapore - tune per data once live-hour observations accumulate."
   }'::jsonb)
on conflict (key) do nothing;

-- --------------------------------------------------------------------------
-- RLS for the two new tables (mirrors sql/ad4_rls.sql's pattern - re-run
-- that file after this one, or apply these two directly):
-- --------------------------------------------------------------------------
alter table live_weather enable row level security;
drop policy if exists anon_read on live_weather;
create policy anon_read on live_weather for select to anon using (true);

alter table weather_events enable row level security;
drop policy if exists anon_read on weather_events;
create policy anon_read on weather_events for select to anon using (true);

-- --------------------------------------------------------------------------
-- Supabase Realtime: the Live Weather UI (§8.4) subscribes to INSERTs on
-- weather_events instead of polling. Safe to re-run (guards against
-- "already a member of publication").
-- --------------------------------------------------------------------------
do $$ begin
  if not exists (select 1 from pg_publication where pubname = 'supabase_realtime') then
    raise notice 'realtime: publication supabase_realtime does not exist - weather_events not added (normal outside Supabase)';
  elsif not exists (
    select 1 from pg_publication_tables
    where pubname = 'supabase_realtime' and tablename = 'weather_events'
  ) then
    begin
      alter publication supabase_realtime add table weather_events;
    exception when others then
      raise notice 'realtime: could not add weather_events to supabase_realtime: %', sqlerrm;
    end;
  end if;
end $$;

