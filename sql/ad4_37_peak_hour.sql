-- ===========================================================================
-- ad4_37_peak_hour.sql - compute the peak hour that nothing was computing.
--
-- THE GAP. derived_weather_peak holds each city's measured peak hour and the
-- width of its window, per calendar month. Four things read it:
--
--   live_weather.minutes_to_peak     the countdown on the Live and Monitor pages
--   v_city_stats.peak_hour_local     the clock on City Clusters
--   v_trade_timing (ad4_33)          when the entry window opens
--   strategy s7_pre_peak_gradient    its entire trigger
--
-- and NOTHING WROTE IT. scripts/regime.py reads it; no Python job, no RPC and
-- no SQL file anywhere in the repo inserts a row. So the table has been empty
-- since the schema was created, every consumer has silently used its fallback,
-- and the desk's own answer to "when is this city's maximum actually made" has
-- never existed.
--
-- WHY IT MUST BE MEASURED AND NOT ASSUMED. Peak hour is not 15:00 everywhere.
-- It moves with latitude, with continentality, and with the season: a coastal
-- city with an afternoon sea breeze can top out at 13:00 in July and 15:30 in
-- October. A single global window is wrong in most cities most of the year,
-- and being wrong about WHEN is indistinguishable from being wrong about WHAT
-- once a trade is on.
--
-- WHAT IS COMPUTED, from this desk's own archive:
--
--   peak_hour_local   the MEDIAN local hour at which the daily maximum was
--                     reached. Median, not mean: one cold front that peaks a
--                     day at 07:00 should not drag a city's whole window two
--                     hours earlier.
--   window_width_h    p90 minus p10 of those hours - how much the peak MOVES.
--                     A city with a 1.5 hour window is one you can time; a
--                     city with a 6 hour window is one where the hour tells
--                     you nothing, and the number says which.
--
-- Days with fewer than 12 readings are excluded, the same bar
-- v_city_day_features uses: a sparsely observed day has its maximum at
-- whichever hour happened to be sampled, which is noise wearing an hour.
--
-- Run order: after sql/ad4_00_preflight.sql. Re-runnable.
-- ===========================================================================

do $ad4$
begin
  if to_regclass('public.derived_weather_peak') is null then
    raise exception 'ad4_37 needs derived_weather_peak - run sql/ad4_00_preflight.sql first';
  end if;
end
$ad4$;


create or replace function refresh_weather_peak(p_min_days int default 20)
returns int language plpgsql security definer as $ad4$
declare v_rows int;
begin
  with daily as (
    -- One row per city-day: the local hour at which that day topped out.
    select
      o.city_key,
      (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date                          as local_date,
      extract(month from (o.valid_at at time zone coalesce(c.timezone, 'UTC')))::int       as month,
      extract(hour  from (o.valid_at at time zone coalesce(c.timezone, 'UTC')))
        + extract(minute from (o.valid_at at time zone coalesce(c.timezone, 'UTC'))) / 60.0 as local_hour,
      o.temp_c,
      count(*)      over (partition by o.city_key, (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date) as n_readings,
      row_number()  over (partition by o.city_key, (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date
                          order by o.temp_c desc, o.valid_at)                              as rk
    from weather_observations o
    join cities c on c.city_key = o.city_key
    where o.temp_c is not null
      and o.valid_at > now() - interval '3 years'
  ),
  peaks as (
    -- The hottest reading of each well-observed day, and when it happened.
    select city_key, month, local_date, local_hour
      from daily
     where rk = 1
       and n_readings >= 12
       -- A maximum at 02:00 is a front, not a diurnal peak. Including those
       -- pulls the median toward the middle of the night in cities that get
       -- them, which is the one hour the desk can be certain is wrong.
       and local_hour between 8 and 22
  ),
  agg as (
    select
      city_key,
      month,
      count(*)::int                                                                  as n_days,
      percentile_cont(0.5)  within group (order by local_hour)                       as p50,
      percentile_cont(0.10) within group (order by local_hour)                       as p10,
      percentile_cont(0.90) within group (order by local_hour)                       as p90
    from peaks
    group by city_key, month
    having count(*) >= p_min_days
  )
  insert into derived_weather_peak (city_key, month, peak_hour_local, window_width_h, n_days, computed_at)
  select
    a.city_key,
    a.month,
    round(a.p50::numeric, 2),
    -- Never zero: a window with no width says "the peak is at exactly 15:00
    -- every day", which no city does, and it would make every INSIDE/BEFORE
    -- boundary a knife edge.
    greatest(round((a.p90 - a.p10)::numeric, 2), 1.0),
    a.n_days,
    now()
  from agg a
  on conflict (city_key, month) do update
    set peak_hour_local = excluded.peak_hour_local,
        window_width_h  = excluded.window_width_h,
        n_days          = excluded.n_days,
        computed_at     = excluded.computed_at;

  get diagnostics v_rows = row_count;
  return v_rows;
end;
$ad4$;

comment on function refresh_weather_peak(int) is
  'Measure each city''s peak hour and window width per calendar month, from the archive. Nothing wrote derived_weather_peak before this: every consumer - minutes_to_peak, v_trade_timing, strategy s7 - was silently on a fallback.';


-- --------------------------------------------------------------------------
-- What was measured, and where the assumption is still in force.
-- --------------------------------------------------------------------------
create or replace view v_peak_hour_coverage as
with months as (
  select c.city_key, c.display_name, c.timezone,
         extract(month from (now() at time zone coalesce(c.timezone, 'UTC')))::int as this_month
    from cities c
   where coalesce(c.status, 'active') = 'active'
)
select
  m.city_key,
  m.display_name,
  p.peak_hour_local,
  p.window_width_h,
  p.n_days,
  p.computed_at,
  (select count(*) from derived_weather_peak d where d.city_key = m.city_key)::int as months_measured,
  case
    when p.peak_hour_local is null then 'ASSUMED - no measured peak for this month'
    when p.n_days < 40            then format('measured on %s day(s) - thin', p.n_days)
    when p.window_width_h > 4     then format('measured, but the peak moves %s hours - timing tells you little here', p.window_width_h)
    else                               format('measured on %s days, window %s hours', p.n_days, p.window_width_h)
  end                                                                              as verdict
from months m
left join derived_weather_peak p
       on p.city_key = m.city_key and p.month = m.this_month
order by p.peak_hour_local nulls last, m.city_key;

comment on view v_peak_hour_coverage is
  'Which cities have a measured peak hour for the CURRENT month and which are still on the assumed one. A wide window is not a failure - it is the honest answer that this city cannot be timed.';


do $ad4$
declare r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_peak_hour_coverage to %I', r);
    end if;
  end loop;
  foreach r in array array['authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant execute on function refresh_weather_peak(int) to %I', r);
    end if;
  end loop;
end
$ad4$;


do $ad4$
declare v_rows int; v_cities int; v_assumed int;
begin
  v_rows := refresh_weather_peak();
  select count(distinct city_key) into v_cities from derived_weather_peak;
  select count(*) into v_assumed from v_peak_hour_coverage where peak_hour_local is null;
  raise notice 'ad4_37: measured % city-month row(s) across % city/cities', v_rows, v_cities;
  if v_assumed > 0 then
    raise notice 'ad4_37: % active city/cities still have no measured peak for THIS month - they fall back to the climb profile, then to 15:00. Backfill with Actions -> Observations.', v_assumed;
  end if;
  raise notice 'ad4_37: this runs daily from Actions -> Derived Recompute. select * from v_peak_hour_coverage;';
end
$ad4$;
