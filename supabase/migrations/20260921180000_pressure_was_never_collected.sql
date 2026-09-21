-- ===========================================================================
-- THE DESK HAS READ PRESSURE SINCE DAY ONE AND HAS NEVER SEEN ONE.
--
-- weather_observations.pressure_hpa has existed since ad4_00.
-- v_city_day_features derives morning_pressure_hpa from it and
-- pressure_change_24h_hpa as the 24-hour lag difference, with a comment
-- explaining exactly why the change is the part that matters: "falling
-- pressure is an approaching front, and a front is exactly when a
-- persistence-style forecast fails". derived_city_day_features caches both.
-- ad4_28 buckets the change into "falling hard / falling / steady / rising"
-- for the reasoning panel.
--
-- Every one of those reads a column nothing has ever written. Measured
-- before this migration:
--
--     weather_observations      142,529 rows,      0 with a pressure
--       - from IEM              110,953 rows,      0
--       - from NWS               31,576 rows,      0
--     derived_city_day_features  22,294 city-days, 0 with morning pressure
--
-- The schema said this desk understood pressure. The data said it had never
-- been offered one. Two separate causes:
--
--   * scripts/ingest_observations.py asked IEM for
--     tmpf,dwpf,relh,drct,sknt,p01i,skyc1 - no pressure field at all
--   * n8n P1.5 fetches surface_pressure from Open-Meteo, writes it into
--     live_weather, and drops it when it builds the forecast-feature row
--
-- MSL, NOT STATION PRESSURE, EVERYWHERE. This is the trap that makes a
-- half-done version worse than none. Station pressure is mostly altitude:
-- Mexico City sits at 2,240 m and reads about 770 hPa on its calmest day,
-- Singapore about 1,010. A model fitted across cities on that is reading a
-- map, not the weather. Worse, the model TRAINS on observed rows and
-- PREDICTS on forecast rows, so if one side is MSL and the other is station
-- pressure the fit is silently fed a number 240 hPa out for some cities and
-- correct for others.
--
-- So: IEM `mslp`, Open-Meteo `pressure_msl`, and the same column name on
-- both sides - which is the convention the rest of this pipeline already
-- follows and the reason a forecast row can be read by a model fitted on
-- observed days with no translation.
-- ===========================================================================
alter table public.weather_forecast_features
  add column if not exists morning_pressure_hpa    numeric,
  add column if not exists pressure_change_24h_hpa numeric;

comment on column public.weather_forecast_features.morning_pressure_hpa is
  'Mean-sea-level pressure at the city''s morning anchor hour, hPa. Same quantity and same name as derived_city_day_features.morning_pressure_hpa so a model fitted on observed days reads a forecast day untranslated.';
comment on column public.weather_forecast_features.pressure_change_24h_hpa is
  'Morning MSL pressure minus the previous forecast day''s, hPa. The level is near-constant for a city and the intercept absorbs it; the CHANGE is the approaching front, which is when persistence fails.';


-- v_forecast_features names its columns explicitly, so a column added to the
-- table is invisible to predict_forward until it is added here too. That is
-- the whole failure mode of this file in miniature: a value collected,
-- stored, and never reaching the thing that needed it.
-- APPENDED AT THE END, not slotted in beside morning_humidity where they
-- belong logically. `create or replace view` may only ADD columns after the
-- existing ones - putting them mid-list renames every column after that
-- point and Postgres refuses:
--
--     42P16: cannot change name of view column "cloud_mean" to
--            "morning_pressure_hpa"
--
-- The alternative is DROP VIEW ... CASCADE, which would take out whatever
-- else has come to depend on this one to buy a tidier column order. Not a
-- trade worth making; nothing reads this view by position.
create or replace view v_forecast_features as
select distinct on (city_key, for_date)
       city_key, for_date, run_at, source, lead_days,
       forecast_max_c, forecast_min_c, apparent_max_c,
       morning_temp_c, morning_dewpoint_c, dewpoint_depression_c,
       morning_humidity,
       cloud_mean, cloud_max, wind_mean, wind_max,
       precip_total, precip_probability, n_hours, captured_at,
       morning_pressure_hpa, pressure_change_24h_hpa
  from weather_forecast_features
 order by city_key, for_date, run_at desc;

grant select on v_forecast_features to anon, authenticated, service_role;
