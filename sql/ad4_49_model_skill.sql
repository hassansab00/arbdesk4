-- ===========================================================================
-- ad4_49_model_skill.sql - SKILL PER MODEL, AND A DETERMINISTIC FORECAST PICK.
--
-- Safe to run any time. Creates one table, one view, one index. Changes no
-- existing relation, so nothing that reads derived_forecast_skill today sees
-- a different row tomorrow.
--
--
-- WHAT WAS WRONG - TWO THINGS, ONE ROOT
--
-- 1. SKILL WAS POOLED ACROSS MODELS. measure_skill.py grouped errors by
--    (city, lead) and threw the model away, so the error of every model that
--    ever wrote a row for a city landed in one mae_c. Three models write to
--    weather_forecasts on this desk right now:
--
--        open_meteo_best_match   328,285 rows   2024-01-01 -> 2026-09-03
--        open_meteo_forecast       1,184 rows   from 2026-09-06
--        nws                          84 rows   from 2026-09-06, 12 cities
--
--    Today the pooled number is 99.6% best_match, so pooling costs almost
--    nothing YET. It starts lying the moment the other two accumulate
--    history, and by then every sigma on the desk is built on the blend.
--
-- 2. THE FORECAST PICK WAS NOT DETERMINISTIC. probability_engine ordered by
--    lead_days alone and took the first row. The identity of a forecast is
--    (city, model, run_at, for_date) - that is already this table's unique
--    key, ad4_uq_wx_fc - so rows tied at the shortest lead are ordinary and
--    expected, and which one PostgREST hands back is arbitrary.
--
--    Measured on the live archive: 360 (city, for_date, lead) keys across 37
--    cities currently hold more than one row, disagreeing by 1.28 C on
--    average. The worst is Denver for 2026-09-13, three runs at lead 7:
--
--        run 2026-09-06 15:21   11.2 C    max_at_local 2026-09-13T00:00
--        run 2026-09-06 21:00   30.3 C    max_at_local 2026-09-13T14:00
--        run 2026-09-07 03:00   26.2 C    max_at_local 2026-09-13T12:00
--
--    A 19.1 C spread, and the engine was free to price against the 11.2 -
--    a run whose "daily max" fell at midnight, which is the signature of a
--    truncated forecast day rather than a real overnight peak. Ordering by
--    run_at desc retires that row without a special case, because it is
--    simply the oldest of the three.
--
--
-- WHY A SEPARATE TABLE AND NOT A model COLUMN
--
-- Five views read derived_forecast_skill through
--
--     select distinct on (city_key) ... order by city_key, computed_at desc
--
-- (v_city_stats, v_trade_plan, the stats cache, v_sigma_inputs, and the
-- reasoning builder). Per-model rows share their computed_at with the pooled
-- row, so adding them to that table would put several rows in each distinct
-- on group tied on the ordering key - and distinct on then picks one
-- ARBITRARILY. That is the very bug this file exists to remove, so putting
-- it into five more views to fix it in one place is not a trade worth making.
--
-- derived_forecast_skill therefore keeps its exact present meaning: the
-- desk's skill number, one row per (city, lead, computed_at). The per-model
-- breakdown lives here, at its own grain, and no existing consumer changes.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- 1. The per-model grain.
--
-- Same columns as derived_forecast_skill so the two can be compared without
-- a translation layer, plus the model that earned them.
-- --------------------------------------------------------------------------
create table if not exists derived_forecast_skill_model (
  city_key             text not null,
  model                text not null,
  computed_at          timestamptz not null default now(),
  lead_days            int not null,
  n_days               int,
  mae_c                numeric,
  bias_c               numeric,
  p90_abs_err_c        numeric,
  mae_bands            numeric,
  pct_within_one_band  numeric,
  band_width_c         numeric,
  evidence_scope       text,
  primary key (city_key, model, computed_at, lead_days)
);

alter table derived_forecast_skill_model
  add column if not exists evidence_scope text;

comment on table derived_forecast_skill_model is
  'Forecast skill scored per (city, model, lead), the grain at which error is actually generated. derived_forecast_skill holds the pooled number the desk prices with; this holds the breakdown behind it, and is what the probability engine reads when it knows which model produced the forecast it is pricing.';

-- The lookup the engine makes: newest row for one city, one model, one lead.
create index if not exists ad4_ix_dfsm_city_model_lead
  on derived_forecast_skill_model (city_key, model, lead_days, computed_at desc);

-- The freshness probe reads max(computed_at) across the whole table.
create index if not exists ad4_ix_fresh_derived_forecast_skill_model
  on derived_forecast_skill_model (computed_at desc nulls last);


-- --------------------------------------------------------------------------
-- 2. Make the deterministic pick cheap.
--
-- idx_fc_city_date is (city_key, for_date, lead_days), which served the old
-- ordering exactly. The new one adds run_at desc as a fourth key so
-- "shortest lead, newest run" is an index walk rather than a sort.
-- --------------------------------------------------------------------------
create index if not exists ad4_ix_fc_city_date_lead_run
  on weather_forecasts (city_key, for_date, lead_days, run_at desc);


-- --------------------------------------------------------------------------
-- 3. Read it as anon, same as every other derived table.
-- --------------------------------------------------------------------------
do $ad4$
begin
  alter table derived_forecast_skill_model enable row level security;
  drop policy if exists anon_read on derived_forecast_skill_model;
  create policy anon_read on derived_forecast_skill_model
    for select to anon using (true);
  grant select on derived_forecast_skill_model to anon, authenticated;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 4. The readable surface: latest skill per city per model per lead, next to
--    the pooled number the desk actually prices with.
--
--    This is the view to open when asking "is the blend hiding a bad model".
-- --------------------------------------------------------------------------
create or replace view v_forecast_model_skill as
with per_model as (
  select distinct on (city_key, model, lead_days)
         city_key, model, lead_days, n_days, mae_c, bias_c,
         mae_bands, pct_within_one_band, computed_at
    from derived_forecast_skill_model
   where evidence_scope = 'verified_outcomes_v1'
   order by city_key, model, lead_days, computed_at desc
),
pooled as (
  select distinct on (city_key, lead_days)
         city_key, lead_days, mae_c as pooled_mae_c, n_days as pooled_n_days
    from derived_forecast_skill
   where evidence_scope = 'verified_outcomes_v1'
   order by city_key, lead_days, computed_at desc
)
select m.city_key,
       m.model,
       m.lead_days,
       m.n_days,
       m.mae_c,
       m.bias_c,
       m.mae_bands,
       m.pct_within_one_band,
       p.pooled_mae_c,
       p.pooled_n_days,
       round(m.mae_c - p.pooled_mae_c, 3) as vs_pooled_c,
       m.computed_at
  from per_model m
  left join pooled p
    on p.city_key = m.city_key and p.lead_days = m.lead_days;

comment on view v_forecast_model_skill is
  'Measured skill of each forecast model per city and lead, beside the pooled number the desk prices with. vs_pooled_c is positive when the model is WORSE than the blend. A model far below the pooled figure is one the blend is being dragged down by.';

grant select on v_forecast_model_skill to anon, authenticated;


-- --------------------------------------------------------------------------
-- 5. Tell PostgREST the new relations exist, or the UI gets PGRST205.
-- --------------------------------------------------------------------------
notify pgrst, 'reload schema';


-- --------------------------------------------------------------------------
-- 6. Report, in the terms the desk cares about.
-- --------------------------------------------------------------------------
do $ad4$
declare
  v_models   int;
  v_collide  bigint;
  v_worst    numeric;
begin
  select count(distinct model) into v_models from weather_forecasts;

  select count(*), coalesce(max(spread), 0) into v_collide, v_worst
    from (select max(forecast_max_c) - min(forecast_max_c) as spread
            from weather_forecasts
           where for_date >= current_date - 2
           group by city_key, for_date, lead_days
          having count(*) > 1) z;

  raise notice 'ad4_49: % model(s) writing forecasts', v_models;
  raise notice 'ad4_49: % (city,date,lead) key(s) hold more than one run; worst spread % C',
               v_collide, round(v_worst, 2);
  raise notice 'ad4_49: the engine now takes shortest lead, then NEWEST run_at - ties are no longer arbitrary';

  if to_regclass('public.derived_forecast_skill_model') is not null then
    raise notice 'ad4_49: derived_forecast_skill_model ready - run Actions -> Measure Skill to fill it';
  end if;
end
$ad4$;
