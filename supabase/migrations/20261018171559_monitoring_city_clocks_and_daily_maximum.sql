-- City-local monitoring and full-day maxima. Frozen predictions, model
-- recipes, capital settings, source truth and existing grants are preserved.
set local search_path=public,extensions,pg_temp;
do $migration$ begin
  if to_regclass('public.weather_forecasts') is not null and to_regclass('public.cities') is not null then
create or replace view public.v_city_forecast_today
with (security_invoker=true) as
select c.city_key, d.for_date, f.forecast_max_c, f.model, f.run_at, f.lead_days
from public.cities c
cross join lateral (
  select (now() at time zone coalesce(c.timezone, 'UTC'))::date as for_date
) d
left join lateral (
  select forecast_max_c, model, run_at, lead_days
  from public.weather_forecasts f
  where f.city_key=c.city_key and f.for_date=d.for_date
    and f.forecast_max_c is not null
  order by f.lead_days asc nulls last, f.run_at desc, f.forecast_id desc
  limit 1
) f on true;
revoke all on public.v_city_forecast_today from public;
grant select on public.v_city_forecast_today to anon,authenticated,service_role;

  end if;
  if to_regclass('public.v_city_stats') is not null then
create or replace view public.v_city_stats as
with fc as (
  select * from public.v_city_forecast_today
),
-- The highest temperature actually observed here in three local days. Not
-- used to price anything - used to CONTRADICT a forecast when the two are far
-- apart, which is the check a trader does by eye and the platform never did.
recent_obs as (
  select o.city_key, max(o.temp_c) as observed_max_3d_c
  from weather_observations o
  where o.valid_at > now() - interval '3 days' and o.temp_c is not null
  group by o.city_key
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
  case when cl.local_today = (now() at time zone coalesce(c.timezone, 'UTC'))::date
    then (coalesce(fc.forecast_max_c, lw.running_max_c) - cl.normal_max_c)::numeric(6,2) end as anomaly_c,
  case
    when cl.local_today is distinct from (now() at time zone coalesce(c.timezone, 'UTC'))::date then null
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
  lw.peak_window_state, lw.day_decided, lw.observed_at,

  -- ---- forecast provenance ----------------------------------------------
  -- Same columns, same order, as the ad4_17 definition of this view, so the
  -- UI reads one shape whichever file was run last.
  fc.lead_days                                             as forecast_lead_days,
  fc.run_at                                                as forecast_at,
  ro.observed_max_3d_c,
  (fc.forecast_max_c is not null
     and ro.observed_max_3d_c is not null
     and fc.forecast_max_c - ro.observed_max_3d_c > 4)     as forecast_suspect
from cities c
left join derived_city_climate cl on cl.city_key = c.city_key
left join live_weather lw   on lw.city_key = c.city_key
left join fc                on fc.city_key = c.city_key
left join recent_obs ro     on ro.city_key = c.city_key
left join skill sk          on sk.city_key = c.city_key
left join cap cp            on cp.city_key = c.city_key
left join div dv            on dv.city_key = c.city_key
left join edges e           on e.city_key = c.city_key
left join v_city_volume v   on v.city_key = c.city_key
left join derived_weather_peak pk
       on pk.city_key = c.city_key and pk.month = extract(month from now() at time zone coalesce(c.timezone, 'UTC'))::int;
  end if;
  if to_regclass('public.v_city_peak_approach') is not null then
create or replace view public.v_city_peak_approach as
select
  t.city_key,
  t.latest_temp_c,
  t.latest_at,
  t.latest_local_hour,
  t.running_max_c,
  t.slope_3_c_per_h,
  t.slope_6_c_per_h,
  t.direction,
  t.rolling_over,
  t.below_running_max_c,
  t.reading_age_min,
  t.n_readings,
  p.typical_climb_left_c,
  p.climb_left_p10_c,
  p.climb_left_p90_c,
  p.pct_already_peaked,
  p.n_days                                               as profile_days,
  case when p.typical_climb_left_c is null then null
       else greatest(t.running_max_c, round((t.latest_temp_c + p.typical_climb_left_c)::numeric, 2))
  end                                                    as implied_max_c,
  case when p.climb_left_p10_c is null then null
       else greatest(t.running_max_c, round((t.latest_temp_c + p.climb_left_p10_c)::numeric, 2))
  end                                                    as implied_max_low_c,
  case when p.climb_left_p90_c is null then null
       else greatest(t.running_max_c, round((t.latest_temp_c + p.climb_left_p90_c)::numeric, 2))
  end                                                    as implied_max_high_c
from v_city_temp_trend t
left join v_city_climb_profile p
       on p.city_key = t.city_key
      and p.local_hour = floor(t.latest_local_hour)::int;
  end if;
end $migration$;

-- Change only explanations of the cooling flag, not strategy eligibility.
do $migration$
declare name text; old_def text; new_def text;
begin
  foreach name in array array['v_trade_timing','v_trade_plan'] loop
    continue when to_regclass('public.'||name) is null;
    old_def:=pg_get_viewdef(('public.'||name)::regclass,true);
    new_def:=replace(old_def,'the maximum is banked - this is S5 territory, not S7',
      'recent readings are below the observed high; a later rise remains possible');
    new_def:=replace(new_def,'The maximum is banked and this band holds it - s5 territory',
      'Cooling trend: this band contains the observed high so far; settlement remains unconfirmed');
    new_def:=replace(new_def,'Day decided - this band is settled, not traded',
      'Cooling trend: the observed high is in another band; settlement remains unconfirmed');
    if new_def<>old_def then execute format('create or replace view public.%I as %s',name,new_def); end if;
  end loop;
end $migration$;
