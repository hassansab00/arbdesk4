-- ===========================================================================
-- THE MODEL INPUTS HAVE THEIR OWN FRESHNESS (audit P3, 5 Oct 2026)
--
-- The audit of 4 Oct: "Track ensemble/model input freshness separately from
-- the main forecast's freshness." The two are fetched on different clocks:
--
--   the main forecast    weather_forecasts, open_meteo_forecast (and NWS):
--                        every 3.00 h for all 48 cities (48 h to 5 Oct
--                        08:00Z: 768 runs, median and largest gap 3.00 h).
--                        Each call records its age (forecast_issued_at).
--   the model inputs     weather_forecast_models, 'open-meteo-models-current':
--                        the seven models' current run, fetched once a night
--                        by scripts/ingest_forecasts.py. 28 Sep - 5 Oct every
--                        city got one each night, all seven models, the
--                        cities' newest runs at most 0.94 h apart.
--
-- The model inputs reach pricing through derived_corrected_forecast (P3.9,
-- and the MOS blend that reads it). Its row records when the nightly fit
-- combined them (computed_at), and the engine's 36 h check reads that - but
-- the fit takes each model's NEWEST run whatever its age, so a city whose
-- current run the ingest missed is combined from the night before's and
-- stamped fresh. Nothing said so; v_data_freshness judges each table by its
-- newest row, which one fresh city keeps green.
--
-- 1. derived_corrected_forecast records the oldest and newest run it combined
--    (scripts/station_correction.py writes them from 6 Oct's fit on).
-- 2. v_city_forecast_inputs: per active city, the two clocks side by side,
--    each with its own verdict, and what today's and tomorrow's corrected
--    rows were combined from. Ages and counts only, never a forecast value;
--    the owner's view, as v_city_observation_health, so the page can read
--    it while weather_forecast_models and derived_corrected_forecast stay
--    closed to anon (v_data_freshness already shows anon their newest time).
--
-- Thresholds, each from a measurement or an existing spec:
--   main 'stale'       older than 8 h: weather_forecasts' fresh_hours in
--                      sql/ad4_39_freshness.sql (two missed 3-hourly fetches
--                      and a margin).
--   models 'stale'     older than 36 h: weather_forecast_models' fresh_hours
--                      there (a nightly feed and a margin).
--   models 'behind'    more than 6 h older than the newest current run any
--                      city has: the city missed the last nightly fetch
--                      (6 x the largest spread measured within one night).
--   models 'partial'   the newest run holds fewer than the 7 models the
--                      ingest asks for (ingest_forecasts.DEFAULT_MODELS; no
--                      workflow sets FORECAST_MODELS): a source missing, and
--                      under 4 the station correction combines nothing
--                      (station_correction.MIN_SOURCES; review of #312).
--   corrected 'earlier_run'  the row's oldest input more than 6 h older than
--                      the newest current run any city had when it was
--                      combined: a model's run came from an earlier night.
--
-- Re-runnable. Nothing here prices or trades. Read as anon after applying.
-- ===========================================================================

alter table public.derived_corrected_forecast add column if not exists inputs_oldest_run_at timestamptz;
alter table public.derived_corrected_forecast add column if not exists inputs_newest_run_at timestamptz;

comment on column public.derived_corrected_forecast.inputs_oldest_run_at is
  'The oldest of the model runs this row combined (each model''s newest run at the fit, whatever its age). computed_at says when they were combined; this says how old they were. Null before 6 Oct.';
comment on column public.derived_corrected_forecast.inputs_newest_run_at is
  'The newest of the model runs this row combined. Null before 6 Oct.';

do $$
begin
  if to_regclass('public.weather_forecasts') is not null
     and to_regclass('public.weather_forecast_models') is not null then
    execute $v$create or replace view public.v_city_forecast_inputs with (security_invoker = false) as
with clock as (
  select c.city_key,
         c.display_name,
         coalesce(c.timezone, 'UTC')                                    as timezone,
         (now() at time zone coalesce(c.timezone, 'UTC'))::date          as local_date
    from public.cities c
   where coalesce(c.status, 'active') = 'active'
),
-- The main forecast for the city's today, as the engine picks it
-- (probability_engine._forecast_for: shortest lead, newest run).
main as (
  select k.city_key, f.model, f.run_at
    from clock k
    left join lateral (
      select w.model, w.run_at
        from public.weather_forecasts w
       where w.city_key = k.city_key and w.for_date = k.local_date
       order by w.lead_days asc nulls last, w.run_at desc
       limit 1) f on true
),
-- The seven models' newest current run for the city, and how many models it holds.
models as (
  select k.city_key, r.run_at,
         (select count(distinct m.model)::int
            from public.weather_forecast_models m
           where m.city_key = k.city_key and m.source = 'open-meteo-models-current'
             and m.run_at = r.run_at)                                  as n_models
    from clock k
    left join lateral (
      select max(m.run_at) as run_at
        from public.weather_forecast_models m
       where m.city_key = k.city_key and m.source = 'open-meteo-models-current') r on true
),
batch as (
  select max(run_at) as newest from models
),
corrected as (
  select k.city_key, k.local_date, d.for_date, d.computed_at, d.inputs_oldest_run_at
    from clock k
    join public.derived_corrected_forecast d
      on d.city_key = k.city_key and d.for_date between k.local_date and k.local_date + 1
),
-- The newest current run any city had when each fit combined its rows.
fits as (
  select f.computed_at,
         (select max(m.run_at)
            from public.weather_forecast_models m
           where m.source = 'open-meteo-models-current'
             and m.run_at <= f.computed_at)                            as newest_before
    from (select distinct computed_at from corrected) f
),
judged as (
  select c.city_key, c.local_date, c.for_date, c.computed_at, c.inputs_oldest_run_at,
         round(extract(epoch from f.newest_before - c.inputs_oldest_run_at) / 3600, 2) as behind_h,
         case when c.inputs_oldest_run_at is null then 'not_recorded'
              when f.newest_before - c.inputs_oldest_run_at > interval '6 hours' then 'earlier_run'
              else 'current' end                                       as verdict
    from corrected c
    left join fits f using (computed_at)
)
select k.city_key,
       k.display_name,
       k.timezone,
       k.local_date,
       m.model                                                         as main_model,
       m.run_at                                                        as main_run_at,
       round(extract(epoch from now() - m.run_at) / 3600, 2)           as main_age_h,
       case when m.run_at is null then 'absent'
            when now() - m.run_at > interval '8 hours' then 'stale'
            else 'fresh' end                                           as main_verdict,
       md.run_at                                                       as models_run_at,
       md.n_models,
       round(extract(epoch from now() - md.run_at) / 3600, 2)          as models_age_h,
       round(extract(epoch from b.newest - md.run_at) / 3600, 2)       as models_behind_h,
       case when md.run_at is null then 'absent'
            when now() - md.run_at > interval '36 hours' then 'stale'
            when b.newest - md.run_at > interval '6 hours' then 'behind'
            when md.n_models < 7 then 'partial'
            else 'fresh' end                                           as models_verdict,
       t.computed_at                                                   as today_corrected_at,
       t.inputs_oldest_run_at                                          as today_inputs_oldest_at,
       t.behind_h                                                      as today_inputs_behind_h,
       coalesce(t.verdict, 'none')                                     as today_inputs_verdict,
       n.computed_at                                                   as tomorrow_corrected_at,
       n.inputs_oldest_run_at                                          as tomorrow_inputs_oldest_at,
       n.behind_h                                                      as tomorrow_inputs_behind_h,
       coalesce(n.verdict, 'none')                                     as tomorrow_inputs_verdict
  from clock k
  join main m using (city_key)
  join models md using (city_key)
  cross join batch b
  left join judged t on t.city_key = k.city_key and t.for_date = k.local_date
  left join judged n on n.city_key = k.city_key and n.for_date = k.local_date + 1$v$;
    execute $c$comment on view public.v_city_forecast_inputs is
  'Per active city, the main forecast''s freshness and the model inputs'' freshness, judged separately (audit P3): the main forecast the engine reads for today (weather_forecasts, stale past 8 h); the seven models'' newest current run (stale past 36 h, behind when more than 6 h older than the newest any city has: a missed nightly fetch, partial when it holds fewer than the 7 models); and what today''s and tomorrow''s station-corrected rows were combined from (earlier_run when an input was more than 6 h older than the newest run at the fit). Ages and counts only.'$c$;
    execute 'revoke all on public.v_city_forecast_inputs from public, anon, authenticated';
    execute 'grant select on public.v_city_forecast_inputs to anon, service_role';
  else
    raise notice 'weather_forecasts / weather_forecast_models not here; v_city_forecast_inputs not installed';
  end if;
end $$;
