-- ===========================================================================
-- ad4_19_stats_cache.sql - make v_city_stats survive a real archive.
--
-- v_city_stats timed out in production. It was written against a database
-- with almost no history and it does not scale, in two ways that both get
-- worse every single day the desk runs:
--
--  1. THE DIVERGENCE JOIN WAS UNBOUNDED. v_forecast_divergence does a
--     `distinct on` across EVERY row of weather_forecasts - 47,000 of them and
--     climbing - computing the model spread for every date the desk has ever
--     forecast, and then the join in v_city_stats throws all of it away except
--     today. Measured: 88ms unbounded against 8ms for dates from today
--     onwards, on 47k rows. That ratio only widens.
--
--  2. THE CLIMATOLOGY WAS RECOMPUTED PER REQUEST. The seasonal normal is a
--     full pass over weather_observations - the largest table here - and it
--     was running on every page load, for a figure that changes once a day.
--
-- Neither is a tuning problem; both are the wrong shape. This file bounds the
-- first and caches the second. The page then reads one small table and a
-- handful of current rows.
--
-- Run order: after sql/ad4_18_databank.sql. Re-runnable.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- 1. Indexes the aggregations actually need.
--    Guarded individually: a missing table must not take the file down.
-- --------------------------------------------------------------------------
do $ad4$
begin
  if to_regclass('public.weather_observations') is not null then
    create index if not exists wx_obs_city_valid on weather_observations (city_key, valid_at desc);
  end if;
  if to_regclass('public.weather_forecasts') is not null then
    create index if not exists wx_fc_date_city on weather_forecasts (for_date, city_key);
  end if;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 1b. Prerequisites, named rather than hit.
--
--     This file rebuilds v_city_stats on top of views ad4_16 and ad4_17
--     create. Run out of order it used to fail with a bare `relation
--     "v_city_climate" does not exist`, which says nothing about which file to
--     run. Say it plainly and stop, instead of leaving a half-applied file.
-- --------------------------------------------------------------------------
do $ad4$
declare missing text[] := array[]::text[];
begin
  if to_regclass('public.v_forecast_divergence') is null then
    missing := array_append(missing, 'v_forecast_divergence -> run sql/ad4_16_nws.sql');
  end if;
  if to_regclass('public.v_city_climate') is null then
    missing := array_append(missing, 'v_city_climate -> run sql/ad4_17_city_stats.sql');
  end if;
  if array_length(missing, 1) > 0 then
    raise exception E'ad4_19 needs these first:\n  %',
      array_to_string(missing, E'\n  ');
  end if;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 2. Forecast divergence, bounded to the days anything actually prices.
--
--    The full view stays as it is - the data bank and any backward-looking
--    analysis still want history. This one exists to be JOINED, and a join
--    against every date the desk has ever seen was the whole problem.
-- --------------------------------------------------------------------------
create or replace view v_forecast_divergence_current as
select * from v_forecast_divergence where for_date >= current_date - 1;


-- --------------------------------------------------------------------------
-- 3. Cached climatology.
--
--    One row per city, holding what the seasonal baseline was when it was
--    last computed. Refreshed daily; a normal for today's date does not move
--    faster than that, and computing it per page load was paying a full table
--    scan for a number that changes at midnight.
-- --------------------------------------------------------------------------
create table if not exists derived_city_climate (
  city_key      text primary key,
  local_today   date,
  baseline      text,
  baseline_days int,
  normal_max_c  numeric,
  sd_max_c      numeric,
  computed_at   timestamptz not null default now()
);

comment on table derived_city_climate is
  'Cache of v_city_climate. Refreshed by refresh_city_climate(); read by v_city_stats so a page load never pays for a pass over weather_observations.';

create or replace function refresh_city_climate() returns jsonb
language plpgsql security definer as $ad4$
declare
  v_rows int;
  v_started timestamptz := clock_timestamp();
begin
  insert into derived_city_climate (city_key, local_today, baseline, baseline_days,
                                     normal_max_c, sd_max_c, computed_at)
  select city_key, local_today, baseline, baseline_days, normal_max_c, sd_max_c, now()
  from v_city_climate
  on conflict (city_key) do update set
    local_today   = excluded.local_today,
    baseline      = excluded.baseline,
    baseline_days = excluded.baseline_days,
    normal_max_c  = excluded.normal_max_c,
    sd_max_c      = excluded.sd_max_c,
    computed_at   = excluded.computed_at;
  get diagnostics v_rows = row_count;

  return jsonb_build_object(
    'ok', true, 'cities', v_rows,
    'ms', round(extract(epoch from clock_timestamp() - v_started) * 1000),
    'with_baseline', (select count(*) from derived_city_climate where normal_max_c is not null));
end;
$ad4$;


-- --------------------------------------------------------------------------
-- 4. v_city_stats, rebuilt on the cache and the bounded divergence.
--
--    Same columns, same meanings - only the cost changes. `baseline` reports
--    'stale' when the cache was computed for an earlier local date, so a page
--    can say the normal is a day old rather than quietly showing yesterday's.
-- --------------------------------------------------------------------------
drop view if exists v_city_stats;
create view v_city_stats as
with fc as (
  select distinct on (f.city_key)
    f.city_key, f.forecast_max_c, f.model, f.run_at
  from weather_forecasts f
  where f.forecast_max_c is not null
    and f.for_date >= current_date - 1
  order by f.city_key, f.run_at desc
),
skill as (
  select distinct on (city_key) city_key, mae_c, bias_c, n_days
  from derived_forecast_skill where lead_days = 1
  order by city_key, computed_at desc
),
cap as (
  select distinct on (city_key) city_key, usd_at_5c, usd_full, live_bands
  from derived_capacity order by city_key, computed_at desc
),
div as (
  select distinct on (city_key) city_key, spread_c, n_models, sigma_multiplier
  from v_forecast_divergence_current
  order by city_key, for_date
),
edges as (
  select city_key, count(*)::int as n_tradeable,
         max(edge_net_pp) as best_edge_pp,
         avg(edge_net_pp)::numeric(8,5) as avg_edge_pp
  from v_opportunities where tradeable group by city_key
)
select
  c.city_key, c.display_name, c.icao, c.timezone, c.unit, c.latitude, c.longitude,

  case
    when cl.city_key is null then null
    when cl.local_today is distinct from (now() at time zone coalesce(c.timezone, 'UTC'))::date
      then 'stale'
    else cl.baseline
  end                                                      as baseline,
  cl.baseline_days,
  cl.normal_max_c,
  cl.sd_max_c                                              as volatility_c,
  lw.temp_c                                                as now_c,
  lw.running_max_c,
  fc.forecast_max_c,
  fc.model                                                 as forecast_model,
  (coalesce(fc.forecast_max_c, lw.running_max_c) - cl.normal_max_c)::numeric(6,2) as anomaly_c,
  case
    when cl.sd_max_c is null or cl.sd_max_c <= 0 then null
    when coalesce(fc.forecast_max_c, lw.running_max_c) is null then null
    else round((coalesce(fc.forecast_max_c, lw.running_max_c) - cl.normal_max_c) / cl.sd_max_c, 2)
  end                                                      as hotness_sigma,

  sk.mae_c, sk.bias_c, sk.n_days as skill_days,
  dv.spread_c as model_spread_c, dv.n_models, dv.sigma_multiplier,

  coalesce(v.volume_usd, 0) as volume_24h,
  coalesce(v.n_trades, 0)   as n_trades_24h,
  cp.usd_at_5c              as depth_5c,
  cp.live_bands,
  coalesce(e.n_tradeable, 0) as n_tradeable,
  e.best_edge_pp, e.avg_edge_pp,

  pk.peak_hour_local, pk.window_width_h,
  lw.peak_window_state, lw.day_decided, lw.observed_at
from cities c
left join derived_city_climate cl on cl.city_key = c.city_key
left join live_weather lw   on lw.city_key = c.city_key
left join fc                on fc.city_key = c.city_key
left join skill sk          on sk.city_key = c.city_key
left join cap cp            on cp.city_key = c.city_key
left join div dv            on dv.city_key = c.city_key
left join edges e           on e.city_key = c.city_key
left join v_city_volume v   on v.city_key = c.city_key
left join derived_weather_peak pk
       on pk.city_key = c.city_key and pk.month = extract(month from current_date)::int;


-- --------------------------------------------------------------------------
-- 5. Grants, and fill the cache once so the page works immediately.
-- --------------------------------------------------------------------------
do $ad4$
declare o text; r text;
begin
  foreach o in array array['v_city_stats', 'v_forecast_divergence_current', 'derived_city_climate'] loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if exists (select 1 from pg_roles where rolname = r) then
        execute format('grant select on %I to %I', o, r);
      end if;
    end loop;
  end loop;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant all on derived_city_climate to service_role;
    grant execute on function refresh_city_climate() to service_role;
  end if;
  -- The browser must not be able to run a full-table-scan function on demand.
  revoke all on function refresh_city_climate() from public;
  foreach r in array array['anon', 'authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('revoke execute on function refresh_city_climate() from %I', r);
    end if;
  end loop;
end
$ad4$;

select refresh_city_climate();


-- --------------------------------------------------------------------------
-- 6. Report.
-- --------------------------------------------------------------------------
do $ad4$
declare v_n int; v_b int;
begin
  select count(*), count(*) filter (where normal_max_c is not null)
    into v_n, v_b from derived_city_climate;
  raise notice 'ad4_19: climate cached for % cities, % with a usable baseline', v_n, v_b;
  raise notice 'ad4_19: refresh it daily - select refresh_city_climate(); Derived Recompute now does.';
end
$ad4$;
