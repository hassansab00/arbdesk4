-- Phase 1D: make the Data Bank's per-city panel independent of archive size.
-- Source evidence remains untouched; this is a rebuildable derived counter.
begin;

create table if not exists public.archive_city_rollup (
  dataset text not null,
  city_key text not null,
  rows bigint not null check (rows >= 0),
  first_at timestamptz,
  last_at timestamptz,
  primary key (dataset, city_key)
);
create index if not exists archive_city_rollup_city
  on public.archive_city_rollup(city_key, dataset);

alter table public.archive_city_rollup enable row level security;
revoke all on public.archive_city_rollup from public, anon, authenticated, service_role;
grant select on public.archive_city_rollup to anon, authenticated, service_role;
drop policy if exists archive_city_rollup_read on public.archive_city_rollup;
create policy archive_city_rollup_read on public.archive_city_rollup
  for select to anon, authenticated using (true);

create or replace function arbdesk_private.capture_archive_city()
returns trigger language plpgsql security definer set search_path = '' as $phase1d$
begin
  if tg_table_name = 'weather_observations' then
    insert into public.archive_city_rollup(dataset, city_key, rows, first_at, last_at)
    select 'Station observations', city_key, count(*), min(valid_at), max(valid_at)
      from new_rows where city_key is not null and valid_at is not null group by city_key
    on conflict (dataset, city_key) do update set
      rows = archive_city_rollup.rows + excluded.rows,
      first_at = least(archive_city_rollup.first_at, excluded.first_at),
      last_at = greatest(archive_city_rollup.last_at, excluded.last_at);
  elsif tg_table_name = 'weather_forecasts' then
    insert into public.archive_city_rollup(dataset, city_key, rows, first_at, last_at)
    select 'Forecasts', city_key, count(*), min(run_at), max(run_at)
      from new_rows where city_key is not null and run_at is not null group by city_key
    on conflict (dataset, city_key) do update set
      rows = archive_city_rollup.rows + excluded.rows,
      first_at = least(archive_city_rollup.first_at, excluded.first_at),
      last_at = greatest(archive_city_rollup.last_at, excluded.last_at);
  elsif tg_table_name = 'book_snapshots' then
    insert into public.archive_city_rollup(dataset, city_key, rows, first_at, last_at)
    select 'Order books', m.city_key, count(*), min(n.observed_at), max(n.observed_at)
      from new_rows n
      join public.bands b on b.band_id = n.band_id
      join public.markets m on m.market_id = b.market_id
     where m.city_key is not null and n.observed_at is not null group by m.city_key
    on conflict (dataset, city_key) do update set
      rows = archive_city_rollup.rows + excluded.rows,
      first_at = least(archive_city_rollup.first_at, excluded.first_at),
      last_at = greatest(archive_city_rollup.last_at, excluded.last_at);
  elsif tg_table_name = 'trades_observed' then
    insert into public.archive_city_rollup(dataset, city_key, rows, first_at, last_at)
    select 'Trades seen', city_key, count(*), min(traded_at), max(traded_at)
      from new_rows where city_key is not null and traded_at is not null group by city_key
    on conflict (dataset, city_key) do update set
      rows = archive_city_rollup.rows + excluded.rows,
      first_at = least(archive_city_rollup.first_at, excluded.first_at),
      last_at = greatest(archive_city_rollup.last_at, excluded.last_at);
  end if;
  return null;
end
$phase1d$;
revoke all on function arbdesk_private.capture_archive_city() from public, anon, authenticated, service_role;

do $phase1d$
declare t text;
begin
  foreach t in array array[
    'weather_observations', 'weather_forecasts', 'book_snapshots', 'trades_observed'
  ] loop
    execute format('drop trigger if exists archive_city_capture on public.%I', t);
    execute format(
      'create trigger archive_city_capture after insert on public.%I '
      'referencing new table as new_rows for each statement '
      'execute function arbdesk_private.capture_archive_city()', t
    );
  end loop;
end
$phase1d$;

-- Exact one-time baseline after the triggers are installed. The transaction's
-- source-table locks prevent a concurrent insert from falling between the
-- baseline and trigger activation.
insert into public.archive_city_rollup(dataset, city_key, rows, first_at, last_at)
select 'Station observations', city_key, count(*), min(valid_at), max(valid_at)
from public.weather_observations where city_key is not null and valid_at is not null group by city_key
on conflict (dataset, city_key) do update set rows=excluded.rows, first_at=excluded.first_at, last_at=excluded.last_at;
insert into public.archive_city_rollup(dataset, city_key, rows, first_at, last_at)
select 'Forecasts', city_key, count(*), min(run_at), max(run_at)
from public.weather_forecasts where city_key is not null and run_at is not null group by city_key
on conflict (dataset, city_key) do update set rows=excluded.rows, first_at=excluded.first_at, last_at=excluded.last_at;
insert into public.archive_city_rollup(dataset, city_key, rows, first_at, last_at)
select 'Order books', m.city_key, count(*), min(s.observed_at), max(s.observed_at)
from public.book_snapshots s join public.bands b using (band_id) join public.markets m using (market_id)
where m.city_key is not null and s.observed_at is not null group by m.city_key
on conflict (dataset, city_key) do update set rows=excluded.rows, first_at=excluded.first_at, last_at=excluded.last_at;
insert into public.archive_city_rollup(dataset, city_key, rows, first_at, last_at)
select 'Trades seen', city_key, count(*), min(traded_at), max(traded_at)
from public.trades_observed where city_key is not null and traded_at is not null group by city_key
on conflict (dataset, city_key) do update set rows=excluded.rows, first_at=excluded.first_at, last_at=excluded.last_at;

create index if not exists phase1d_forecast_city_date
  on public.weather_forecasts(city_key, for_date, run_at desc);

create or replace view public.v_archive_by_city
with (security_invoker = true)
as
with obs_days as (
  select city_key, count(*)::integer as days
  from public.archive_daily_city_presence
  where dataset = 'Station observations'
  group by city_key
), forward_forecasts as (
  select city_key, count(*)::bigint as forward
  from public.weather_forecasts
  where for_date >= current_date
  group by city_key
), obs as (
  select city_key, rows as n, first_at, last_at
  from public.archive_city_rollup where dataset = 'Station observations'
), fc as (
  select city_key, rows as n, last_at
  from public.archive_city_rollup where dataset = 'Forecasts'
), bk as (
  select city_key, rows as n, last_at
  from public.archive_city_rollup where dataset = 'Order books'
)
select c.city_key, c.display_name, coalesce(c.status, 'active') as status,
       coalesce(o.n, 0) as observations, o.first_at as obs_first_at,
       o.last_at as obs_last_at, coalesce(d.days, 0) as obs_days,
       case when o.last_at is null then null
            else round(extract(epoch from (now() - o.last_at)) / 3600.0, 1) end as obs_age_h,
       case when coalesce(d.days, 0) = 0 then null
            else round(o.n::numeric / d.days, 1) end as readings_per_day,
       coalesce(f.n, 0) as forecasts, coalesce(ff.forward, 0) as forecasts_forward,
       f.last_at as forecast_last_at, coalesce(k.n, 0) as book_snapshots,
       k.last_at as book_last_at,
       case
         when coalesce(o.n, 0) = 0 then 'no observations at all'
         when o.last_at < now() - interval '24 hours' then 'observations have stopped'
         when coalesce(ff.forward, 0) = 0 then 'no forward forecast - nothing to price against'
         when coalesce(o.n, 0) / greatest(d.days, 1) < 12 then 'sparsely observed - the daily maximum is understated'
         when coalesce(k.n, 0) = 0 then 'no book has ever been snapshotted'
         else 'complete'
       end as verdict
from public.cities c
left join obs o using (city_key)
left join obs_days d using (city_key)
left join fc f using (city_key)
left join forward_forecasts ff using (city_key)
left join bk k using (city_key)
order by c.city_key;

revoke all on public.v_archive_by_city from public;
grant select on public.v_archive_by_city to anon, authenticated, service_role;

commit;
