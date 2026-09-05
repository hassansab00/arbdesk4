-- ===========================================================================
-- ad4_28_feature_cache.sql - stop the UI paying for a pass over the archive.
--
-- WHAT WENT WRONG. `canceling statement due to statement timeout (57014)`,
-- across the desk. Two separate causes, both mine:
--
--   1. ad4_17/ad4_19 grew a `recent_obs` CTE filtering weather_observations on
--      valid_at ALONE. The only index was (city_key, valid_at desc), whose
--      leading column is city_key - useless for a bare valid_at predicate - so
--      every page load ran a sequential scan of the whole archive. And since
--      the Signals rail lives in the layout, "every page" is literal.
--
--   2. ad4_28's own additions to Analytics put v_weather_effects,
--      v_persistence_skill and v_city_climb_profile on a page. All three
--      aggregate over ALL of history by definition. Measured on 710k
--      observations - a modest archive - they run 1.0s, 2.0s and 2.7s. Supabase
--      cancels an anon statement at a few seconds, so they did not run slowly,
--      they did not run.
--
-- THE RULE ad4_19 ESTABLISHED AND THIS RESTORES: a page load may not scan the
-- archive. Anything O(history) is computed once by a job and read as a table.
-- The views keep their names, so nothing downstream changes.
--
-- Run order: after sql/ad4_26_temp_trend.sql. Re-runnable.
-- ===========================================================================

do $ad4$
begin
  if to_regclass('public.weather_observations') is null then
    raise exception 'ad4_28 needs weather_observations - run sql/ad4_00_preflight.sql first';
  end if;
  if to_regclass('public.v_city_day_features') is null then
    raise exception
      'ad4_28 caches v_city_day_features, which does not exist. Run sql/ad4_21_weather_features.sql first - it is the file that creates it, and it now refuses to report success unless it really did.';
  end if;

  -- Not just "does the view exist" - does it have the columns this file
  -- caches. ad4_21 gained wind_max and pressure_change_24h_hpa in the same
  -- change that added ad4_28, so a database running the PREVIOUS ad4_21 has
  -- the view and would fail here on a missing column, several hundred lines
  -- in, with a message about a column rather than about a stale file.
  if not exists (
    select 1 from information_schema.columns
     where table_name = 'v_city_day_features'
       and column_name = 'pressure_change_24h_hpa'
  ) then
    raise exception
      'v_city_day_features exists but is the OLD version - it has no pressure_change_24h_hpa. RE-RUN sql/ad4_21_weather_features.sql (it changed), then this file.';
  end if;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 1. The missing index.
--
--    (city_key, valid_at) cannot serve `where valid_at > ...` with no city in
--    the predicate. Several queries do exactly that - the 3-day recent maximum
--    in v_city_stats, the current-day readings in ad4_26 - and each was a full
--    scan. On the test archive this took the recent-maximum query from a 46ms
--    parallel sequential scan to 1.5ms.
-- --------------------------------------------------------------------------
create index if not exists wx_obs_valid on weather_observations (valid_at desc);


-- --------------------------------------------------------------------------
-- 2. One row per city-day, computed by a job rather than by a page.
--
--    This is v_city_day_features, materialised. The view stays exactly as it
--    is - scripts/weather_model.py wants the live truth and is a batch job that
--    can afford it - and the UI reads this instead.
-- --------------------------------------------------------------------------
create table if not exists derived_city_day_features (
  city_key              text not null,
  obs_date              date not null,
  max_c                 numeric,
  min_c                 numeric,
  diurnal_range_c       numeric,
  n_obs                 int,
  prev_max_c            numeric,
  delta_max_c           numeric,
  morning_temp_c        numeric,
  morning_dewpoint_c    numeric,
  dewpoint_depression_c numeric,
  morning_humidity      numeric,
  morning_pressure_hpa  numeric,
  morning_to_max_c      numeric,
  cloud_mean            numeric,
  cloud_max             numeric,
  wind_mean             numeric,
  wind_max              numeric,
  precip_total          numeric,
  pressure_change_24h_hpa numeric,
  computed_at           timestamptz not null default now(),
  primary key (city_key, obs_date)
);

create index if not exists dcdf_date on derived_city_day_features (obs_date desc);

comment on table derived_city_day_features is
  'Cache of v_city_day_features. Refreshed by refresh_feature_cache(); read by the UI so a page load never pays for a pass over weather_observations.';


-- --------------------------------------------------------------------------
-- 3. The climb profile, cached.
--
--    v_city_climb_profile windows over two years of hourly observations per
--    city-day. It is the single most expensive object on the desk and it is
--    read by the City Monitor and Analytics.
-- --------------------------------------------------------------------------
create table if not exists derived_climb_profile (
  city_key             text not null,
  local_hour           int  not null,
  n_days               int,
  typical_climb_left_c numeric,
  climb_left_sd_c      numeric,
  climb_left_p10_c     numeric,
  climb_left_p90_c     numeric,
  pct_already_peaked   numeric,
  computed_at          timestamptz not null default now(),
  primary key (city_key, local_hour)
);


-- --------------------------------------------------------------------------
-- 4. Refresh both. Called daily by Derived Recompute.
-- --------------------------------------------------------------------------
-- SIGNATURE MATTERS. sql/ad4_29 replaces this body with an incremental one and
-- must be able to `create or replace` it - so both files declare the SAME
-- signature. Declared with a different one they would OVERLOAD instead, and a
-- bare `select refresh_feature_cache()` - which is how scripts/capacity.py
-- calls it - then fails with "function is not unique". The argument is unused
-- here; this version always rebuilds whole.
drop function if exists refresh_feature_cache();

create or replace function refresh_feature_cache(p_days int default null)
returns jsonb language plpgsql security definer as $ad4$
declare
  t0 timestamptz := clock_timestamp();
  -- v_ prefixed: a plpgsql variable named n_days is ambiguous with the
  -- column of the same name in the INSERT ... SELECT below, and Postgres
  -- refuses rather than guessing.
  v_days int; v_hours int;
begin
  -- Rebuilt whole rather than incrementally on purpose: prev_max_c and
  -- pressure_change_24h_hpa are window functions over the city's whole
  -- ordered history, so a row inserted late would leave the row after it
  -- holding a lag of the wrong day. A full rebuild of ~40k rows costs about a
  -- second in a job; getting this subtly wrong costs a model.
  delete from derived_city_day_features;
  insert into derived_city_day_features (
    city_key, obs_date, max_c, min_c, diurnal_range_c, n_obs, prev_max_c,
    delta_max_c, morning_temp_c, morning_dewpoint_c, dewpoint_depression_c,
    morning_humidity, morning_pressure_hpa, morning_to_max_c, cloud_mean,
    cloud_max, wind_mean, wind_max, precip_total, pressure_change_24h_hpa)
  select
    city_key, obs_date, max_c, min_c, diurnal_range_c, n_obs, prev_max_c,
    delta_max_c, morning_temp_c, morning_dewpoint_c, dewpoint_depression_c,
    morning_humidity, morning_pressure_hpa, morning_to_max_c, cloud_mean,
    cloud_max, wind_mean, wind_max, precip_total, pressure_change_24h_hpa
  from v_city_day_features;
  get diagnostics v_days = row_count;

  delete from derived_climb_profile;
  insert into derived_climb_profile (
    city_key, local_hour, n_days, typical_climb_left_c, climb_left_sd_c,
    climb_left_p10_c, climb_left_p90_c, pct_already_peaked)
  select city_key, local_hour, n_days, typical_climb_left_c, climb_left_sd_c,
         climb_left_p10_c, climb_left_p90_c, pct_already_peaked
  from v_city_climb_profile_live;
  get diagnostics v_hours = row_count;

  return jsonb_build_object(
    'ok', true, 'city_days', v_days, 'city_hours', v_hours,
    'ms', round(extract(epoch from (clock_timestamp() - t0)) * 1000));
end;
$ad4$;


-- --------------------------------------------------------------------------
-- 5. The live climb profile keeps its definition under a new name, and the
--    name the UI reads becomes the cache.
--
--    Same columns in the same order, so v_city_peak_approach - which joins it -
--    needs no change and `create or replace` is legal.
-- --------------------------------------------------------------------------
create or replace view v_city_climb_profile_live as
with hourly as (
  select
    o.city_key,
    (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date            as local_date,
    floor(extract(hour from (o.valid_at at time zone coalesce(c.timezone, 'UTC'))))::int as local_hour,
    max(o.temp_c)                                                          as temp_c
  from weather_observations o
  join cities c on c.city_key = o.city_key
  where o.temp_c is not null
    and o.valid_at > now() - interval '2 years'
  group by 1, 2, 3
),
counted as (
  select h.*, count(*) over (partition by h.city_key, h.local_date) as n_hours
  from hourly h
),
-- The climb still AHEAD, not the distance to the day's maximum wherever it
-- fell. The difference matters and the first version got it wrong: at 18:00
-- on a day that peaked at 16:00 it reported "0.49C still to climb", because
-- the reading had fallen 0.49 below a maximum that was already two hours in
-- the past. A trader reads that as room above and there is none.
--
-- A window frame over the rest of the day gives the honest number: the
-- highest temperature from this hour ONWARD, minus this hour's. After the
-- peak that is zero, which is the answer.
ahead as (
  select
    city_key, local_date, local_hour, temp_c,
    max(temp_c) over (
      partition by city_key, local_date
      order by local_hour
      rows between current row and unbounded following
    ) - temp_c                                           as climb_left_c
  from counted
  where n_hours >= 12
)
select
  a.city_key,
  a.local_hour,
  count(*)::int                                          as n_days,
  round(avg(a.climb_left_c)::numeric, 2)                 as typical_climb_left_c,
  round(stddev_samp(a.climb_left_c)::numeric, 2)         as climb_left_sd_c,
  -- The pessimistic case, which is the one that matters when deciding whether
  -- a band above the current reading is still reachable.
  round(percentile_cont(0.10) within group (order by a.climb_left_c)::numeric, 2)
                                                         as climb_left_p10_c,
  round(percentile_cont(0.90) within group (order by a.climb_left_c)::numeric, 2)
                                                         as climb_left_p90_c,
  -- How often the day was already over by this hour: nothing further ahead
  -- beat the reading in hand.
  round(100.0 * count(*) filter (where a.climb_left_c < 0.1) / count(*), 1)
                                                         as pct_already_peaked
from ahead a
group by a.city_key, a.local_hour
having count(*) >= 20;

comment on view v_city_climb_profile_live is
  'The live computation. Expensive by nature - two years of hourly observations windowed per city-day - so only refresh_feature_cache() reads it.';

drop view if exists v_city_climb_profile cascade;
create view v_city_climb_profile as
select city_key, local_hour, n_days, typical_climb_left_c, climb_left_sd_c,
       climb_left_p10_c, climb_left_p90_c, pct_already_peaked
from derived_climb_profile;


-- --------------------------------------------------------------------------
-- 6. v_city_peak_approach was dropped by the cascade above. Rebuilt identical.
-- --------------------------------------------------------------------------
create or replace view v_city_peak_approach as
select
  t.city_key,
  t.latest_temp_c,
  t.latest_at,
  t.latest_local_hour,
  t.running_max_c,
  t.slope_3_c_per_h,
  t.slope_6_c_per_h,
  t.direction,
  t.rolling_over,
  t.below_running_max_c,
  t.reading_age_min,
  t.n_readings,
  p.typical_climb_left_c,
  p.climb_left_p10_c,
  p.climb_left_p90_c,
  p.pct_already_peaked,
  p.n_days                                               as profile_days,
  case when p.typical_climb_left_c is null then null
       else round((t.latest_temp_c + p.typical_climb_left_c)::numeric, 2)
  end                                                    as implied_max_c,
  case when p.climb_left_p10_c is null then null
       else round((t.latest_temp_c + p.climb_left_p10_c)::numeric, 2)
  end                                                    as implied_max_low_c,
  case when p.climb_left_p90_c is null then null
       else round((t.latest_temp_c + p.climb_left_p90_c)::numeric, 2)
  end                                                    as implied_max_high_c
from v_city_temp_trend t
left join v_city_climb_profile p
       on p.city_key = t.city_key
      and p.local_hour = floor(t.latest_local_hour)::int;


-- --------------------------------------------------------------------------
-- 7. The two Analytics views, rebuilt on the cache.
--
--    Same columns, same meanings. Only the source changes: they read
--    derived_city_day_features instead of recomputing it, which is the whole
--    difference between 1-3 seconds and a few milliseconds.
-- --------------------------------------------------------------------------
create or replace view v_persistence_skill as
select
  city_key,
  count(*)::int                                     as n_days,
  round(avg(abs(delta_max_c)), 2)                   as persistence_mae_c,
  round(stddev_samp(delta_max_c), 2)                as delta_sd_c,
  round(avg(delta_max_c), 2)                        as mean_drift_c,
  round(max(abs(delta_max_c)), 1)                   as biggest_swing_c
from derived_city_day_features
where delta_max_c is not null and n_obs >= 12
group by city_key
having count(*) >= 20;

create or replace view v_weather_effects as
with d as (
  select * from derived_city_day_features
  where morning_to_max_c is not null and n_obs >= 12
),
banded as (
  select 'cloud' as variable,
         case when cloud_mean is null then null
              when cloud_mean <= 1 then 'clear (0-1)'
              when cloud_mean <= 3 then 'few (2-3)'
              when cloud_mean <= 5 then 'scattered (4-5)'
              when cloud_mean <= 7 then 'broken (6-7)'
              else 'overcast (8)' end as bucket,
         morning_to_max_c, delta_max_c
    from d
  union all
  select 'dryness at 08:00',
         case when dewpoint_depression_c is null then null
              when dewpoint_depression_c < 3  then 'humid (<3C)'
              when dewpoint_depression_c < 7  then 'moderate (3-7C)'
              when dewpoint_depression_c < 12 then 'dry (7-12C)'
              else 'very dry (>12C)' end,
         morning_to_max_c, delta_max_c
    from d
  union all
  select 'rain',
         case when precip_total is null then null
              when precip_total = 0 then 'none'
              when precip_total < 0.1 then 'trace'
              else 'wet' end,
         morning_to_max_c, delta_max_c
    from d
  union all
  select 'pressure change 24h',
         case when pressure_change_24h_hpa is null then null
              when pressure_change_24h_hpa < -3 then 'falling hard (<-3 hPa)'
              when pressure_change_24h_hpa < -1 then 'falling (-3 to -1)'
              when pressure_change_24h_hpa <= 1 then 'steady (-1 to +1)'
              when pressure_change_24h_hpa <= 3 then 'rising (+1 to +3)'
              else 'rising hard (>+3 hPa)' end,
         morning_to_max_c, delta_max_c
    from d
)
select variable, bucket,
       count(*)::int                    as n,
       round(avg(morning_to_max_c), 2)  as avg_climb_c,
       round(avg(delta_max_c), 2)       as avg_day_change_c
from banded
where bucket is not null
group by variable, bucket
having count(*) >= 10
order by variable, bucket;

comment on view v_weather_effects is
  'What actually moves a day, in plain buckets, from the cached features. Now includes pressure tendency - the variable the desk collected all along and never used.';


-- --------------------------------------------------------------------------
-- 8. Grants.
-- --------------------------------------------------------------------------
do $ad4$
declare o text; r text;
begin
  foreach o in array array['derived_city_day_features', 'derived_climb_profile',
                           'v_city_climb_profile', 'v_city_climb_profile_live',
                           'v_city_peak_approach', 'v_persistence_skill',
                           'v_weather_effects'] loop
    if to_regclass('public.' || o) is null then continue; end if;
    foreach r in array array['anon', 'authenticated'] loop
      if exists (select 1 from pg_roles where rolname = r) then
        execute format('revoke all on %I from %I', o, r);
        execute format('grant select on %I to %I', o, r);
      end if;
    end loop;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
      execute format('grant all on %I to service_role', o);
    end if;
  end loop;
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant execute on function refresh_feature_cache(int) to %I', r);
    end if;
  end loop;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 9. Fill it once, so the pages work the moment this file finishes.
-- --------------------------------------------------------------------------
do $ad4$
declare r jsonb;
begin
  r := refresh_feature_cache();
  raise notice 'ad4_28: cached % city-day(s) and % city-hour(s) in % ms',
    r->>'city_days', r->>'city_hours', r->>'ms';
  raise notice 'ad4_28: refresh daily - select refresh_feature_cache(); Derived Recompute does.';
end
$ad4$;
