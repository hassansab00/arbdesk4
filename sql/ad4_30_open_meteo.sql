-- ===========================================================================
-- ad4_30_open_meteo.sql - a global source, because NWS is not one.
--
-- THE GAP THIS CLOSES. api.weather.gov covers the United States and its
-- territories, and nothing else. /points/{lat},{lon} answers 404 for Warsaw,
-- Ankara, Moscow and Jinan, so n8n P1.2 marks them nws_supported = false and
-- never asks again - correctly, for that API. The consequence was not
-- correct: those cities then had NO live reading and NO forward forecast from
-- any job at all.
--
--   scripts/ingest_observations.py  IEM METAR         global, but past only
--   scripts/ingest_forecasts.py     Open-Meteo        global, but its window
--                                   previous-runs     is today-10 -> today.
--                                                     It backfills what was
--                                                     forecast, never what
--                                                     WILL happen.
--   n8n P1.2 / P1.3 / P1.4          api.weather.gov   US only
--
-- So nothing in this desk forecast tomorrow for a non-US city, and nothing
-- showed a current temperature for one. "Live weather 42 hours old, forecast
-- three days old" was not a stale job; it was an absent one.
--
-- WHAT THIS IS NOT. It is not a claim about settlement. Every city here
-- resolves on weather.gov/wrh/timeseries?site=<icao> - a DIFFERENT surface
-- from api.weather.gov, and a global one. nws_supported = false means "the
-- JSON API does not cover this point", never "weather.gov does not". The
-- comment on that column now says so, because reading it the other way is
-- exactly the mistake that produced this gap.
--
-- WHY OPEN-METEO IS NOT AN OBSERVATION. Its `current` block is model output
-- interpolated to a point, not a station reading. weather_observations is the
-- settlement evidence base and what the model is FITTED on; putting model
-- output in it would train the model on its own kind of guess and destroy the
-- comparison in docs/settlement_verification.md. So this writes live_weather
-- (a display table) and weather_forecasts / weather_forecast_features
-- (forecasts, which is what it is), and never weather_observations.
--
-- Run order: any time after sql/ad4_24_nws_gridpoint.sql. Re-runnable.
-- ===========================================================================

do $ad4$
begin
  if to_regclass('public.live_weather') is null then
    raise exception 'ad4_30 needs live_weather - run sql/ad4_live_weather.sql first';
  end if;
  if to_regclass('public.weather_forecast_features') is null then
    raise exception 'ad4_30 needs weather_forecast_features - run sql/ad4_24_nws_gridpoint.sql first';
  end if;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 1. live_weather has to say where a reading came from.
--
--    It carries one row per city and four jobs can write it. Until now
--    nothing recorded which, so a station reading and a model interpolation
--    were indistinguishable on the page - and they are not the same claim.
--    A METAR temperature is measured at the airport the market settles on; an
--    Open-Meteo current is a model's opinion about that coordinate.
-- --------------------------------------------------------------------------
alter table live_weather add column if not exists source text;
alter table live_weather add column if not exists source_kind text;

comment on column live_weather.source is
  'Which feed wrote this row: NWS, IEM, or open-meteo.';
comment on column live_weather.source_kind is
  'station = a real instrument reading at the ICAO the market settles on. model = interpolated model output, which is an opinion about that point and not a measurement. The distinction matters: only a station reading is evidence about settlement.';

-- Everything already there came from a station feed - P1.2 (NWS) or the
-- Live Weather Monitor action (IEM). Backfill rather than leave it null,
-- so "unknown" means unknown rather than "old".
update live_weather set source_kind = 'station' where source_kind is null;


-- --------------------------------------------------------------------------
-- 2. Say what nws_supported actually measures.
--
--    The column name reads as "weather.gov has this city", the value means
--    "api.weather.gov has this point". Every city in this desk resolves on
--    weather.gov; only the US ones are on that JSON API.
-- --------------------------------------------------------------------------
comment on column cities.nws_supported is
  'FALSE means api.weather.gov (the JSON API) returned 404 for this point - it covers the US and its territories only. It does NOT mean weather.gov has no data for this city: every city here resolves on weather.gov/wrh/timeseries?site=<icao>, which is global and is the settlement surface. Reading this column as "weather.gov does not cover it" is what left the non-US cities with no live reading and no forward forecast.';


-- --------------------------------------------------------------------------
-- 3. Which cities have a forward forecast from which model, and which have
--    none. The question nobody could answer, which is why nobody noticed.
-- --------------------------------------------------------------------------
create or replace view v_forecast_coverage as
with horizon as (
  select c.city_key,
         c.display_name,
         c.nws_supported,
         f.model,
         max(f.for_date)                                   as furthest_day,
         count(distinct f.for_date) filter (
           where f.for_date >= current_date)               as future_days,
         max(f.run_at)                                     as last_run_at
    from cities c
    left join weather_forecasts f
      on f.city_key = c.city_key
     and f.for_date >= current_date - 1
   where coalesce(c.status, 'active') = 'active'
   group by 1, 2, 3, 4
)
select
  city_key,
  display_name,
  nws_supported,
  -- one row per city: which models reach forward, and how far
  count(*) filter (where model is not null and future_days > 0)   as models_with_a_future,
  max(future_days)                                                 as days_ahead,
  max(furthest_day)                                                as furthest_day,
  max(last_run_at)                                                 as last_run_at,
  string_agg(distinct model, ', ' order by model)
    filter (where future_days > 0)                                 as models,
  case
    when count(*) filter (where model is not null and future_days > 0) = 0
      then 'NO FORWARD FORECAST'
    when count(*) filter (where model is not null and future_days > 0) = 1
      then 'one model - no disagreement, so sigma cannot widen'
    else 'ok'
  end                                                              as verdict
from horizon
group by 1, 2, 3
order by models_with_a_future asc, city_key;

comment on view v_forecast_coverage is
  'Which cities can actually be traded tomorrow. A city with NO FORWARD FORECAST has no price to disagree with; a city with one model has a spread of zero, so every band looks equally likely and the edge engine has nothing to say.';


-- --------------------------------------------------------------------------
-- 4. Register P1.5 on the Workflows page and give it a cadence.
-- --------------------------------------------------------------------------
do $ad4$
begin
  if to_regclass('public.settings') is null then return; end if;

  update settings
     set value = value || jsonb_build_object(
           'P1.5_open_meteo',
           jsonb_build_object('url', '', 'path', 'ad4-open-meteo', 'label', 'Open-Meteo Global'))
   where key = 'n8n_webhooks' and not (value ? 'P1.5_open_meteo');

  -- Every 3 hours. Open-Meteo refreshes its current block every 15 minutes
  -- and its forecast hourly; three hours keeps live_weather usable without
  -- spending executions on a number that moves slowly.
  update settings
     set value = value || jsonb_build_object(
           'P1.5_open_meteo', jsonb_build_object('mode', 'auto', 'every_minutes', 180))
   where key = 'workflow_schedules' and not (value ? 'P1.5_open_meteo');
end
$ad4$;


-- --------------------------------------------------------------------------
-- 5. Grants. Read-only for the browser; the view is safe to expose.
-- --------------------------------------------------------------------------
do $ad4$
declare r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_forecast_coverage to %I', r);
    end if;
  end loop;
end
$ad4$;


do $ad4$
declare v_none int; v_one int; v_total int;
begin
  select count(*) filter (where verdict = 'NO FORWARD FORECAST'),
         count(*) filter (where verdict like 'one model%'),
         count(*)
    into v_none, v_one, v_total
    from v_forecast_coverage;
  raise notice 'ad4_30: % of % city/cities have NO forward forecast, % have only one model',
    v_none, v_total, v_one;
  raise notice 'ad4_30: import n8n/P1.5_open_meteo.template.json - it is global, so it covers the cities api.weather.gov does not.';
  raise notice 'ad4_30: read select * from v_forecast_coverage; after it runs.';
end
$ad4$;
