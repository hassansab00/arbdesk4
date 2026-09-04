-- ===========================================================================
-- ad4_23_reasoning.sql - the desk's argument, per city, in one row.
--
-- AD4 computes a probability for every band and shows the ANSWER. It has never
-- shown the REASONING, and without that the board is a table of numbers you
-- either trust or do not. A trader working a city by hand goes through a chain:
--
--   what does the forecast say the max will be
--   what did yesterday do, and is today's air mass the same
--   what do this morning's conditions imply - dry and clear, or damp and grey
--   how far has the day already climbed, and is the peak still ahead
--   how wrong is the forecast usually HERE, at this lead
--   do the models even agree
--   therefore: which bucket, and how sure
--
-- Every one of those is already in this database, scattered across six views
-- and three tables. This assembles them into one row per city so the desk can
-- show its working - city by city, which is how the trade is actually made.
--
-- It computes nothing new. It is a JOIN, deliberately: any number here must be
-- the same number the pricing engine used, or the reasoning would be a story
-- told alongside the trade rather than the reason for it.
--
-- Run order: after sql/ad4_22_opportunity_context.sql. Re-runnable.
-- ===========================================================================

create or replace view v_city_reasoning as
with day as (
  -- The nearest settlement day per city: the one being traded now.
  select distinct on (m.city_key) m.city_key, m.resolution_date, m.market_id
  from markets m
  where m.resolution_date >= current_date
  order by m.city_key, m.resolution_date
),
fc as (
  select distinct on (f.city_key, f.for_date)
    f.city_key, f.for_date, f.forecast_max_c, f.model, f.run_at, f.lead_days
  from weather_forecasts f
  where f.for_date >= current_date and f.forecast_max_c is not null
  order by f.city_key, f.for_date, f.run_at desc
),
skill as (
  select distinct on (city_key, lead_days) city_key, lead_days, mae_c, bias_c, n_days
  from derived_forecast_skill order by city_key, lead_days, computed_at desc
),
-- Today's morning conditions and what this city's own history says they mean.
feat as (
  select distinct on (city_key)
    city_key, obs_date, morning_temp_c, dewpoint_depression_c, cloud_mean,
    wind_mean, precip_total, prev_max_c, max_c as max_so_far_c
  from v_city_day_features
  where obs_date >= current_date - 1
  order by city_key, obs_date desc
),
wmodel as (
  select city_key, coefficients, mae_c as model_mae_c, persistence_mae_c,
         beats_persistence, notes
  from derived_weather_model where target = 'max_c'
),
pers as (
  select city_key, persistence_mae_c as city_persistence_mae_c, n_days as persistence_days
  from v_persistence_skill
),
div as (
  select distinct on (city_key) city_key, spread_c, n_models, models, sigma_multiplier
  from v_forecast_divergence_current order by city_key, for_date
),
-- The band the model currently thinks most likely, and what it costs.
top_band as (
  select distinct on (o.city_key)
    o.city_key, o.band_id, o.band_label, o.band_lo, o.band_hi,
    o.open_low, o.open_high, o.model_prob, o.market_price, o.edge_net_pp,
    o.confidence, o.regime_label, o.tradeable, o.block_reason
  from v_opportunities o
  join day d on d.city_key = o.city_key and d.resolution_date = o.resolution_date
  where o.side = 'YES' and o.model_prob is not null
  order by o.city_key, o.model_prob desc
)
select
  c.city_key,
  c.display_name,
  c.unit,
  c.timezone,
  d.resolution_date,

  -- 1. what the forecast says
  fc.forecast_max_c,
  fc.model                                as forecast_model,
  fc.run_at                               as forecast_at,
  fc.lead_days,

  -- 2. what yesterday did, and the benchmark it sets
  feat.prev_max_c,
  p.city_persistence_mae_c,
  p.persistence_days,

  -- 3. what this morning implies
  feat.obs_date                           as feature_date,
  feat.morning_temp_c,
  feat.dewpoint_depression_c,
  feat.cloud_mean,
  feat.wind_mean,
  feat.precip_total,
  wm.coefficients                         as weather_coefficients,
  wm.beats_persistence                    as weather_model_useful,
  wm.model_mae_c                          as weather_model_mae_c,
  wm.notes                                as weather_model_notes,

  -- 4. how far the day has already got
  lw.temp_c                               as now_c,
  lw.running_max_c,
  lw.peak_window_state,
  lw.day_decided,
  lw.observed_at,

  -- 5. how wrong the forecast usually is here
  sk.mae_c                                as forecast_mae_c,
  sk.bias_c                               as forecast_bias_c,
  sk.n_days                               as skill_days,

  -- 6. do the models agree
  dv.spread_c                             as model_spread_c,
  dv.n_models,
  dv.models,
  dv.sigma_multiplier,

  -- 7. therefore
  tb.band_id                              as top_band_id,
  tb.band_label                           as top_band_label,
  tb.band_lo                              as top_band_lo,
  tb.band_hi                              as top_band_hi,
  tb.open_low                             as top_open_low,
  tb.open_high                            as top_open_high,
  tb.model_prob                           as top_model_prob,
  tb.market_price                         as top_market_price,
  tb.edge_net_pp                          as top_edge_net_pp,
  tb.confidence,
  tb.regime_label,
  tb.tradeable                            as top_tradeable,
  tb.block_reason                         as top_block_reason,

  -- and whether the market has seen the current forecast
  oc.forecast_ahead_of_book,
  oc.forecast_move_c,
  oc.drift_24h,
  oc.hours_to_resolution
from cities c
join day d              on d.city_key = c.city_key
left join fc            on fc.city_key = c.city_key and fc.for_date = d.resolution_date
left join feat          on feat.city_key = c.city_key
left join wmodel wm     on wm.city_key = c.city_key
left join pers p        on p.city_key = c.city_key
left join live_weather lw on lw.city_key = c.city_key
left join skill sk      on sk.city_key = c.city_key and sk.lead_days = coalesce(fc.lead_days, 1)
left join div dv        on dv.city_key = c.city_key
left join top_band tb   on tb.city_key = c.city_key
left join v_opportunity_context oc on oc.band_id = tb.band_id;


do $ad4$
declare r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_city_reasoning to %I', r);
    end if;
  end loop;
end
$ad4$;

do $ad4$
declare v_n int; v_fc int; v_top int;
begin
  select count(*), count(forecast_max_c), count(top_band_id)
    into v_n, v_fc, v_top from v_city_reasoning;
  raise notice 'ad4_23: % city/cities trading now; % with a forecast; % with a priced band',
               v_n, v_fc, v_top;
end
$ad4$;
