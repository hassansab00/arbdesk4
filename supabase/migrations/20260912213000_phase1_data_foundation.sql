-- Phase 1: keep the Data Bank fast and record source defects without
-- rewriting the proprietary history that exposed them.
begin;

-- The old v_archive_daily aggregated hundreds of thousands of source rows on
-- every page load.  The browser role has a three-second statement timeout and
-- the live query took more than four seconds.  These two small derived tables
-- are maintained from new inserts, while the original source rows remain the
-- authority and are never changed here.
create table if not exists public.archive_daily_rollup (
  dataset text not null,
  day date not null,
  rows bigint not null check (rows >= 0),
  primary key (dataset, day)
);

create table if not exists public.archive_daily_city_presence (
  dataset text not null,
  day date not null,
  city_key text not null,
  primary key (dataset, day, city_key)
);

create index if not exists archive_daily_rollup_day
  on public.archive_daily_rollup(day desc);
create index if not exists archive_daily_city_presence_day
  on public.archive_daily_city_presence(day desc, dataset);

alter table public.archive_daily_rollup enable row level security;
alter table public.archive_daily_city_presence enable row level security;
revoke all on public.archive_daily_rollup from public, anon, authenticated, service_role;
revoke all on public.archive_daily_city_presence from public, anon, authenticated, service_role;
grant select on public.archive_daily_rollup to anon, authenticated, service_role;
grant select on public.archive_daily_city_presence to anon, authenticated, service_role;

drop policy if exists archive_daily_rollup_read on public.archive_daily_rollup;
create policy archive_daily_rollup_read on public.archive_daily_rollup
  for select to anon, authenticated using (true);
drop policy if exists archive_daily_city_presence_read on public.archive_daily_city_presence;
create policy archive_daily_city_presence_read on public.archive_daily_city_presence
  for select to anon, authenticated using (true);

create or replace function arbdesk_private.capture_archive_daily()
returns trigger
language plpgsql
security definer
set search_path = ''
as $phase1$
begin
  if tg_table_name = 'weather_observations' then
    insert into public.archive_daily_rollup(dataset, day, rows)
    select 'Station observations', (valid_at at time zone 'UTC')::date, count(*)
      from new_rows where valid_at is not null group by 2
    on conflict (dataset, day) do update
      set rows = archive_daily_rollup.rows + excluded.rows;
    insert into public.archive_daily_city_presence(dataset, day, city_key)
    select distinct 'Station observations', (valid_at at time zone 'UTC')::date, city_key
      from new_rows where valid_at is not null and city_key is not null
    on conflict do nothing;

  elsif tg_table_name = 'weather_forecasts' then
    insert into public.archive_daily_rollup(dataset, day, rows)
    select 'Forecasts', (run_at at time zone 'UTC')::date, count(*)
      from new_rows where run_at is not null group by 2
    on conflict (dataset, day) do update
      set rows = archive_daily_rollup.rows + excluded.rows;
    insert into public.archive_daily_city_presence(dataset, day, city_key)
    select distinct 'Forecasts', (run_at at time zone 'UTC')::date, city_key
      from new_rows where run_at is not null and city_key is not null
    on conflict do nothing;

  elsif tg_table_name = 'book_snapshots' then
    insert into public.archive_daily_rollup(dataset, day, rows)
    select 'Order books', (observed_at at time zone 'UTC')::date, count(*)
      from new_rows where observed_at is not null group by 2
    on conflict (dataset, day) do update
      set rows = archive_daily_rollup.rows + excluded.rows;
    insert into public.archive_daily_city_presence(dataset, day, city_key)
    select distinct 'Order books', (n.observed_at at time zone 'UTC')::date, m.city_key
      from new_rows n
      join public.bands b on b.band_id = n.band_id
      join public.markets m on m.market_id = b.market_id
     where n.observed_at is not null and m.city_key is not null
    on conflict do nothing;

  elsif tg_table_name = 'trades_observed' then
    insert into public.archive_daily_rollup(dataset, day, rows)
    select 'Trades seen', (traded_at at time zone 'UTC')::date, count(*)
      from new_rows where traded_at is not null group by 2
    on conflict (dataset, day) do update
      set rows = archive_daily_rollup.rows + excluded.rows;
    insert into public.archive_daily_city_presence(dataset, day, city_key)
    select distinct 'Trades seen', (traded_at at time zone 'UTC')::date, city_key
      from new_rows where traded_at is not null and city_key is not null
    on conflict do nothing;
  end if;
  return null;
end
$phase1$;

revoke all on function arbdesk_private.capture_archive_daily() from public, anon, authenticated, service_role;

do $phase1$
declare t text;
begin
  foreach t in array array[
    'weather_observations', 'weather_forecasts', 'book_snapshots', 'trades_observed'
  ] loop
    if to_regclass('public.' || t) is null then
      continue;
    end if;
    execute format('drop trigger if exists archive_daily_capture on public.%I', t);
    execute format(
      'create trigger archive_daily_capture after insert on public.%I '
      'referencing new table as new_rows for each statement '
      'execute function arbdesk_private.capture_archive_daily()', t
    );
  end loop;
end
$phase1$;

-- Backfill exact daily counts.  The trigger DDL above holds source-table locks
-- until this transaction commits, so a concurrent collector cannot slip rows
-- between this baseline and trigger activation.
insert into public.archive_daily_rollup(dataset, day, rows)
select 'Station observations', (valid_at at time zone 'UTC')::date, count(*)
  from public.weather_observations where valid_at is not null group by 2
on conflict (dataset, day) do update set rows = excluded.rows;
insert into public.archive_daily_rollup(dataset, day, rows)
select 'Forecasts', (run_at at time zone 'UTC')::date, count(*)
  from public.weather_forecasts where run_at is not null group by 2
on conflict (dataset, day) do update set rows = excluded.rows;
insert into public.archive_daily_rollup(dataset, day, rows)
select 'Order books', (observed_at at time zone 'UTC')::date, count(*)
  from public.book_snapshots where observed_at is not null group by 2
on conflict (dataset, day) do update set rows = excluded.rows;
insert into public.archive_daily_rollup(dataset, day, rows)
select 'Trades seen', (traded_at at time zone 'UTC')::date, count(*)
  from public.trades_observed where traded_at is not null group by 2
on conflict (dataset, day) do update set rows = excluded.rows;

insert into public.archive_daily_city_presence(dataset, day, city_key)
select distinct 'Station observations', (valid_at at time zone 'UTC')::date, city_key
  from public.weather_observations where valid_at is not null and city_key is not null
on conflict do nothing;
insert into public.archive_daily_city_presence(dataset, day, city_key)
select distinct 'Forecasts', (run_at at time zone 'UTC')::date, city_key
  from public.weather_forecasts where run_at is not null and city_key is not null
on conflict do nothing;
insert into public.archive_daily_city_presence(dataset, day, city_key)
select distinct 'Order books', (s.observed_at at time zone 'UTC')::date, m.city_key
  from public.book_snapshots s
  join public.bands b on b.band_id = s.band_id
  join public.markets m on m.market_id = b.market_id
 where s.observed_at is not null and m.city_key is not null
on conflict do nothing;
insert into public.archive_daily_city_presence(dataset, day, city_key)
select distinct 'Trades seen', (traded_at at time zone 'UTC')::date, city_key
  from public.trades_observed where traded_at is not null and city_key is not null
on conflict do nothing;

create or replace view public.v_archive_daily
with (security_invoker = true)
as
select r.dataset, r.day, r.rows, count(p.city_key)::bigint as cities
  from public.archive_daily_rollup r
  left join public.archive_daily_city_presence p
    on p.dataset = r.dataset and p.day = r.day
 where r.day >= current_date - 90
 group by r.dataset, r.day, r.rows;

grant select on public.v_archive_daily to anon, authenticated, service_role;

-- A detector version is immutable evidence.  Re-running the same version is
-- idempotent; a later detector version may add a new finding but never edits
-- an older one.
create index if not exists proprietary_quality_issue_version
  on public.proprietary_data_quality_flags(issue_code, detector_version, source_relation);

create or replace function arbdesk_private.detect_data_quality()
returns bigint
language plpgsql
security definer
set search_path = ''
as $phase1$
declare
  v_added bigint := 0;
  v_n bigint;
  v_version constant text := 'phase1-foundation-v1';
begin
  insert into public.proprietary_data_quality_flags(
    source_relation, source_key, issue_code, severity, evidence, detector_version
  )
  select 'bands', jsonb_build_object('band_id', b.band_id),
         'zero_width_non_tail_band', 'critical',
         jsonb_build_object('market_id', b.market_id, 'band_index', b.band_index,
                            'band_label', b.band_label, 'band_lo', b.band_lo,
                            'band_hi', b.band_hi), v_version
    from public.bands b
   where b.band_lo is not null and b.band_hi is not null and b.band_lo = b.band_hi
     and not coalesce(b.open_low, false) and not coalesce(b.open_high, false)
     and not exists (
       select 1 from public.proprietary_data_quality_flags q
        where q.source_relation = 'bands'
          and q.source_key = jsonb_build_object('band_id', b.band_id)
          and q.issue_code = 'zero_width_non_tail_band'
          and q.detector_version = v_version
     );
  get diagnostics v_n = row_count; v_added := v_added + v_n;

  insert into public.proprietary_data_quality_flags(
    source_relation, source_key, issue_code, severity, evidence, detector_version
  )
  select 'markets', jsonb_build_object('market_id', m.market_id),
         'market_city_unit_conflict', 'critical',
         jsonb_build_object('city_key', m.city_key, 'resolution_date', m.resolution_date,
                            'event_slug', m.event_slug, 'market_unit', m.unit,
                            'city_unit', c.unit), v_version
    from public.markets m join public.cities c on c.city_key = m.city_key
   where upper(trim(coalesce(m.unit, ''))) <> upper(trim(coalesce(c.unit, '')))
     and not exists (
       select 1 from public.proprietary_data_quality_flags q
        where q.source_relation = 'markets'
          and q.source_key = jsonb_build_object('market_id', m.market_id)
          and q.issue_code = 'market_city_unit_conflict'
          and q.detector_version = v_version
     );
  get diagnostics v_n = row_count; v_added := v_added + v_n;

  insert into public.proprietary_data_quality_flags(
    source_relation, source_key, issue_code, severity, evidence, detector_version
  )
  select 'weather_observations', jsonb_build_object('obs_id', o.obs_id),
         'future_observation_timestamp', 'critical',
         jsonb_build_object('city_key', o.city_key, 'valid_at', o.valid_at,
                            'observed_at', o.observed_at, 'detected_against', clock_timestamp()),
         v_version
    from public.weather_observations o
   where (o.valid_at > now() + interval '5 minutes'
      or o.observed_at > now() + interval '5 minutes')
     and not exists (
       select 1 from public.proprietary_data_quality_flags q
        where q.source_relation = 'weather_observations'
          and q.source_key = jsonb_build_object('obs_id', o.obs_id)
          and q.issue_code = 'future_observation_timestamp'
          and q.detector_version = v_version
     );
  get diagnostics v_n = row_count; v_added := v_added + v_n;

  insert into public.proprietary_data_quality_flags(
    source_relation, source_key, issue_code, severity, evidence, detector_version
  )
  select 'weather_forecasts', jsonb_build_object('forecast_id', f.forecast_id),
         'future_forecast_run_timestamp', 'critical',
         jsonb_build_object('city_key', f.city_key, 'model', f.model,
                            'run_at', f.run_at, 'for_date', f.for_date,
                            'detected_against', clock_timestamp()), v_version
    from public.weather_forecasts f
   where f.run_at > now() + interval '5 minutes'
     and not exists (
       select 1 from public.proprietary_data_quality_flags q
        where q.source_relation = 'weather_forecasts'
          and q.source_key = jsonb_build_object('forecast_id', f.forecast_id)
          and q.issue_code = 'future_forecast_run_timestamp'
          and q.detector_version = v_version
     );
  get diagnostics v_n = row_count; v_added := v_added + v_n;

  insert into public.proprietary_data_quality_flags(
    source_relation, source_key, issue_code, severity, evidence, detector_version
  )
  select 'cities', jsonb_build_object('city_key', c.city_key),
         'active_city_missing_coordinates', 'critical',
         jsonb_build_object('display_name', c.display_name, 'latitude', c.latitude,
                            'longitude', c.longitude), v_version
    from public.cities c
   where coalesce(c.status, 'active') = 'active'
     and (c.latitude is null or c.longitude is null)
     and not exists (
       select 1 from public.proprietary_data_quality_flags q
        where q.source_relation = 'cities'
          and q.source_key = jsonb_build_object('city_key', c.city_key)
          and q.issue_code = 'active_city_missing_coordinates'
          and q.detector_version = v_version
     );
  get diagnostics v_n = row_count; v_added := v_added + v_n;

  insert into public.proprietary_data_quality_flags(
    source_relation, source_key, issue_code, severity, evidence, detector_version
  )
  select 'cities', jsonb_build_object('city_key', c.city_key),
         'active_city_missing_timezone', 'critical',
         jsonb_build_object('display_name', c.display_name, 'timezone', c.timezone), v_version
    from public.cities c
   where coalesce(c.status, 'active') = 'active' and c.timezone is null
     and not exists (
       select 1 from public.proprietary_data_quality_flags q
        where q.source_relation = 'cities'
          and q.source_key = jsonb_build_object('city_key', c.city_key)
          and q.issue_code = 'active_city_missing_timezone'
          and q.detector_version = v_version
     );
  get diagnostics v_n = row_count; v_added := v_added + v_n;

  return v_added;
end
$phase1$;

revoke all on function arbdesk_private.detect_data_quality() from public, anon, authenticated;
grant usage on schema arbdesk_private to service_role;
grant execute on function arbdesk_private.detect_data_quality() to service_role;

create or replace function public.refresh_data_quality_flags()
returns bigint
language sql
security invoker
set search_path = ''
as $phase1$
  select arbdesk_private.detect_data_quality();
$phase1$;
revoke all on function public.refresh_data_quality_flags() from public, anon, authenticated;
grant execute on function public.refresh_data_quality_flags() to service_role;

select arbdesk_private.detect_data_quality();
notify pgrst, 'reload schema';
commit;
