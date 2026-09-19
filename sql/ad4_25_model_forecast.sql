-- ===========================================================================
-- ad4_25_model_forecast.sql - AD4's OWN forecast, and the record of whether
-- it was any good.
--
-- Up to here the desk has two forecasts (Open-Meteo, NWS) and one model
-- (derived_weather_model, fitted in ad4_21 on observed mornings). The model
-- could only ever look BACKWARD: its inputs came from weather_observations,
-- so it explained a day after that day's morning had already happened.
--
-- ad4_24 removed that limit. weather_forecast_features carries the same
-- columns for days that have NOT happened yet, under the same names. So the
-- coefficients fitted on history can be applied to a forecast day directly,
-- and AD4 gets a third opinion that is ITS OWN - not a model it subscribes
-- to, but one fitted on its own archive of what these specific stations
-- actually did.
--
-- WHAT THIS IS NOT. These rows are deliberately NOT written into
-- weather_forecasts. v_forecast_divergence measures disagreement between
-- INDEPENDENT numerical models and widens sigma by the spread; AD4's own
-- post-processing of one of those models is not independent of it, and
-- feeding it back in would inflate the spread with its own reflection. It
-- stands beside them and is compared to them, which is the honest use.
--
-- Run order: after sql/ad4_21_weather_features.sql and
-- sql/ad4_24_nws_gridpoint.sql. Filled by scripts/weather_model.py.
-- Re-runnable.
-- ===========================================================================

do $ad4$
begin
  if to_regclass('public.cities') is null then
    raise exception 'ad4_25 needs cities - run sql/ad4_00_preflight.sql first';
  end if;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 1. The prediction, with its arithmetic kept alongside it.
--
--    contributions is the point of this table as much as predicted_max_c is.
--    A number with no derivation is not a reason, and the desk's whole claim
--    is that it can say WHY it disagrees with the market. Storing the
--    per-feature contribution means the UI shows the same decomposition the
--    prediction was actually made from, rather than a plausible story
--    reconstructed next to it.
-- --------------------------------------------------------------------------
create table if not exists derived_model_forecast (
  city_key          text        not null,
  for_date          date        not null,
  run_at            timestamptz not null,          -- the NWS run this came from
  lead_days         int,

  predicted_max_c   numeric,
  -- what the two published models say for the same day, carried along so a
  -- comparison costs no join at read time
  nws_max_c         numeric,
  -- yesterday's number this prediction was built on, and where it came from:
  -- 'observed' for a real archived maximum, 'chained' for the model's own
  -- prediction of the previous day. Error compounds along a chain and the
  -- row has to say so rather than presenting day 5 like day 0.
  prev_max_c        numeric,
  prev_source       text,

  -- + one entry per feature: {"intercept": 2.1, "cloud_mean": -1.83, ...}
  -- in DEGREES, already multiplied by the feature value
  contributions     jsonb,
  -- the feature values themselves, so a wrong prediction is diagnosable
  inputs            jsonb,

  -- the fitted model's measured skill on held-out days, copied at prediction
  -- time. A prediction from a model that loses to persistence is still
  -- written - and still marked, so nothing downstream has to trust it blind.
  model_mae_c       numeric,
  persistence_mae_c numeric,
  beats_persistence boolean,

  predicted_at      timestamptz not null default now(),
  primary key (city_key, for_date, run_at)
);

comment on table derived_model_forecast is
  'AD4''s own forward prediction of the daily maximum: ad4_21 coefficients applied to ad4_24 forecast conditions. Deliberately not in weather_forecasts - it is not independent of NWS and must not inflate v_forecast_divergence.';

-- The fit this prediction was made by, copied at prediction time so a later
-- refit cannot rewrite which coefficients produced a past number. Rule 5 of
-- sql/ad4_72_model_promotion.sql cannot be checked without it.
alter table derived_model_forecast add column if not exists model_version text;

create index if not exists dmf_city_date on derived_model_forecast (city_key, for_date desc, run_at desc);
create index if not exists dmf_version on derived_model_forecast (model_version);


-- --------------------------------------------------------------------------
-- 2. The freshest prediction per city-day.
-- --------------------------------------------------------------------------
create or replace view v_model_forecast_current as
select distinct on (city_key, for_date) *
from derived_model_forecast
where for_date >= current_date - 1
order by city_key, for_date, run_at desc;


-- --------------------------------------------------------------------------
-- 3. Where AD4 disagrees with the published forecasts - and only where its
--    own measured skill says the disagreement is worth anything.
--
--    disagreement_c is signed: positive means AD4 expects the day HOTTER
--    than NWS does, which is the direction that matters for a band above the
--    forecast. tradeable_view is the gate - a model that loses to persistence
--    on held-out days has no business moving a price, however confident the
--    arithmetic looks.
--
--    BEATING PERSISTENCE IS NOT ENOUGH, and that is what this gate used to
--    ask. beats_persistence is measured on held-out days of the model's OWN
--    TRAINING WINDOW, against yesterday-equals-today. The thing a price has
--    to beat is the PUBLIC FORECAST, on forward days nobody had seen when the
--    prediction was made. Those are different questions and a model can pass
--    the first while losing the second.
--
--    So the gate now also requires that this city AND THIS LEAD have been
--    promoted - see sql/ad4_72_model_promotion.sql. Leads are never averaged
--    together: a model sharp at lead 0 and useless on Friday looks fine as
--    one number and is not.
-- --------------------------------------------------------------------------
create or replace view v_model_disagreement as
select
  m.city_key,
  m.for_date,
  m.lead_days,
  m.predicted_max_c,
  m.nws_max_c,
  round(m.predicted_max_c - m.nws_max_c, 2)          as disagreement_c,
  m.beats_persistence,
  m.model_mae_c,
  m.persistence_mae_c,
  -- a disagreement smaller than the model's own error is noise, not a view,
  -- and a model nobody has promoted is not a view either
  (m.beats_persistence
     and m.model_mae_c is not null
     and abs(m.predicted_max_c - m.nws_max_c) > m.model_mae_c
     and p.state = 'promoted')                        as tradeable_view,
  m.prev_source,
  m.contributions,
  m.inputs,
  -- shadow | promoted | rejected | stale, or 'unjudged' where the promotion
  -- job has not scored this city and lead yet. Appended rather than inserted:
  -- create or replace can only add columns at the end.
  coalesce(p.state, 'unjudged')                       as promotion_state
from v_model_forecast_current m
left join derived_model_promotion p
       on p.city_key  = m.city_key
      and p.lead_days = m.lead_days
      and p.target    = 'max_c'
where m.predicted_max_c is not null;


-- --------------------------------------------------------------------------
-- 4. Was it any good? The forward equivalent of v_persistence_skill.
--
--    Scored against the same observed maximum the markets settle on, per
--    lead day, because a model that is sharp at lead 0 and useless at lead 4
--    looks fine averaged together and is not.
-- --------------------------------------------------------------------------
-- ADAPTIVE, for the same reason ad4_24's v_condition_skill is: this is the
-- only object here that needs another file's view, and a missing sql/ad4_21
-- must not abort the table, the two views the UI reads, or the grants.
do $ad4$
begin
  -- The CACHE, not the windowed view: v_city_day_features recomputes every
  -- day in the archive through a window function before anything can be
  -- filtered, and this view aggregates across all of it. Same rows, already
  -- computed, indexed. See ad4_44 for the measurements.
  if to_regclass('public.derived_city_day_features') is null then
    execute 'drop view if exists v_model_forecast_skill';
    raise notice 'ad4_25: v_model_forecast_skill SKIPPED - derived_city_day_features does not exist. Run sql/ad4_28_feature_cache.sql, then re-run this file. Everything else in ad4_25 is installed.';
    return;
  end if;

  execute $v$
    create or replace view v_model_forecast_skill as
    select
      m.city_key,
      m.lead_days,
      count(*)::int                                        as n_days,
      round(avg(abs(m.predicted_max_c - o.max_c)), 2)      as model_mae_c,
      round(avg(m.predicted_max_c - o.max_c), 2)           as model_bias_c,
      round(avg(abs(m.nws_max_c - o.max_c)), 2)            as nws_mae_c,
      round(avg(abs(m.prev_max_c - o.max_c)), 2)           as persistence_mae_c,
      (avg(abs(m.predicted_max_c - o.max_c)) < avg(abs(m.nws_max_c - o.max_c)))  as beat_nws,
      (avg(abs(m.predicted_max_c - o.max_c)) < avg(abs(m.prev_max_c - o.max_c))) as beat_persistence
    from derived_model_forecast m
    join derived_city_day_features o
      on o.city_key = m.city_key and o.obs_date = m.for_date and o.n_obs >= 12
    where m.predicted_max_c is not null
    group by m.city_key, m.lead_days
  $v$;

  execute $v$
    comment on view v_model_forecast_skill is
      'Forward skill per lead day, scored on outcomes. Averaging leads together hides a model that is sharp today and useless on Friday.'
  $v$;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 5. Grants.
-- --------------------------------------------------------------------------
do $ad4$
declare o text; r text;
begin
  foreach o in array array['derived_model_forecast', 'v_model_forecast_current',
                           'v_model_disagreement', 'v_model_forecast_skill'] loop
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
end
$ad4$;


-- --------------------------------------------------------------------------
-- 6. Say what is still missing, by name.
-- --------------------------------------------------------------------------
do $ad4$
declare n_fc int := 0; n_model int := 0;
begin
  if to_regclass('public.weather_forecast_features') is null then
    raise notice 'ad4_25: weather_forecast_features is missing - run sql/ad4_24_nws_gridpoint.sql, then n8n P1.4. Nothing can be predicted forward until it has rows.';
  else
    execute 'select count(*) from weather_forecast_features where for_date >= current_date'
      into n_fc;
    if n_fc = 0 then
      raise notice 'ad4_25: weather_forecast_features has no future days - run n8n P1.4 (NWS Gridpoint).';
    else
      raise notice 'ad4_25: % forecast city-day(s) available to predict from.', n_fc;
    end if;
  end if;

  if to_regclass('public.derived_weather_model') is null then
    raise notice 'ad4_25: derived_weather_model is missing - run sql/ad4_21_weather_features.sql.';
  else
    execute 'select count(*) from derived_weather_model where beats_persistence'
      into n_model;
    raise notice 'ad4_25: % city model(s) beat persistence and may move a price. Fit them with scripts/weather_model.py.', n_model;
  end if;

  raise notice 'ad4_25: filled by scripts/weather_model.py (it fits, then predicts forward in the same run).';
end
$ad4$;
