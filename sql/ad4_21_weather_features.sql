-- ===========================================================================
-- ad4_21_weather_features.sql - the variables that actually move a daily max.
--
-- AD4 has been treating the daily maximum as a number a forecast model hands
-- it, and everything else in weather_observations - dewpoint, cloud, humidity,
-- wind, precipitation, pressure - as decoration. It is not. The physics of how
-- hot an afternoon gets is mostly settled by conditions that are ALREADY
-- OBSERVABLE in the morning:
--
--   DEWPOINT DEPRESSION (temperature minus dewpoint) is the strongest of them.
--     Dry air heats fast: the sun's energy goes into raising temperature
--     instead of evaporating moisture. A morning with a 15C depression will
--     out-climb a morning with a 2C depression from the same starting point,
--     and by a lot.
--   CLOUD COVER caps it. Sunlight that does not reach the ground does not heat
--     it, and eight oktas of cover is a different day from clear sky.
--   WIND mixes the surface layer with cooler air above, flattening extremes in
--     both directions.
--   PRECIPITATION ends the argument: a wet surface spends the afternoon
--     evaporating rather than warming.
--
-- And PERSISTENCE - yesterday's maximum - is the benchmark every forecast has
-- to beat. It is not a naive baseline to be dismissed; in a stable air mass it
-- is very hard to improve on, and a model that cannot beat it is not a model.
--
-- This file exposes those as features per city-day. sql/ad4_18's fact tables
-- record what was PREDICTED; this records what the atmosphere was DOING, so
-- the two can be regressed against each other.
--
-- Run order: after sql/ad4_20_schedules.sql. Re-runnable.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- 1. One row per city-day: the morning's conditions, and how the day ended.
--
--    "Morning" is 06:00-10:00 LOCAL. That window is deliberate: it is after
--    sunrise, so the day's heating has started and the air mass has declared
--    itself, but before the afternoon it is trying to predict. Averaging over
--    the whole day would leak the answer into the question.
-- --------------------------------------------------------------------------
create or replace view v_city_day_features as
-- NOT MATERIALIZED is load-bearing, not a hint.
--
-- `obs` is referenced twice below (by `daily` and by `morning`), and a CTE
-- referenced more than once is MATERIALIZED by default: Postgres evaluates it
-- once, whole, and only then applies the caller's WHERE. So
-- `... from v_city_day_features where city_key = 'x'` read all 710,000
-- observations and threw away 691,000 of them - the filter could not reach the
-- scan. Every per-city or per-day read of this view cost the same as reading
-- all of it, which is what made refresh_feature_cache() a single seven-second
-- statement that Supabase cancels (HTTP 500 to the caller, and the Archive
-- Observations job correctly refusing to prune behind it).
--
-- Inlined, the predicate reaches weather_observations and uses
-- (city_key, valid_at). Measured on a 710k-row archive:
--
--   where city_key = 'c1'   823 ms  ->   54 ms
--   no filter at all        946 ms  ->  810 ms
--
-- The two evaluations cost less than one materialisation of everything, and
-- the results are identical: checked both ways on 29,600 city-days, all 18
-- feature columns agree on both the sum and the non-null count - including
-- prev_max_c and pressure_change_24h_hpa, the two window terms most at risk
-- from a plan change.
with obs as not materialized (
  select
    o.city_key,
    (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date            as obs_date,
    extract(hour from (o.valid_at at time zone coalesce(c.timezone, 'UTC'))) as local_hour,
    o.temp_c, o.dewpoint_c, o.humidity, o.wind_speed, o.precip,
    o.cloud_cover, o.pressure_hpa
  from weather_observations o
  left join cities c on c.city_key = o.city_key
  where o.temp_c is not null
),
daily as (
  select
    city_key, obs_date,
    max(temp_c)                                       as max_c,
    min(temp_c)                                       as min_c,
    count(*)::int                                     as n_obs,
    -- daytime cloud and wind: what the sun had to work through
    avg(cloud_cover) filter (where local_hour between 9 and 17)   as cloud_mean,
    max(cloud_cover) filter (where local_hour between 9 and 17)   as cloud_max,
    avg(wind_speed)  filter (where local_hour between 9 and 17)   as wind_mean,
    -- wind_max exists on the FORECAST side (weather_forecast_features) and did
    -- not exist here, so a model that used it could be fitted and then never
    -- applied. The two column sets have to match or the forward model dies
    -- silently, one skipped row at a time.
    max(wind_speed)  filter (where local_hour between 9 and 17)   as wind_max,
    sum(coalesce(precip, 0))                                      as precip_total
  from obs group by city_key, obs_date
),
morning as (
  -- The reading nearest 08:00 local, and the window's own averages. Nearest
  -- rather than averaged for temperature and dewpoint, because the DEPRESSION
  -- between them at a single instant is the physical quantity; averaging two
  -- series separately and subtracting is not the same thing.
  select distinct on (city_key, obs_date)
    city_key, obs_date,
    temp_c                                   as morning_temp_c,
    dewpoint_c                               as morning_dewpoint_c,
    (temp_c - dewpoint_c)                    as dewpoint_depression_c,
    humidity                                 as morning_humidity,
    pressure_hpa                             as morning_pressure_hpa
  from obs
  where local_hour between 6 and 10 and dewpoint_c is not null
  order by city_key, obs_date, abs(local_hour - 8)
)
select
  d.city_key,
  d.obs_date,
  d.max_c,
  d.min_c,
  (d.max_c - d.min_c)                        as diurnal_range_c,
  d.n_obs,
  -- persistence: the benchmark, and a feature in its own right
  lag(d.max_c) over (partition by d.city_key order by d.obs_date)  as prev_max_c,
  d.max_c - lag(d.max_c) over (partition by d.city_key order by d.obs_date) as delta_max_c,
  m.morning_temp_c,
  m.morning_dewpoint_c,
  m.dewpoint_depression_c,
  m.morning_humidity,
  m.morning_pressure_hpa,
  -- how far the day climbed from its morning reading: the quantity the
  -- covariates below actually explain
  (d.max_c - m.morning_temp_c)               as morning_to_max_c,
  round(d.cloud_mean, 2)                     as cloud_mean,
  d.cloud_max,
  round(d.wind_mean, 2)                      as wind_mean,
  round(d.precip_total, 3)                   as precip_total,

  -- ---- appended (create or replace can only add columns at the end) -------
  round(d.wind_max, 2)                       as wind_max,
  -- PRESSURE TENDENCY, not the level. Station pressure is mostly a statement
  -- about altitude: Denver reads ~840 hPa and Miami ~1015 every single day, so
  -- the level is near-constant per city and the intercept absorbs it. What
  -- carries information is the CHANGE - falling pressure is an approaching
  -- front, and a front is exactly when a persistence-style forecast fails.
  round(m.morning_pressure_hpa
        - lag(m.morning_pressure_hpa) over (partition by d.city_key order by d.obs_date), 2)
                                             as pressure_change_24h_hpa
from daily d
left join morning m on m.city_key = d.city_key and m.obs_date = d.obs_date;


-- --------------------------------------------------------------------------
-- 2. Persistence: the benchmark any forecast must beat.
--
--    If a model's error is not clearly below this, the model is decoration.
--    Reported per city so a city where the forecast adds nothing is visible
--    rather than averaged away.
-- --------------------------------------------------------------------------
create or replace view v_persistence_skill as
select
  city_key,
  count(*)::int                                          as n_days,
  round(avg(abs(delta_max_c)), 3)                        as persistence_mae_c,
  round(stddev_samp(delta_max_c), 3)                     as delta_sd_c,
  round(avg(delta_max_c), 3)                             as mean_drift_c,
  round(max(abs(delta_max_c)), 2)                        as biggest_swing_c
from v_city_day_features
where delta_max_c is not null and n_obs >= 12
group by city_key;


-- --------------------------------------------------------------------------
-- 3. What each variable is worth, as plain buckets.
--
--    Deliberately NOT a regression - that lives in scripts/weather_model.py
--    where it can be tested. This is the version you can read: on days when
--    the sky was clear, the afternoon climbed THIS much from its morning
--    reading; on overcast days, THIS much. The difference is the effect.
-- --------------------------------------------------------------------------
create or replace view v_weather_effects as
with f as (
  select *,
    case
      when cloud_mean is null then null
      when cloud_mean <= 1 then 'clear (0-1)'
      when cloud_mean <= 3 then 'few (2-3)'
      when cloud_mean <= 5 then 'scattered (4-5)'
      when cloud_mean <= 7 then 'broken (6-7)'
      else 'overcast (8)'
    end as cloud_band,
    case
      when dewpoint_depression_c is null then null
      when dewpoint_depression_c < 3  then 'humid (<3C)'
      when dewpoint_depression_c < 7  then 'moderate (3-7C)'
      when dewpoint_depression_c < 12 then 'dry (7-12C)'
      else 'very dry (>12C)'
    end as dryness_band,
    case when coalesce(precip_total, 0) > 0.01 then 'wet' else 'dry' end as precip_band
  from v_city_day_features
  where morning_to_max_c is not null and n_obs >= 12
)
select 'cloud'   as variable, cloud_band   as bucket, count(*)::int as n,
       round(avg(morning_to_max_c), 2) as avg_climb_c,
       round(avg(delta_max_c), 2)      as avg_day_change_c
  from f where cloud_band is not null group by cloud_band
union all
select 'dryness', dryness_band, count(*)::int,
       round(avg(morning_to_max_c), 2), round(avg(delta_max_c), 2)
  from f where dryness_band is not null group by dryness_band
union all
select 'precipitation', precip_band, count(*)::int,
       round(avg(morning_to_max_c), 2), round(avg(delta_max_c), 2)
  from f group by precip_band;


-- --------------------------------------------------------------------------
-- 4. Fitted coefficients, written by scripts/weather_model.py.
-- --------------------------------------------------------------------------
create table if not exists derived_weather_model (
  city_key       text not null,
  target         text not null default 'max_c',
  n_days         int,
  -- intercept + one coefficient per feature, as jsonb so the feature set can
  -- change without a migration
  coefficients   jsonb,
  -- what it is worth: both errors on the same held-out days
  mae_c          numeric,
  persistence_mae_c numeric,
  beats_persistence boolean,
  r2             numeric,
  notes          text,
  fitted_at      timestamptz not null default now(),
  primary key (city_key, target)
);

-- Why a variable is NOT in the model is worth as much as why one is: it says
-- this measurement, taken on every reading, does not move this city's
-- afternoon. Added rather than baked into the create so an existing table
-- gains it without being dropped.
alter table derived_weather_model add column if not exists selection jsonb;

comment on table derived_weather_model is
  'Per-city model of the daily maximum from morning conditions. beats_persistence is the only column that matters: a model that cannot beat yesterday-equals-today is not a model.';


do $ad4$
declare o text; r text;
begin
  foreach o in array array['v_city_day_features', 'v_persistence_skill', 'v_weather_effects',
                            'derived_weather_model'] loop
    foreach r in array array['anon', 'authenticated'] loop
      if exists (select 1 from pg_roles where rolname = r) then
        execute format('grant select on %I to %I', o, r);
      end if;
    end loop;
    if exists (select 1 from pg_roles where rolname = 'service_role') then
      execute format('grant all on %I to service_role', o);
    end if;
  end loop;
end
$ad4$;


-- Say what was actually created, by name.
--
-- "Success. No rows returned." is what the SQL editor prints whether this file
-- built four objects or was pasted in half. Later files then fail with
-- `relation "v_city_day_features" does not exist` and the run that "worked" is
-- the one nobody suspects. So the last thing this file does is look for its
-- own objects and list them - and shout if one is missing.
do $ad4$
declare
  v_rows int; v_cities int;
  o text; gone text[] := array[]::text[];
begin
  foreach o in array array['v_city_day_features', 'v_persistence_skill',
                           'v_weather_effects', 'derived_weather_model'] loop
    if to_regclass('public.' || o) is null then
      gone := array_append(gone, o);
    end if;
  end loop;

  if array_length(gone, 1) > 0 then
    raise exception 'ad4_21 did NOT create: %. Re-run the WHOLE file - a partial paste is the usual cause.',
      array_to_string(gone, ', ');
  end if;

  raise notice 'ad4_21: created v_city_day_features, v_persistence_skill, v_weather_effects, derived_weather_model';

  select count(*), count(distinct city_key) into v_rows, v_cities
    from v_city_day_features where morning_to_max_c is not null;
  raise notice 'ad4_21: % city-day(s) with morning features across % cities', v_rows, v_cities;
  raise notice 'ad4_21: fit with scripts/weather_model.py; read v_weather_effects for the plain version';
end
$ad4$;
