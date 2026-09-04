-- ===========================================================================
-- ad4_17_city_stats.sql - what a city is actually like, as numbers.
--
-- The City Clusters page could say how much a city TRADES and nothing about
-- the weather that decides its markets. "Hot" and "volatile" had nowhere to
-- come from: the schema has observations and forecasts but no climatology, so
-- there was no normal to be hot relative to.
--
-- Computing it in the browser is not an option - it is a full pass over
-- weather_observations, which is the largest table here. So it is a view, and
-- the page fetches one row per city.
--
-- Two decisions worth stating, because both change what the numbers mean:
--
-- 1. The normal is CLIMATOLOGICAL, not a trailing average. A trailing 30-day
--    mean in October is dominated by September; a day 3C above it may be
--    perfectly ordinary for the date. So the baseline is every year's
--    observations within +/-10 days of today's day-of-year. Only if that is
--    too thin does it fall back to the trailing window, and the view says
--    which it used.
--
-- 2. Hotness is reported in STANDARD DEVIATIONS as well as degrees. +3C in a
--    maritime city where the daily max barely moves is a far more extreme day
--    than +3C somewhere continental, and a page comparing cities side by side
--    has to say so. The z-score is the comparable number; the degrees are the
--    readable one.
--
-- Run order: after sql/ad4_16_nws.sql. Re-runnable.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- 1. Daily maxima, once, so everything below reads the same series.
--
--    valid_at is a timestamptz; the "day" a maximum belongs to is the day in
--    the CITY's timezone, not UTC. In Chicago the two differ for six hours of
--    every day, which is exactly the afternoon a daily maximum happens in.
-- --------------------------------------------------------------------------
create or replace view v_city_daily_max as
select
  o.city_key,
  (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date as obs_date,
  max(o.temp_c)                                               as max_c,
  min(o.temp_c)                                               as min_c,
  count(*)::int                                               as n_obs
from weather_observations o
left join cities c on c.city_key = o.city_key
where o.temp_c is not null
group by 1, 2;


-- --------------------------------------------------------------------------
-- 2. The climatological normal for TODAY's date, per city.
-- --------------------------------------------------------------------------
create or replace view v_city_climate as
with today as (
  select c.city_key,
         (now() at time zone coalesce(c.timezone, 'UTC'))::date as local_today
  from cities c
),
seasonal as (
  -- every year's observations near today's day-of-year
  select d.city_key,
         avg(d.max_c)::numeric(6,2)          as normal_max_c,
         stddev_samp(d.max_c)::numeric(6,2)  as sd_max_c,
         count(*)::int                       as n_days
  from v_city_daily_max d
  join today t on t.city_key = d.city_key
  where least(
          abs(extract(doy from d.obs_date) - extract(doy from t.local_today)),
          365 - abs(extract(doy from d.obs_date) - extract(doy from t.local_today))
        ) <= 10
    and d.obs_date < t.local_today
  group by d.city_key
),
recent as (
  -- the fallback: recent weather, whatever time of year it is
  select d.city_key,
         avg(d.max_c)::numeric(6,2)          as normal_max_c,
         stddev_samp(d.max_c)::numeric(6,2)  as sd_max_c,
         count(*)::int                       as n_days
  from v_city_daily_max d
  join today t on t.city_key = d.city_key
  where d.obs_date >= t.local_today - 30 and d.obs_date < t.local_today
  group by d.city_key
)
select
  t.city_key,
  t.local_today,
  case when coalesce(s.n_days, 0) >= 15 then 'seasonal' 
       when coalesce(r.n_days, 0) >= 5  then 'trailing_30d'
       else 'none' end                                as baseline,
  case when coalesce(s.n_days, 0) >= 15 then s.normal_max_c else r.normal_max_c end as normal_max_c,
  case when coalesce(s.n_days, 0) >= 15 then s.sd_max_c     else r.sd_max_c     end as sd_max_c,
  case when coalesce(s.n_days, 0) >= 15 then s.n_days       else r.n_days       end as baseline_days
from today t
left join seasonal s on s.city_key = t.city_key
left join recent r on r.city_key = t.city_key;


-- --------------------------------------------------------------------------
-- 3. One row per city: weather, market and model, side by side.
-- --------------------------------------------------------------------------
-- Dropped and recreated, not `create or replace`. sql/ad4_19 rebuilds this
-- same view on the climate cache, which changes some column types; a later
-- re-run of THIS file then failed with `cannot change data type of view
-- column normal_max_c`, and the fix looked like a corrupt database. Nothing
-- in the schema selects from v_city_stats - only the app does - so dropping
-- it costs nothing and makes both files re-runnable in either order.
drop view if exists v_city_stats;
create view v_city_stats as
with fc as (
  -- The forecast for today that carries the MOST INFORMATION - which is the
  -- shortest lead, not merely the newest run_at.
  --
  -- This was `order by run_at desc` and it produced a wrong number on a real
  -- desk. weather_forecasts is filled by the Open-Meteo previous-runs ingest,
  -- which archives leads 1 through 7 for every day; a day sitting seven days
  -- out has exactly one row, a lead-7 forecast issued a week ago, and
  -- `run_at desc` picked it happily. The board then showed Chicago at 98F on a
  -- day nothing had been near 84F, with nothing on screen to say the figure
  -- was a week-old seven-day-lead guess.
  --
  -- Lead ascending fixes the choice; lead_days and run_at come out of the view
  -- so the UI can show the provenance instead of a bare number.
  select distinct on (f.city_key)
    f.city_key, f.forecast_max_c, f.model, f.run_at, f.lead_days
  from weather_forecasts f
  join v_city_climate cl on cl.city_key = f.city_key and f.for_date = cl.local_today
  where f.forecast_max_c is not null
  order by f.city_key, f.lead_days asc nulls last, f.run_at desc
),
-- The highest temperature actually observed in this city over the last three
-- local days. Not used to price anything - used to CONTRADICT the forecast
-- when the two are far apart, which is the check a trader does by eye and the
-- platform never did.
recent_obs as (
  select o.city_key, max(o.temp_c) as observed_max_3d_c
  from weather_observations o
  join cities c2 on c2.city_key = o.city_key
  where o.valid_at > now() - interval '3 days' and o.temp_c is not null
  group by o.city_key
),
skill as (
  select distinct on (city_key) city_key, mae_c, bias_c, n_days
  from derived_forecast_skill
  where lead_days = 1
  order by city_key, computed_at desc
),
cap as (
  select distinct on (city_key) city_key, usd_at_5c, usd_full, live_bands
  from derived_capacity
  order by city_key, computed_at desc
),
div as (
  select d.city_key, d.spread_c, d.n_models, d.sigma_multiplier
  from v_forecast_divergence d
  join v_city_climate cl on cl.city_key = d.city_key and d.for_date = cl.local_today
),
edges as (
  select city_key,
         count(*)::int                          as n_tradeable,
         max(edge_net_pp)                       as best_edge_pp,
         avg(edge_net_pp)::numeric(8,5)         as avg_edge_pp
  from v_opportunities
  where tradeable
  group by city_key
)
select
  c.city_key,
  c.display_name,
  c.icao,
  c.timezone,
  c.unit,
  c.latitude,
  c.longitude,

  -- ---- weather: where today sits against this city's own normal ----------
  cl.baseline,
  cl.baseline_days,
  cl.normal_max_c,
  cl.sd_max_c                                              as volatility_c,
  lw.temp_c                                                as now_c,
  lw.running_max_c,
  fc.forecast_max_c,
  fc.model                                                 as forecast_model,
  -- degrees above or below normal: the readable number
  (coalesce(fc.forecast_max_c, lw.running_max_c) - cl.normal_max_c)::numeric(6,2)
                                                           as anomaly_c,
  -- the same thing in standard deviations: the COMPARABLE number. +3C is
  -- unremarkable in a continental city and extraordinary in a maritime one,
  -- and a page ranking cities against each other needs the second form.
  case
    when cl.sd_max_c is null or cl.sd_max_c <= 0 then null
    when coalesce(fc.forecast_max_c, lw.running_max_c) is null then null
    else round((coalesce(fc.forecast_max_c, lw.running_max_c) - cl.normal_max_c) / cl.sd_max_c, 2)
  end                                                      as hotness_sigma,

  -- ---- model: how well this city is actually known ----------------------
  sk.mae_c,
  sk.bias_c,
  sk.n_days                                                as skill_days,
  dv.spread_c                                              as model_spread_c,
  dv.n_models,
  dv.sigma_multiplier,

  -- ---- market: scale --------------------------------------------------
  coalesce(v.volume_usd, 0)                                as volume_24h,
  coalesce(v.n_trades, 0)                                  as n_trades_24h,
  cp.usd_at_5c                                             as depth_5c,
  cp.live_bands,
  coalesce(e.n_tradeable, 0)                               as n_tradeable,
  e.best_edge_pp,
  e.avg_edge_pp,

  -- ---- clock ------------------------------------------------------------
  pk.peak_hour_local,
  pk.window_width_h,
  lw.peak_window_state,
  lw.day_decided,
  lw.observed_at,

  -- ---- forecast provenance ----------------------------------------------
  -- Appended rather than placed beside forecast_max_c on purpose: `create or
  -- replace view` can only ADD columns at the end, and a desk that has to
  -- drop this view first is a desk whose dependent views cascade away.
  fc.lead_days                                             as forecast_lead_days,
  fc.run_at                                                as forecast_at,
  ro.observed_max_3d_c,
  -- A forecast more than 4C above everything observed in three days is not
  -- necessarily wrong - but it is the shape of a stale long-lead row, and the
  -- desk should be told rather than shown a number it cannot check.
  (fc.forecast_max_c is not null
     and ro.observed_max_3d_c is not null
     and fc.forecast_max_c - ro.observed_max_3d_c > 4)     as forecast_suspect
from cities c
left join v_city_climate cl on cl.city_key = c.city_key
left join live_weather lw   on lw.city_key = c.city_key
left join fc                on fc.city_key = c.city_key
left join recent_obs ro     on ro.city_key = c.city_key
left join skill sk          on sk.city_key = c.city_key
left join cap cp            on cp.city_key = c.city_key
left join div dv            on dv.city_key = c.city_key
left join edges e           on e.city_key = c.city_key
left join v_city_volume v   on v.city_key = c.city_key
left join derived_weather_peak pk
       on pk.city_key = c.city_key
      and pk.month = extract(month from coalesce(cl.local_today, current_date))::int;


-- --------------------------------------------------------------------------
-- 4. Grants. Section 8 of ad4_13_reconcile.sql revoked everything and granted
--    back a fixed list, so views created after it need SELECT re-granted.
-- --------------------------------------------------------------------------
do $ad4$
declare
  v text;
  r text;
begin
  foreach v in array array['v_city_daily_max', 'v_city_climate', 'v_city_stats'] loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if exists (select 1 from pg_roles where rolname = r) then
        execute format('grant select on %I to %I', v, r);
      end if;
    end loop;
  end loop;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 5. Report.
-- --------------------------------------------------------------------------
do $ad4$
declare
  v_cities   int;
  v_seasonal int;
  v_trailing int;
  v_none     int;
begin
  select count(*) into v_cities from v_city_stats;
  select count(*) into v_seasonal from v_city_stats where baseline = 'seasonal';
  select count(*) into v_trailing from v_city_stats where baseline = 'trailing_30d';
  select count(*) into v_none from v_city_stats where baseline = 'none' or baseline is null;

  raise notice 'ad4_17: % cities', v_cities;
  raise notice 'ad4_17: baseline - % seasonal, % trailing-30d, % with no history yet',
               v_seasonal, v_trailing, v_none;
  if v_none > 0 then
    raise notice 'ad4_17: a city with no baseline shows no hotness. It needs observation history - Actions -> Station Observations backfills it.';
  end if;
end
$ad4$;
