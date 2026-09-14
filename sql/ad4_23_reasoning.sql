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
-- DEPENDENCIES ARE OPTIONAL, ON PURPOSE. This file draws on views created by
-- ad4_19, ad4_21 and ad4_22, and the first version hard-referenced them - so
-- running it on a database that had not yet run ad4_21 failed outright with
-- `relation "v_city_day_features" does not exist`. Every other file in this
-- repo guards its dependencies (ad4_13 builds its DDL from
-- information_schema for exactly this reason) and this one did not.
--
-- The view is therefore ASSEMBLED: each block is included only if its source
-- exists, and the columns it would have supplied come back NULL instead. The
-- panel that reads it already renders a missing input as "this step needs job
-- X", so a partial chain degrades into a chain with visible gaps rather than
-- into an error.
--
-- Run order: any time after sql/ad4_13_reconcile.sql. Runs further up the
-- chain fill in more of it. Re-runnable.
-- ===========================================================================

do $ad4$
declare
  -- THE CACHE FIRST. v_city_day_features carries a window function
  -- (prev_max_c needs lag() across a city's days), and a window cannot be
  -- pushed past a WHERE - so `where obs_date >= current_date - 1` still made
  -- Postgres compute EVERY day in the archive before discarding all but two.
  -- On 650k observations that is 2.8 seconds for 37 rows, which is a
  -- statement timeout on the anon role and a red box on the page.
  --
  -- derived_city_day_features is the same rows, already computed, indexed,
  -- and carrying every column this view reads. It exists for exactly this.
  -- The view stays as the fallback for a database whose cache has never been
  -- filled, and the notice below says which one was used.
  has_cache boolean := to_regclass('public.derived_city_day_features') is not null;
  feat_src  text;
  has_feat  boolean := to_regclass('public.v_city_day_features')      is not null;
  has_wmod  boolean := to_regclass('public.derived_weather_model')    is not null;
  has_pers  boolean := to_regclass('public.v_persistence_skill')      is not null;
  has_divc  boolean := to_regclass('public.v_forecast_divergence_current') is not null;
  has_div   boolean := to_regclass('public.v_forecast_divergence')    is not null;
  has_ctx   boolean := to_regclass('public.v_opportunity_context')    is not null;
  has_opp   boolean := to_regclass('public.v_opportunities')          is not null;
  has_skill boolean := to_regclass('public.derived_forecast_skill')   is not null;
  has_lw    boolean := to_regclass('public.live_weather')             is not null;
  sql text;
  missing text[] := array[]::text[];
begin
  if to_regclass('public.markets') is null or to_regclass('public.cities') is null then
    raise notice 'ad4_23: markets/cities missing - run sql/ad4_00_preflight.sql first';
    return;
  end if;

  sql := $q$
create or replace view v_city_reasoning as
with day as (
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
)
$q$;

  if has_skill then
    sql := sql || $q$, skill as (
  select distinct on (city_key, lead_days) city_key, lead_days, mae_c, bias_c, n_days
  from derived_forecast_skill order by city_key, lead_days, computed_at desc
)$q$;
  else
    missing := array_append(missing, 'derived_forecast_skill (Actions -> Skill)');
    sql := sql || $q$, skill as (
  select null::text as city_key, null::int as lead_days,
         null::numeric as mae_c, null::numeric as bias_c, null::int as n_days
  where false
)$q$;
  end if;

  feat_src := case when has_cache then 'derived_city_day_features'
                   when has_feat  then 'v_city_day_features'
                   else null end;
  if feat_src is not null then
    sql := sql || format($q$, feat as (
  select distinct on (city_key)
    city_key, obs_date, morning_temp_c, dewpoint_depression_c, cloud_mean,
    wind_mean, precip_total, prev_max_c
  from %s
  where obs_date >= current_date - 1
  order by city_key, obs_date desc
)$q$, feat_src);
  else
    missing := array_append(missing, 'derived_city_day_features (sql/ad4_28_feature_cache.sql, then Actions -> Derived Recompute)');
    sql := sql || $q$, feat as (
  select null::text as city_key, null::date as obs_date,
         null::numeric as morning_temp_c, null::numeric as dewpoint_depression_c,
         null::numeric as cloud_mean, null::numeric as wind_mean,
         null::numeric as precip_total, null::numeric as prev_max_c
  where false
)$q$;
  end if;

  if has_wmod then
    sql := sql || $q$, wmodel as (
  select city_key, coefficients, mae_c as model_mae_c, persistence_mae_c,
         beats_persistence, notes
  from derived_weather_model where target = 'max_c'
)$q$;
  else
    missing := array_append(missing, 'derived_weather_model (Actions -> Weather Model)');
    sql := sql || $q$, wmodel as (
  select null::text as city_key, null::jsonb as coefficients,
         null::numeric as model_mae_c, null::numeric as persistence_mae_c,
         null::boolean as beats_persistence, null::text as notes
  where false
)$q$;
  end if;

  if has_pers then
    sql := sql || $q$, pers as (
  select city_key, persistence_mae_c as city_persistence_mae_c, n_days as persistence_days
  from v_persistence_skill
)$q$;
  else
    sql := sql || $q$, pers as (
  select null::text as city_key, null::numeric as city_persistence_mae_c,
         null::int as persistence_days
  where false
)$q$;
  end if;

  -- The bounded divergence view where ad4_19 has run, the full one otherwise.
  -- Filtering the full view here keeps the cost sane on a large archive.
  if has_divc then
    sql := sql || $q$, div as (
  select distinct on (city_key) city_key, spread_c, n_models, models, sigma_multiplier
  from v_forecast_divergence_current order by city_key, for_date
)$q$;
  elsif has_div then
    sql := sql || $q$, div as (
  select distinct on (city_key) city_key, spread_c, n_models, models, sigma_multiplier
  from v_forecast_divergence where for_date >= current_date - 1
  order by city_key, for_date
)$q$;
  else
    sql := sql || $q$, div as (
  select null::text as city_key, null::numeric as spread_c, null::int as n_models,
         null::text as models, null::numeric as sigma_multiplier
  where false
)$q$;
  end if;

  if has_opp then
    sql := sql || $q$, top_band as (
  -- "MOST LIKELY" IS THE MODAL *CLOSED* BUCKET.
  --
  -- The ladder's end buckets are open-ended: "27C or higher" runs to plus
  -- infinity. Taking max(model_prob) over every bucket compares one of
  -- infinite width against buckets 1C wide, so the widest always wins and it
  -- is not the likeliest. London on 14 Sep, forecast 24.2C, read
  --
  --     Most likely bucket  27C or higher  at 32%      market 1c
  --
  -- while every closed bucket sat near 8% and the market held 46c and 45c on
  -- 24 and 25. The open mass is real - it is just not a mode - so it is
  -- carried out separately as tail_low_pct / tail_high_pct rather than
  -- dropped. Same fix ad4_58 already applies to v_city_prediction_confidence;
  -- this view never got it, and this view is what the board's Why panel reads.
  --
  -- Ties broke arbitrarily too, so the winner could change between reads:
  -- ordered by band_lo after probability.
  select distinct on (o.city_key)
    o.city_key, o.band_id, o.band_label, o.band_lo, o.band_hi,
    o.open_low, o.open_high, o.model_prob, o.market_price, o.edge_net_pp,
    o.confidence, o.regime_label, o.tradeable, o.block_reason
  from v_opportunities o
  join day d on d.city_key = o.city_key and d.resolution_date = o.resolution_date
  where o.side = 'YES' and o.model_prob is not null
    and not coalesce(o.open_low, false) and not coalesce(o.open_high, false)
    and o.band_lo is not null and o.band_hi is not null
  order by o.city_key, o.model_prob desc, o.band_lo
), top_tails as (
  -- What the mode deliberately leaves out: "32% chance the day lands above
  -- the entire board" is a statement worth printing, it is just not a bucket
  -- anyone can be most likely to be in.
  select o.city_key,
         round(100 * sum(o.model_prob) filter (where coalesce(o.open_low, false)), 1)  as tail_low_pct,
         round(100 * sum(o.model_prob) filter (where coalesce(o.open_high, false)), 1) as tail_high_pct
  from v_opportunities o
  join day d on d.city_key = o.city_key and d.resolution_date = o.resolution_date
  where o.side = 'YES' and o.model_prob is not null
  group by o.city_key
)$q$;
  else
    missing := array_append(missing, 'v_opportunities (Actions -> Probabilities)');
    sql := sql || $q$, top_band as (
  select null::text as city_key, null::uuid as band_id, null::text as band_label,
         null::numeric as band_lo, null::numeric as band_hi,
         null::boolean as open_low, null::boolean as open_high,
         null::numeric as model_prob, null::numeric as market_price,
         null::numeric as edge_net_pp, null::numeric as confidence,
         null::text as regime_label, null::boolean as tradeable,
         null::text as block_reason
  where false
), top_tails as (
  select null::text as city_key, null::numeric as tail_low_pct,
         null::numeric as tail_high_pct
  where false
)$q$;
  end if;

  if has_ctx then
    sql := sql || $q$, ctx as (
  select band_id, forecast_ahead_of_book, forecast_move_c, drift_24h, hours_to_resolution
  from v_opportunity_context
)$q$;
  else
    missing := array_append(missing, 'v_opportunity_context (sql/ad4_22_opportunity_context.sql)');
    sql := sql || $q$, ctx as (
  select null::uuid as band_id, null::boolean as forecast_ahead_of_book,
         null::numeric as forecast_move_c, null::numeric as drift_24h,
         null::numeric as hours_to_resolution
  where false
)$q$;
  end if;

  if has_lw then
    sql := sql || $q$, lw as (
  select city_key, temp_c, running_max_c, peak_window_state, day_decided, observed_at
  from live_weather
)$q$;
  else
    sql := sql || $q$, lw as (
  select null::text as city_key, null::numeric as temp_c, null::numeric as running_max_c,
         null::text as peak_window_state, null::boolean as day_decided,
         null::timestamptz as observed_at
  where false
)$q$;
  end if;

  sql := sql || $q$
select
  c.city_key, c.display_name, c.unit, c.timezone, d.resolution_date,
  fc.forecast_max_c, fc.model as forecast_model, fc.run_at as forecast_at, fc.lead_days,
  feat.prev_max_c, p.city_persistence_mae_c, p.persistence_days,
  feat.obs_date as feature_date, feat.morning_temp_c, feat.dewpoint_depression_c,
  feat.cloud_mean, feat.wind_mean, feat.precip_total,
  wm.coefficients as weather_coefficients, wm.beats_persistence as weather_model_useful,
  wm.model_mae_c as weather_model_mae_c, wm.notes as weather_model_notes,
  lw.temp_c as now_c, lw.running_max_c, lw.peak_window_state, lw.day_decided, lw.observed_at,
  sk.mae_c as forecast_mae_c, sk.bias_c as forecast_bias_c, sk.n_days as skill_days,
  dv.spread_c as model_spread_c, dv.n_models, dv.models, dv.sigma_multiplier,
  tb.band_id as top_band_id, tb.band_label as top_band_label,
  tb.band_lo as top_band_lo, tb.band_hi as top_band_hi,
  tb.open_low as top_open_low, tb.open_high as top_open_high,
  tb.model_prob as top_model_prob, tb.market_price as top_market_price,
  tb.edge_net_pp as top_edge_net_pp, tb.confidence, tb.regime_label,
  tb.tradeable as top_tradeable, tb.block_reason as top_block_reason,
  tt.tail_low_pct, tt.tail_high_pct,
  oc.forecast_ahead_of_book, oc.forecast_move_c, oc.drift_24h, oc.hours_to_resolution
from cities c
join day d              on d.city_key = c.city_key
left join fc            on fc.city_key = c.city_key and fc.for_date = d.resolution_date
left join feat          on feat.city_key = c.city_key
left join wmodel wm     on wm.city_key = c.city_key
left join pers p        on p.city_key = c.city_key
left join lw            on lw.city_key = c.city_key
left join skill sk      on sk.city_key = c.city_key and sk.lead_days = coalesce(fc.lead_days, 1)
left join div dv        on dv.city_key = c.city_key
left join top_band tb   on tb.city_key = c.city_key
left join top_tails tt  on tt.city_key = c.city_key
left join ctx oc        on oc.band_id = tb.band_id
$q$;

  execute sql;

  if array_length(missing, 1) > 0 then
    raise notice 'ad4_23: view created with % step(s) unavailable:', array_length(missing, 1);
    for i in 1 .. array_length(missing, 1) loop
      raise notice 'ad4_23:   - %', missing[i];
    end loop;
    raise notice 'ad4_23: those steps show as gaps in the UI naming the job that fills them. Re-run this file after any of them lands.';
  else
    raise notice 'ad4_23: every input available - the full reasoning chain is live';
  end if;
end
$ad4$;


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
