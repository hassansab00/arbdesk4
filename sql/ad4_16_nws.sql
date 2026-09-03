-- ===========================================================================
-- ad4_16_nws.sql - make room for api.weather.gov alongside the existing feeds.
--
-- weather.gov is the surface 50 of AD4's 54 markets actually settle on, and
-- until now the repo reached it by scraping an HTML page - a parser that was
-- never finished (scripts/live_weather.py:fetch_primary_reading returns None).
-- api.weather.gov serves the same NWS data as documented JSON, with quality
-- control flags, no rate limit, and a station-reported daily maximum.
--
-- Almost nothing new is needed. The existing tables already fit:
--
--   weather_observations   source='NWS' rows sit beside source='IEM' ones;
--                          the unique key is (city_key, valid_at, source), so
--                          both feeds coexist per timestamp and can be COMPARED
--   weather_forecasts      model='nws' rows sit beside open-meteo's; that is
--                          what makes forecast DIVERGENCE measurable
--   weather_events         NWS heat advisories land here as kind='nws_alert',
--                          which means P1.1 emails them and the UI feed shows
--                          them live, with no new plumbing at all
--
-- This file adds only what genuinely has nowhere to live: the per-city NWS
-- identifiers, today's solar transit, and a view over forecast disagreement.
--
-- Run order: after sql/ad4_15_pipeline_fixes.sql. Re-runnable.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- 1. Per-city NWS identifiers.
--
--    /points/{lat},{lon} resolves a coordinate to its forecast gridpoint and
--    its observation stations. That lookup is stable for a location, so it is
--    cached here rather than repeated on every run. nws_checked_at records
--    when we last asked, including when the answer was "not a US location" -
--    so a non-US city is asked once, not every cycle.
-- --------------------------------------------------------------------------
do $ad4$
declare
  r record;
begin
  for r in select * from (values
      ('cities','nws_station_id','text'),
      ('cities','nws_grid_wfo','text'),
      ('cities','nws_grid_x','integer'),
      ('cities','nws_grid_y','integer'),
      ('cities','nws_supported','boolean'),
      ('cities','nws_checked_at','timestamptz'),
      -- solar transit is the sun's zenith: the physical anchor of the daily
      -- temperature peak. derived_weather_peak holds a MONTHLY historical
      -- average; this is the actual figure for today, per location.
      ('live_weather','solar_transit_at','timestamptz'),
      ('live_weather','sunrise_at','timestamptz'),
      ('live_weather','sunset_at','timestamptz'),
      ('live_weather','nws_alert','text'),
      ('live_weather','nws_alert_severity','text'),
      ('live_weather','max_temp_24h_c','numeric'),
      ('live_weather','obs_source','text')
    ) as t(tbl, col, typ)
  loop
    if to_regclass('public.' || r.tbl) is null then
      raise notice 'ad4_16: table % missing - skipped', r.tbl;
      continue;
    end if;
    if not exists (select 1 from information_schema.columns
                    where table_schema = 'public' and table_name = r.tbl
                      and column_name = r.col) then
      execute format('alter table %I add column %I %s', r.tbl, r.col, r.typ);
      raise notice 'ad4_16: added %.%', r.tbl, r.col;
    end if;
  end loop;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 2. Settings. All PROVISIONAL and UI-tunable - nothing here is measured.
-- --------------------------------------------------------------------------
insert into settings (key, value) values
  ('nws', '{
     "note": "api.weather.gov settings. US stations only - non-US cities 404 and are skipped after one check. A User-Agent identifying the app is required by the API.",
     "user_agent": "ArbDesk4 (github.com/hassansab00/arbdesk4)",
     "alert_events": ["Excessive Heat Warning", "Heat Advisory", "Extreme Heat Warning", "Extreme Heat Watch", "Excessive Heat Watch"],
     "alert_severity_floor": "Moderate",
     "prefer_nws_observations": true,
     "forecast_model_label": "nws"
   }'::jsonb)
on conflict (key) do nothing;

insert into settings (key, value) values
  ('forecast_divergence', '{
     "note": "NO evidential basis - PROVISIONAL. When two independent models disagree about today the day is genuinely less certain than the historical MAE alone implies, so sigma widens. It can only ever WIDEN: the multiplier is clamped at a floor of 1.0, so a second source can never make AD4 more confident than its own measured skill says it should be. Set enabled=false to ignore divergence entirely.",
     "enabled": true,
     "c_per_multiplier": 2.0,
     "max_multiplier": 2.0,
     "min_models": 2
   }'::jsonb)
on conflict (key) do nothing;


-- --------------------------------------------------------------------------
-- 3. Forecast divergence.
--
--    One row per city per target date: how far apart the latest run of each
--    model is about the same day. AD4's sigma is otherwise built entirely
--    from HISTORICAL skill (mae_c) - this is the only signal it has about how
--    uncertain TODAY is.
--
--    spread_c is max - min across models. With one model it is 0 and the
--    multiplier is 1.0, so a database with only open-meteo behaves exactly as
--    it does now.
-- --------------------------------------------------------------------------
create or replace view v_forecast_divergence as
with latest_per_model as (
  select distinct on (city_key, model, for_date)
    city_key, model, for_date, lead_days, forecast_max_c, run_at
  from weather_forecasts
  where forecast_max_c is not null
  order by city_key, model, for_date, run_at desc
),
cfg as (
  select
    coalesce(((select value from settings where key = 'forecast_divergence')->>'c_per_multiplier')::numeric, 2.0) as c_per,
    coalesce(((select value from settings where key = 'forecast_divergence')->>'max_multiplier')::numeric, 2.0) as cap,
    coalesce(((select value from settings where key = 'forecast_divergence')->>'min_models')::int, 2) as min_models,
    coalesce(((select value from settings where key = 'forecast_divergence')->>'enabled')::boolean, true) as enabled
)
select
  f.city_key,
  f.for_date,
  min(f.lead_days)                              as lead_days,
  count(*)::int                                 as n_models,
  string_agg(f.model, ', ' order by f.model)    as models,
  round(min(f.forecast_max_c), 2)               as min_c,
  round(max(f.forecast_max_c), 2)               as max_c,
  round(avg(f.forecast_max_c), 2)               as mean_c,
  round(max(f.forecast_max_c) - min(f.forecast_max_c), 2) as spread_c,
  max(f.run_at)                                 as latest_run_at,
  -- The multiplier probability_engine applies to sigma. Never below 1.0:
  -- disagreement can only ever make AD4 less sure, never more.
  case
    when not c.enabled then 1.0
    when count(*) < c.min_models then 1.0
    else least(c.cap,
               greatest(1.0,
                        1.0 + (max(f.forecast_max_c) - min(f.forecast_max_c)) / nullif(c.c_per, 0)))
  end::numeric(6,4) as sigma_multiplier
from latest_per_model f
cross join cfg c
group by f.city_key, f.for_date, c.enabled, c.min_models, c.cap, c.c_per;


-- --------------------------------------------------------------------------
-- 3b. Register the two NWS workflows with the Workflows page.
--
--     sql/ad4_14_workflows.sql seeds settings.n8n_webhooks with `on conflict
--     do nothing`, so a database that already ran it will NOT pick up the new
--     entries from re-running that file. Merge them in here instead - and only
--     the keys that are missing, so a URL already pasted in the UI survives.
-- --------------------------------------------------------------------------
do $ad4$
declare
  r record;
begin
  if not exists (select 1 from settings where key = 'n8n_webhooks') then
    raise notice 'ad4_16: settings.n8n_webhooks absent - run sql/ad4_14_workflows.sql first';
    return;
  end if;
  for r in select * from (values
      ('P1.2_nws_monitor',  'ad4-nws-monitor',  'NWS Monitor'),
      ('P1.3_nws_forecast', 'ad4-nws-forecast', 'NWS Forecast')
    ) as t(job, path, label)
  loop
    update settings
       set value = value || jsonb_build_object(
             r.job, jsonb_build_object('url', '', 'path', r.path, 'label', r.label))
     where key = 'n8n_webhooks'
       and not (value ? r.job);
    if found then
      raise notice 'ad4_16: registered % in settings.n8n_webhooks', r.job;
    end if;
  end loop;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 4. Grants. Section 8 of ad4_13_reconcile.sql revoked everything and granted
--    back a fixed list, so a view created after it needs SELECT re-granted.
-- --------------------------------------------------------------------------
do $ad4$
declare r text;
begin
  foreach r in array array['anon','authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_forecast_divergence to %I', r);
    end if;
  end loop;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant select on v_forecast_divergence to service_role;
  end if;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 5. Report.
-- --------------------------------------------------------------------------
do $ad4$
declare
  v_cities int;
  v_us     int;
  v_div    int;
begin
  select count(*) into v_cities from cities;
  select count(*) into v_us from cities where nws_supported is true;
  select count(*) into v_div from v_forecast_divergence;

  raise notice 'ad4_16: % cities, % confirmed NWS-supported (0 until P1.2 runs its first /points lookup)',
               v_cities, v_us;
  raise notice 'ad4_16: v_forecast_divergence has % row(s)', v_div;
  raise notice 'ad4_16: sigma widens only once a SECOND forecast model writes to weather_forecasts';
end
$ad4$;
