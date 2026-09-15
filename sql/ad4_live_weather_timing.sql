-- live_weather timing columns: minutes_to_peak, peak_window_state,
-- day_decided, running_max_c/at, running_min_c, temp_change_1h/3h, trend,
-- local_date.
--
-- Nothing ever wrote these. v_trade_timing computes the timing from the
-- clock and the observation series but reads live_weather.day_decided back,
-- so the S5 gate ("the maximum is banked") could never open and every
-- minutes_to_peak in the UI was null. This function fills the columns from
-- the same views. It runs after every live_weather write (statement
-- trigger, so P1.2 station rows and P1.5 model rows both refresh it) and
-- every 10 minutes under pg_cron, because minutes_to_peak is a clock
-- quantity that moves even when no reading arrives.
--
-- Applied to the live project on 2026-09-15 as migrations
-- refresh_live_weather_timing, pg_cron_live_weather_timing and
-- refresh_live_weather_timing_v2. Re-runnable.

create or replace function public.refresh_live_weather_timing()
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  n integer := 0;
begin
  with clock as (
    select c.city_key,
           coalesce(c.timezone, 'UTC') as tz,
           (now() at time zone coalesce(c.timezone, 'UTC')) as local_now
      from cities c
     where coalesce(c.status, 'active') = 'active'
  ),
  timing as (
    select t.city_key, t.local_hour, t.window_opens_hour, t.window_closes_hour,
           t.minutes_to_peak, t.rolling_over, t.direction
      from v_trade_timing t
  ),
  today as (
    select r.city_key, r.valid_at, r.temp_c
      from v_city_today_readings r
  ),
  newest as (
    select distinct on (city_key) city_key, valid_at as latest_at, temp_c as latest_temp_c
      from today order by city_key, valid_at desc
  ),
  peak as (
    select distinct on (city_key) city_key, temp_c as running_max_c, valid_at as running_max_at
      from today order by city_key, temp_c desc, valid_at asc
  ),
  low as (
    select city_key, min(temp_c) as running_min_c from today group by city_key
  ),
  lag1 as (
    select distinct on (t.city_key) t.city_key, t.temp_c
      from today t join newest nw using (city_key)
     where t.valid_at <= nw.latest_at - interval '50 minutes'
     order by t.city_key, t.valid_at desc
  ),
  lag3 as (
    select distinct on (t.city_key) t.city_key, t.temp_c
      from today t join newest nw using (city_key)
     where t.valid_at <= nw.latest_at - interval '170 minutes'
     order by t.city_key, t.valid_at desc
  ),
  calc as (
    select k.city_key,
           k.tz,
           k.local_now::date as local_date,
           tm.minutes_to_peak,
           tm.local_hour, tm.window_opens_hour, tm.window_closes_hour,
           p.running_max_c, p.running_max_at, l.running_min_c,
           nw.latest_temp_c,
           case when l1.temp_c is null then null else round(nw.latest_temp_c - l1.temp_c, 2) end as temp_change_1h,
           case when l3.temp_c is null then null else round(nw.latest_temp_c - l3.temp_c, 2) end as temp_change_3h,
           tm.direction,
           -- The day is DECIDED when the maximum can no longer move: the peak
           -- window has closed and the reading has fallen away from the day's
           -- high, or it is late evening by the local clock whatever the trend.
           coalesce(
             (tm.local_hour >= 21)
             or (tm.local_hour > tm.window_closes_hour + 1
                 and (coalesce(tm.rolling_over, false)
                      or (nw.latest_temp_c is not null
                          and p.running_max_c - nw.latest_temp_c >= 1.0))),
             false) as decided
      from clock k
      left join timing tm on tm.city_key = k.city_key
      left join peak p    on p.city_key = k.city_key
      left join low l     on l.city_key = k.city_key
      left join newest nw on nw.city_key = k.city_key
      left join lag1 l1   on l1.city_key = k.city_key
      left join lag3 l3   on l3.city_key = k.city_key
  )
  update live_weather lw
     set minutes_to_peak   = c.minutes_to_peak,
         peak_window_state = case
             when c.decided then 'AFTER'
             when c.local_hour is null then null
             when c.local_hour < c.window_opens_hour then 'BEFORE'
             when c.local_hour <= c.window_closes_hour then 'INSIDE'
             else 'AFTER' end,
         day_decided       = c.decided,
         -- Today's readings win. A stored maximum is kept only while the
         -- reading it came from belongs to the city's current local day.
         running_max_c     = coalesce(c.running_max_c,
                               case when (lw.running_max_at at time zone c.tz)::date = c.local_date then lw.running_max_c  end),
         running_max_at    = coalesce(c.running_max_at,
                               case when (lw.running_max_at at time zone c.tz)::date = c.local_date then lw.running_max_at end),
         running_min_c     = coalesce(c.running_min_c,
                               case when (lw.running_max_at at time zone c.tz)::date = c.local_date then lw.running_min_c  end),
         temp_change_1h    = c.temp_change_1h,
         temp_change_3h    = c.temp_change_3h,
         trend             = c.direction,
         local_date        = c.local_date
    from calc c
   where c.city_key = lw.city_key;
  get diagnostics n = row_count;
  return n;
end;
$$;

comment on function public.refresh_live_weather_timing() is
  'Fills live_weather timing columns (minutes_to_peak, peak_window_state, day_decided, running max/min, 1h/3h change, trend, local_date) from v_trade_timing and today''s observation series. Called by the live_weather statement trigger and by pg_cron every 10 minutes.';

revoke all on function public.refresh_live_weather_timing() from public;
revoke all on function public.refresh_live_weather_timing() from anon;
revoke all on function public.refresh_live_weather_timing() from authenticated;
grant execute on function public.refresh_live_weather_timing() to service_role;

-- After every write of a reading the timing is recomputed once per statement.
-- The refresh itself touches none of the listed columns, and the depth guard
-- closes the loop a second way.
create or replace function public.live_weather_timing_tg()
returns trigger
language plpgsql
security definer
set search_path = public, pg_temp
as $$
begin
  if pg_trigger_depth() > 1 then
    return null;
  end if;
  begin
    perform public.refresh_live_weather_timing();
  exception when others then
    raise warning 'refresh_live_weather_timing failed: %', sqlerrm;
  end;
  return null;
end;
$$;

revoke all on function public.live_weather_timing_tg() from public;
revoke all on function public.live_weather_timing_tg() from anon;
revoke all on function public.live_weather_timing_tg() from authenticated;

drop trigger if exists live_weather_refresh_timing on public.live_weather;
create trigger live_weather_refresh_timing
  after insert or update of temp_c, observed_at on public.live_weather
  for each statement
  execute function public.live_weather_timing_tg();

-- The clock-driven refresh. pg_cron is on Supabase's allowed list; the job
-- name makes the schedule call idempotent.
create extension if not exists pg_cron;
grant usage on schema cron to postgres;
select cron.schedule('ad4_refresh_live_weather_timing', '*/10 * * * *',
                     $$select public.refresh_live_weather_timing()$$);

-- First fill.
select public.refresh_live_weather_timing() as rows_refreshed;
