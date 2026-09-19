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
-- A MAXIMUM IS NEVER BELOW THE LATEST READING, and this function is where
-- that has to be true. The first version computed the running maximum from
-- v_city_today_readings alone and fell back to the stored value. For 37 of
-- the 54 cities that view is EMPTY - IEM serves their observations about a
-- day late - so the maximum came from whatever happened to be stored, while
-- n8n kept live_weather.temp_c current for all 54. Measured 2026-09-19: 25
-- cities with no running maximum at all, and 14 with a CURRENT TEMPERATURE
-- ABOVE THE STORED MAXIMUM, worst by 12.5 C (munich 9.0 against 21.5).
--
-- That field drives holds_running_max, out_of_reach, day_decided, strategy s5
-- and the observed-max floor in the probability engine, so an understated
-- maximum leaves probability on buckets the day has already passed and makes
-- bands it has already cleared look unreachable. The fix is the live
-- thermometer as a third input and greatest()/least() over all three, plus
-- readings_today and running_max_basis so a consumer can tell a measured
-- maximum from a single point that is only a FLOOR under one. See
-- sql/ad4_71_observation_health.sql, which must be installed first.
--
-- Applied to the live project on 2026-09-15 as migrations
-- refresh_live_weather_timing, pg_cron_live_weather_timing and
-- refresh_live_weather_timing_v2, and on 2026-09-19 as
-- running_max_never_below_latest_reading. Re-runnable.

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
  cnt as (
    select city_key, count(*)::int as readings_today from today group by city_key
  ),
  newest as (
    select distinct on (city_key) city_key, valid_at as latest_at, temp_c as latest_temp_c
      from today order by city_key, valid_at desc
  ),
  peak as (
    select distinct on (city_key) city_key, temp_c as series_max_c, valid_at as series_max_at
      from today order by city_key, temp_c desc, valid_at asc
  ),
  low as (
    select city_key, min(temp_c) as series_min_c from today group by city_key
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
  -- The row as it stands. Weighing the stored maximum and the live
  -- thermometer against today's series HERE, rather than in the SET clause,
  -- is what lets running_max_at name the reading the maximum actually came
  -- from instead of being coalesced away from it.
  prev as (
    select city_key, running_max_c, running_max_at, running_min_c, temp_c, observed_at
      from live_weather
  ),
  calc as (
    select k.city_key,
           k.tz,
           k.local_now::date as local_date,
           tm.minutes_to_peak,
           tm.local_hour, tm.window_opens_hour, tm.window_closes_hour,
           coalesce(cn.readings_today, 0) as readings_today,
           p.series_max_c, p.series_max_at, l.series_min_c,
           nw.latest_temp_c,
           -- A stored extreme counts only while the reading it came from
           -- belongs to this city's current local day.
           case when (pv.running_max_at at time zone k.tz)::date = k.local_now::date
                then pv.running_max_c end  as kept_max_c,
           case when (pv.running_max_at at time zone k.tz)::date = k.local_now::date
                then pv.running_max_at end as kept_max_at,
           case when (pv.running_max_at at time zone k.tz)::date = k.local_now::date
                then pv.running_min_c end  as kept_min_c,
           -- THE LIVE THERMOMETER, which nothing here used to read. n8n keeps
           -- it current for all 54 cities; the observation feed is about a day
           -- behind for 37 of them. Leaving it out is what produced a stored
           -- maximum BELOW the current temperature for 14 cities - by up to
           -- 12.5 C - which is arithmetically impossible and made bands the
           -- day had already cleared look unreachable.
           case when (pv.observed_at at time zone k.tz)::date = k.local_now::date
                then pv.temp_c end         as live_temp_c,
           case when (pv.observed_at at time zone k.tz)::date = k.local_now::date
                then pv.observed_at end    as live_at,
           case when l1.temp_c is null then null else round(nw.latest_temp_c - l1.temp_c, 2) end as temp_change_1h,
           case when l3.temp_c is null then null else round(nw.latest_temp_c - l3.temp_c, 2) end as temp_change_3h,
           tm.direction,
           -- The day is DECIDED when the maximum can no longer move: the peak
           -- window has closed and the reading has fallen away from the day's
           -- high, or it is late evening by the local clock whatever the trend.
           -- Measured against the SERIES, because "has it turned over" is a
           -- question about a sequence of readings; a city with no series has
           -- no timing row, so this is false there and s5 cannot fire.
           coalesce(
             (tm.local_hour >= 21)
             or (tm.local_hour > tm.window_closes_hour + 1
                 and (coalesce(tm.rolling_over, false)
                      or (nw.latest_temp_c is not null
                          and p.series_max_c - nw.latest_temp_c >= 1.0))),
             false) as decided
      from clock k
      left join timing tm on tm.city_key = k.city_key
      left join cnt cn    on cn.city_key = k.city_key
      left join peak p    on p.city_key = k.city_key
      left join low l     on l.city_key = k.city_key
      left join newest nw on nw.city_key = k.city_key
      left join lag1 l1   on l1.city_key = k.city_key
      left join lag3 l3   on l3.city_key = k.city_key
      left join prev pv   on pv.city_key = k.city_key
  ),
  -- THE INVARIANT, in one place: a maximum is never below anything measured
  -- today, and a minimum is never above it. greatest()/least() ignore nulls,
  -- so a city with only one of the three still gets the one it has.
  final as (
    select c.*,
           greatest(c.series_max_c, c.kept_max_c, c.live_temp_c) as max_c,
           least(c.series_min_c, c.kept_min_c, c.live_temp_c)    as min_c
      from calc c
  )
  update live_weather lw
     set minutes_to_peak   = f.minutes_to_peak,
         peak_window_state = case
             when f.decided then 'AFTER'
             when f.local_hour is null then null
             when f.local_hour < f.window_opens_hour then 'BEFORE'
             when f.local_hour <= f.window_closes_hour then 'INSIDE'
             else 'AFTER' end,
         day_decided       = f.decided,
         running_max_c     = f.max_c,
         -- Name the reading it came from. A measured series wins a tie: it is
         -- the better provenance even at the same temperature.
         running_max_at    = case
             when f.max_c is null then null
             when f.series_max_c is not null and f.series_max_c = f.max_c then f.series_max_at
             when f.kept_max_c   is not null and f.kept_max_c   = f.max_c then f.kept_max_at
             else f.live_at end,
         running_min_c     = f.min_c,
         readings_today    = f.readings_today,
         -- The provenance travels with the number, so a consumer that needs a
         -- settled maximum (s5) can refuse a floor instead of trading on it.
         running_max_basis = public.ad4_running_max_basis(
                               f.readings_today,
                               coalesce(f.live_temp_c, f.latest_temp_c)),
         temp_change_1h    = f.temp_change_1h,
         temp_change_3h    = f.temp_change_3h,
         trend             = f.direction,
         local_date        = f.local_date
    from final f
   where f.city_key = lw.city_key;
  get diagnostics n = row_count;
  return n;
end;
$$;

comment on function public.refresh_live_weather_timing() is
  'Fills live_weather timing columns (minutes_to_peak, peak_window_state, day_decided, running max/min and what they rest on, 1h/3h change, trend, local_date) from v_trade_timing, today''s observation series AND the live thermometer - so the running maximum is never below anything measured today. Called by the live_weather statement trigger and by pg_cron every 10 minutes.';

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
