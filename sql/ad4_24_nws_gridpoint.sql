-- ===========================================================================
-- ad4_24_nws_gridpoint.sql - forecast the CONDITIONS, not just the temperature.
--
-- ad4_21 showed that a day's maximum is largely settled by things observable
-- in the morning: dewpoint depression, cloud, rain. But those come from
-- weather_observations, which means the desk can only EXPLAIN a day after its
-- morning has happened. It cannot use them to predict tomorrow.
--
-- api.weather.gov already forecasts every one of them. /gridpoints/{wfo}/{x},{y}
-- - the raw gridpoint, not the /forecast convenience endpoint AD4 has been
-- using - returns about forty time series, including skyCover, dewpoint,
-- relativeHumidity, probabilityOfPrecipitation and quantitativePrecipitation.
-- AD4 was taking the temperature out of it and discarding the rest.
--
-- THE DESIGN POINT. This table's columns are named to MATCH
-- v_city_day_features exactly - cloud_mean, dewpoint_depression_c,
-- precip_total, wind_mean. The model fitted on observed features
-- (scripts/weather_model.py) then applies to forecast features without any
-- translation layer, which is what turns "explain today" into "predict
-- tomorrow". A separate naming scheme here would have quietly made the two
-- incomparable.
--
-- Run order: after sql/ad4_21_weather_features.sql. Re-runnable.
-- ===========================================================================

do $ad4$
begin
  if to_regclass('public.cities') is null then
    raise exception 'ad4_24 needs cities - run sql/ad4_00_preflight.sql first';
  end if;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 1. Forecast conditions per city-day, per model run.
--
--    One row per (city, day, run): about 54 x 7 = 380 rows a run, not the
--    340,000 that storing every raw series point would cost. The aggregation
--    to a day is where the value is anyway - the model works on daily
--    features, so a per-hour archive would be storage without a consumer.
-- --------------------------------------------------------------------------
create table if not exists weather_forecast_features (
  city_key                text        not null,
  for_date                date        not null,
  run_at                  timestamptz not null,
  source                  text        not null default 'api.weather.gov',
  lead_days               int,

  -- the temperature envelope
  forecast_max_c          numeric,
  forecast_min_c          numeric,
  apparent_max_c          numeric,

  -- the conditions that decide where in that envelope the day lands.
  -- Names deliberately identical to v_city_day_features.
  morning_temp_c          numeric,
  morning_dewpoint_c      numeric,
  dewpoint_depression_c   numeric,
  morning_humidity        numeric,
  cloud_mean              numeric,   -- OKTAS 0-8, converted from NWS percent
  cloud_max               numeric,
  wind_mean               numeric,
  wind_max                numeric,
  precip_total            numeric,   -- inches, matching the observed column
  precip_probability      numeric,   -- percent, max across the day

  n_hours                 int,
  captured_at             timestamptz not null default now(),
  primary key (city_key, for_date, run_at)
);

comment on table weather_forecast_features is
  'Forecast conditions per city-day from the NWS raw gridpoint. Columns match v_city_day_features by name so a model fitted on observed features applies directly to forecast ones.';

create index if not exists wff_city_date on weather_forecast_features (city_key, for_date desc, run_at desc);


-- --------------------------------------------------------------------------
-- 2. The freshest forecast conditions per city-day.
-- --------------------------------------------------------------------------
create or replace view v_forecast_features as
select distinct on (city_key, for_date) *
from weather_forecast_features
order by city_key, for_date, run_at desc;


-- --------------------------------------------------------------------------
-- 3. Forecast against outcome, for the conditions themselves.
--
--    The desk measures how wrong the temperature forecast is. It has never
--    measured how wrong the CLOUD forecast is - and if cloud is worth -1C per
--    okta, a cloud forecast that is two oktas out is a 2C error entering the
--    model through a side door, invisible to every existing skill metric.
--
--    ADAPTIVE. This is the one thing in the file that needs another file's
--    view, and hard-referencing it made a missing sql/ad4_21 abort the WHOLE
--    of ad4_24 - the table, the freshest-forecast view, the P1.4 registration,
--    all of it - over a diagnostic. A missing prerequisite must cost the thing
--    that needs it, and nothing else. So: build it when the input is there,
--    and name the file that supplies it when it is not.
-- --------------------------------------------------------------------------
do $ad4$
begin
  if to_regclass('public.v_city_day_features') is null then
    execute 'drop view if exists v_condition_skill';
    raise notice 'ad4_24: v_condition_skill SKIPPED - v_city_day_features does not exist. Run sql/ad4_21_weather_features.sql, then re-run this file. Everything else in ad4_24 is installed.';
    return;
  end if;

  execute $v$
    create or replace view v_condition_skill as
    select
      f.city_key,
      count(*)::int                                              as n_days,
      round(avg(abs(f.cloud_mean - o.cloud_mean)), 2)            as cloud_mae_oktas,
      round(avg(f.cloud_mean - o.cloud_mean), 2)                 as cloud_bias_oktas,
      round(avg(abs(f.dewpoint_depression_c - o.dewpoint_depression_c)), 2) as dryness_mae_c,
      round(avg(f.dewpoint_depression_c - o.dewpoint_depression_c), 2)      as dryness_bias_c,
      round(avg(abs(f.forecast_max_c - o.max_c)), 2)             as max_mae_c
    from v_forecast_features f
    join v_city_day_features o
      on o.city_key = f.city_key and o.obs_date = f.for_date and o.n_obs >= 12
    where f.cloud_mean is not null and o.cloud_mean is not null
    group by f.city_key
  $v$;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 4. Grants.
-- --------------------------------------------------------------------------
do $ad4$
declare o text; r text;
begin
  foreach o in array array['weather_forecast_features', 'v_forecast_features', 'v_condition_skill'] loop
    if to_regclass('public.' || o) is null then continue; end if;   -- skipped above
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
end
$ad4$;


-- --------------------------------------------------------------------------
-- 5. Register P1.4 on the Workflows page and give it a schedule.
-- --------------------------------------------------------------------------
do $ad4$
begin
  if to_regclass('public.settings') is null then return; end if;

  update settings
     set value = value || jsonb_build_object(
           'P1.4_nws_gridpoint',
           jsonb_build_object('url', '', 'path', 'ad4-nws-gridpoint', 'label', 'NWS Gridpoint'))
   where key = 'n8n_webhooks' and not (value ? 'P1.4_nws_gridpoint');

  update settings
     set value = value || jsonb_build_object(
           'P1.4_nws_gridpoint', jsonb_build_object('mode', 'auto', 'every_minutes', 360))
   where key = 'workflow_schedules' and not (value ? 'P1.4_nws_gridpoint');
end
$ad4$;


do $ad4$
declare v_n int;
begin
  select count(*) into v_n from weather_forecast_features;
  raise notice 'ad4_24: weather_forecast_features has % row(s)', v_n;
  raise notice 'ad4_24: filled by n8n P1.4 NWS Gridpoint. Columns match v_city_day_features by name, so scripts/weather_model.py applies straight to them.';
end
$ad4$;
