-- ===========================================================================
-- THE LABEL IS WHAT THE VENUE READS (4 Oct 2026)
--
-- derived_city_day_features.max_c is the station label the models train on
-- (station_mos nightly, station_correction, weather_model weekly, the S10
-- files). It is cached from v_city_day_features, whose max_c was the maximum
-- over every reading of the local day. From 5 Sep 2026 the US cities also
-- carry NWS five-minute readings (whole C, ~270 a day; first 5 Sep 06:46Z, in
-- the database and the repository alike) beside the routine reports the venue
-- settles on (source 'IEM', obs_primary_source()):
--   * 5 Sep - 2 Oct, 297 US city-days: the IEM daily maximum was in the
--     venue's winning bucket on 286, the maximum over every source on 213.
--   * 1 Aug - 2 Oct, 462 US city-days: the cached label averaged +0.187 C
--     above the settlement's observed maximum; 54 differed by 0.6 C or more.
--   * Before 5 Sep every US reading was IEM (data/archive/observations and
--     data/mirror/weather_observations), so no earlier label changes.
--
-- max_c is now the settlement feed's maximum when the day has any, else the
-- maximum over what was measured. max_c_all_sources (the old value) and
-- max_c_source are appended. The cached rows of 5 Sep onward are recomputed
-- by refresh_feature_cache from readings the database still holds (oldest
-- 2 Sep 02:47Z on 4 Oct); data/repairs/2026-10-04-settlement-max records them
-- before and after.
--
-- The statement is sql/ad4_21_weather_features.sql's, verbatim
-- (tests/test_the_label_is_what_the_venue_reads.py holds them equal), as
-- CREATE OR REPLACE so its dependents stay, and applied only where the view
-- exists (the contract fixtures have none). Re-runnable.
-- ===========================================================================

do $mig$
begin
  if to_regclass('public.v_city_day_features') is not null then
    execute $v$create or replace view public.v_city_day_features as
-- NOT MATERIALIZED is load-bearing, not a hint.
--
-- `obs` is referenced twice below (by `daily` and by `morning`), and a CTE
-- referenced more than once is MATERIALIZED by default: Postgres evaluates it
-- once, whole, and only then applies the caller's WHERE. So
-- `... from v_city_day_features where city_key = 'x'` read all 710,000
-- observations and threw away 691,000 of them - the filter could not reach the
-- scan. Every per-city or per-day read of this view cost the same as reading
-- all of it, which is what made refresh_feature_cache() a single seven-second
-- statement that Supabase cancels (HTTP 500 to the caller, and the Archive
-- Observations job correctly refusing to prune behind it).
--
-- Inlined, the predicate reaches weather_observations and uses
-- (city_key, valid_at). Measured on a 710k-row archive:
--
--   where city_key = 'c1'   823 ms  ->   54 ms
--   no filter at all        946 ms  ->  810 ms
--
-- The two evaluations cost less than one materialisation of everything, and
-- the results are identical: checked both ways on 29,600 city-days, all 18
-- feature columns agree on both the sum and the non-null count - including
-- prev_max_c and pressure_change_24h_hpa, the two window terms most at risk
-- from a plan change.
with obs as not materialized (
  select
    o.city_key,
    (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date            as obs_date,
    extract(hour from (o.valid_at at time zone coalesce(c.timezone, 'UTC'))) as local_hour,
    o.temp_c, o.dewpoint_c, o.humidity, o.wind_speed, o.precip,
    o.cloud_cover, o.pressure_hpa, o.wind_dir_deg, o.source
  from weather_observations o
  left join cities c on c.city_key = o.city_key
  where o.temp_c is not null
),
daily as (
  select
    city_key, obs_date,
    -- THE SETTLEMENT FEED'S MAXIMUM (4 Oct). This is the label every model
    -- trains on, and the venue settles on the station's routine reports
    -- (source 'IEM', obs_primary_source()). From 5 Sep the US cities also
    -- carry NWS five-minute readings (whole C), whose maximum runs warm: on
    -- 5 Sep - 2 Oct the IEM maximum was in the venue's winning bucket on 286
    -- of 297 US city-days and the maximum over every source on 213. Before
    -- 5 Sep every reading was IEM, so those days are unchanged. A day the
    -- settlement feed missed keeps the maximum over what was measured.
    coalesce(max(temp_c) filter (where source = 'IEM'), max(temp_c)) as max_c,
    max(temp_c)                                       as max_c_all_sources,
    count(*) filter (where source = 'IEM')            as n_settlement,
    min(temp_c)                                       as min_c,
    count(*)::int                                     as n_obs,
    -- daytime cloud and wind: what the sun had to work through
    avg(cloud_cover) filter (where local_hour between 9 and 17)   as cloud_mean,
    max(cloud_cover) filter (where local_hour between 9 and 17)   as cloud_max,
    avg(wind_speed)  filter (where local_hour between 9 and 17)   as wind_mean,
    -- wind_max exists on the FORECAST side (weather_forecast_features) and did
    -- not exist here, so a model that used it could be fitted and then never
    -- applied. The two column sets have to match or the forward model dies
    -- silently, one skipped row at a time.
    max(wind_speed)  filter (where local_hour between 9 and 17)   as wind_max,
    sum(coalesce(precip, 0))                                      as precip_total,
    -- WIND DIRECTION, AS A VECTOR. It was collected on 130,572 of 144,601
    -- observations - 90.3% - and aggregated nowhere: the only consumer was
    -- live_weather.py turning it into a compass glyph for the UI.
    --
    -- A MEAN OF DEGREES IS NOT A MEAN WIND. 350 and 10 average to 180, which
    -- is the opposite direction, so the bearing has to be resolved into
    -- components before anything is summed. These four sums are the pieces of
    -- the speed-weighted resultant; the view divides them, because dividing
    -- inside an aggregate cannot carry the weighting.
    --
    -- Meteorological convention: the bearing is where the wind comes FROM, so
    -- the vector it blows TOWARD is (-sin, -cos).
    sum(-wind_speed * sin(radians(wind_dir_deg)))
      filter (where local_hour between 9 and 17
                and wind_dir_deg is not null and wind_speed is not null)  as wd_su,
    sum(-wind_speed * cos(radians(wind_dir_deg)))
      filter (where local_hour between 9 and 17
                and wind_dir_deg is not null and wind_speed is not null)  as wd_sv,
    sum(wind_speed)
      filter (where local_hour between 9 and 17
                and wind_dir_deg is not null and wind_speed is not null)  as wd_scalar,
    count(*)
      filter (where local_hour between 9 and 17
                and wind_dir_deg is not null and wind_speed is not null)  as wd_n
  from obs group by city_key, obs_date
),
morning as (
  -- The reading nearest 08:00 local, and the window's own averages. Nearest
  -- rather than averaged for temperature and dewpoint, because the DEPRESSION
  -- between them at a single instant is the physical quantity; averaging two
  -- series separately and subtracting is not the same thing.
  select distinct on (city_key, obs_date)
    city_key, obs_date,
    temp_c                                   as morning_temp_c,
    dewpoint_c                               as morning_dewpoint_c,
    (temp_c - dewpoint_c)                    as dewpoint_depression_c,
    humidity                                 as morning_humidity,
    pressure_hpa                             as morning_pressure_hpa
  from obs
  where local_hour between 6 and 10 and dewpoint_c is not null
  order by city_key, obs_date, abs(local_hour - 8)
)
select
  d.city_key,
  d.obs_date,
  d.max_c,
  d.min_c,
  (d.max_c - d.min_c)                        as diurnal_range_c,
  d.n_obs,
  -- persistence: the benchmark, and a feature in its own right
  lag(d.max_c) over (partition by d.city_key order by d.obs_date)  as prev_max_c,
  d.max_c - lag(d.max_c) over (partition by d.city_key order by d.obs_date) as delta_max_c,
  m.morning_temp_c,
  m.morning_dewpoint_c,
  m.dewpoint_depression_c,
  m.morning_humidity,
  m.morning_pressure_hpa,
  -- how far the day climbed from its morning reading: the quantity the
  -- covariates below actually explain
  (d.max_c - m.morning_temp_c)               as morning_to_max_c,
  round(d.cloud_mean, 2)                     as cloud_mean,
  d.cloud_max,
  round(d.wind_mean, 2)                      as wind_mean,
  round(d.precip_total, 3)                   as precip_total,

  -- ---- appended (create or replace can only add columns at the end) -------
  round(d.wind_max, 2)                       as wind_max,
  -- PRESSURE TENDENCY, not the level. Station pressure is mostly a statement
  -- about altitude: Denver reads ~840 hPa and Miami ~1015 every single day, so
  -- the level is near-constant per city and the intercept absorbs it. What
  -- carries information is the CHANGE - falling pressure is an approaching
  -- front, and a front is exactly when a persistence-style forecast fails.
  round(m.morning_pressure_hpa
        - lag(m.morning_pressure_hpa) over (partition by d.city_key order by d.obs_date), 2)
                                             as pressure_change_24h_hpa,

  -- The speed-weighted resultant, divided by the scalar wind run. That makes
  -- each component the direction's east/north share TIMES the day's
  -- directional constancy: 1.0 is a steady wind from one quarter, 0.0 is a
  -- day that boxed the compass. Both are dimensionless and bounded by [-1, 1],
  -- which is what lets one coefficient mean the same thing in Denver and
  -- Singapore - a raw u in knots would make the term a proxy for how windy
  -- the city is rather than for where its wind comes from.
  --
  -- THREE READINGS, because two points make a resultant out of nothing much
  -- and the daytime window is nine hours; 96.9% of city-days clear it.
  case when d.wd_n >= 3 and d.wd_scalar > 0
       then round((d.wd_su / d.wd_scalar)::numeric, 4) end   as wind_u_mean,
  case when d.wd_n >= 3 and d.wd_scalar > 0
       then round((d.wd_sv / d.wd_scalar)::numeric, 4) end   as wind_v_mean,
  -- Appended (4 Oct): the maximum over every source, as max_c was before, and
  -- which of the two max_c is.
  d.max_c_all_sources,
  case when d.n_settlement > 0 then 'settlement_feed' else 'all_sources' end as max_c_source
from daily d
left join morning m on m.city_key = d.city_key and m.obs_date = d.obs_date;$v$;
  else
    raise notice 'v_city_day_features not installed here; nothing to replace';
  end if;
end
$mig$;
