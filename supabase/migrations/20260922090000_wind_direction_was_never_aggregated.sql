-- ===========================================================================
-- WIND DIRECTION WAS COLLECTED AND AGGREGATED NOWHERE
--
-- weather_observations.wind_dir_deg has existed since ad4_00 and is populated
-- on 130,572 of 144,601 rows - 90.3%. scripts/ingest_observations.py asks IEM
-- for it (`drct`), n8n P1.6 asks for it, both write it. And then it stops: the
-- `obs` CTE of v_city_day_features never selected the column, so the daily
-- aggregate had nothing to work with, and the only consumer in the whole repo
-- was scripts/live_weather.py turning it into a compass glyph for the UI.
--
-- Wind direction is a first-order control on a daily maximum at a good number
-- of these stations - downslope, offshore against onshore, foehn. Measured on
-- this repo's own archive, the day's climb (morning_to_max_c) splits by
-- resultant octant with a spread of 8.08 C at Sao Paulo, 4.81 C at Cape Town
-- and 4.16 C at Warsaw. On a held-out OLS over 205 city-days it is worth about
-- 0.135 C of MAE on average and 0.26 to 0.70 C on the cities where the
-- mechanism is strongest - against band widths of a degree or two.
--
-- A MEAN OF DEGREES IS NOT A MEAN WIND: 350 and 10 average to 180, which is
-- the opposite direction. The bearing is resolved into components first, and
-- what is stored is the speed-weighted resultant divided by the scalar wind
-- run, so each component is the direction's east/north share TIMES the day's
-- directional constancy. Dimensionless and bounded by [-1, 1], which is what
-- lets one coefficient mean the same thing in Denver and in Singapore.
--
-- Meteorological convention: the bearing is where the wind comes FROM, so the
-- vector it blows TOWARD is (-sin, -cos).
--
-- WHY THE FORECAST SIDE IS IN A MIGRATION AND THE OBSERVED SIDE IS NOT.
-- weather_forecast_features is in the fixture tests/database/paper-contracts
-- builds, so a migration against it does real work there. derived_city_day_-
-- features is created by sql/ad4_28_feature_cache.sql, which that harness
-- never applies - a migration touching it would fail with
-- `relation "public.derived_city_day_features" does not exist`. Its columns
-- are added by sql/ad4_00_preflight.sql's column registry instead.
--
-- AND WHY BOTH SIDES HAVE TO LAND TOGETHER. scripts/weather_model.py's own
-- comment states the rule: a feature present on only one side can be fitted
-- and then never applied, and the failure is SILENT - forecast_city skips any
-- row missing a feature the fit kept, so the city simply stops producing
-- forward predictions, exit 0, no note. That is not hypothetical here; it
-- cost six cities their forward predictions in September when a hand-written
-- column list kept wind_max on one side only.
-- ===========================================================================

alter table public.weather_forecast_features
  add column if not exists wind_u_mean numeric;
alter table public.weather_forecast_features
  add column if not exists wind_v_mean numeric;

comment on column public.weather_forecast_features.wind_u_mean is
  'Speed-weighted daytime resultant, east component, divided by the scalar wind run: the direction''s east share times the day''s directional constancy. Bounded [-1, 1]. Positive is wind blowing toward the east.';
comment on column public.weather_forecast_features.wind_v_mean is
  'Speed-weighted daytime resultant, north component, divided by the scalar wind run. Bounded [-1, 1]. Positive is wind blowing toward the north.';

-- APPENDED AT THE END, beside the pressure columns that were appended for the
-- same reason. `create or replace view` may only ADD columns after the
-- existing ones; slotting these in beside wind_mean where they belong
-- logically renames every column after that point and Postgres refuses with
-- 42P16. The alternative is DROP VIEW ... CASCADE to buy a tidier column
-- order, which is not a trade worth making - nothing reads this view by
-- position.
create or replace view v_forecast_features as
select distinct on (city_key, for_date)
       city_key, for_date, run_at, source, lead_days,
       forecast_max_c, forecast_min_c, apparent_max_c,
       morning_temp_c, morning_dewpoint_c, dewpoint_depression_c,
       morning_humidity,
       cloud_mean, cloud_max, wind_mean, wind_max,
       precip_total, precip_probability, n_hours, captured_at,
       morning_pressure_hpa, pressure_change_24h_hpa,
       wind_u_mean, wind_v_mean
  from weather_forecast_features
 order by city_key, for_date, run_at desc;

grant select on v_forecast_features to anon, authenticated, service_role;
