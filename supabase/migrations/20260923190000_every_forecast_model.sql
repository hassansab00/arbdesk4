-- ===========================================================================
-- EVERY FORECAST MODEL, IN ITS OWN TABLE (plan v2.1 P2.8)
--
-- On 23 Sep weather_forecasts held one forecast series per city - Open-Meteo's
-- best_match, its own choice of model per location - plus NWS for 12 cities.
-- The hit tournament (P3.8) needs every model to choose between, so the
-- previous-runs ingest now also asks for ECMWF, GFS, ICON, UKMO, JMA, GEM and
-- Meteo-France by name.
--
-- WHY NOT weather_forecasts. About 35 files read it (scripts and sql/), and
-- several pick "the newest, shortest-lead row, whatever the model" -
-- probability_engine._forecast_for orders by lead_days, run_at and nothing
-- else. Per-model previous-runs rows carry the same synthetic midnight run_at
-- as best_match, so in that table they would tie with it, and which model
-- priced a city would be left to row order. Here they cannot reach a single
-- existing reader; the tournament reads them on purpose, and a model reaches
-- pricing only through a recipe that has passed its gate.
--
-- Same shape and key as weather_forecasts. issued_at semantics are P2.6's
-- 'ingest_time_true_issue_unverified': observed_at is when we fetched it.
-- ===========================================================================

create table if not exists public.weather_forecast_models (
  city_key        text        not null,
  model           text        not null,
  run_at          timestamptz not null,
  for_date        date        not null,
  lead_days       int         not null,
  forecast_max_c  numeric     not null,
  source          text        not null default 'open-meteo-previous-runs',
  observed_at     timestamptz not null default now(),
  primary key (city_key, model, run_at, for_date)
);

create index if not exists weather_forecast_models_date
  on public.weather_forecast_models (city_key, for_date, lead_days);

comment on table public.weather_forecast_models is
  'One day-ahead maximum per (city, model, run, date) from Open-Meteo previous-runs, each model by name (plan v2.1 P2.8). Kept apart from weather_forecasts so no existing reader can pick a model by row order; the hit tournament (P3.8) reads it.';

alter table public.weather_forecast_models enable row level security;
revoke all on public.weather_forecast_models from public, anon, authenticated;
grant select, insert on public.weather_forecast_models to service_role;
