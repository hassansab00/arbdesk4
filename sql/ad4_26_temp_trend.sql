-- ===========================================================================
-- ad4_26_temp_trend.sql - the shape of the day so far, not just its level.
--
-- WHY. The desk knows today's maximum SO FAR and today's forecast. It does not
-- know the thing a trader actually watches: which way the line is pointing and
-- how fast. "28.4C" an hour before peak means one thing if the last three
-- readings were 26.9, 27.7, 28.4 and the opposite if they were 29.1, 28.8,
-- 28.4 - the first day is still buying the band above, the second has already
-- sold it.
--
-- This became computable only once n8n P1.2 started archiving the whole
-- observation SERIES instead of one reading. A single latest reading has no
-- slope.
--
-- Two things are exposed:
--
--   v_city_temp_trend      where the line is pointing right now: the slope over
--                          the last 3 and last 6 readings, in degrees per hour,
--                          by ordinary least squares over the real timestamps.
--                          Uneven spacing is normal (METAR is hourly, SPECIs
--                          are not), so a naive last-minus-first over a
--                          6-reading window would be wrong whenever a SPECI
--                          lands.
--
--   v_city_climb_profile   how much this city has HISTORICALLY still climbed
--                          from this hour to its peak. That is the number that
--                          turns a slope into a decision: +0.9C/h at 13:00 is
--                          ordinary in Phoenix and remarkable in Seattle, and
--                          the difference is measurable in this desk's own
--                          archive rather than assumed.
--
-- Run order: after sql/ad4_21_weather_features.sql (for cities/observations
-- it is really only ad4_00, but the climb profile shares its local-day
-- definition and they should not drift). Re-runnable.
-- ===========================================================================

do $ad4$
begin
  if to_regclass('public.weather_observations') is null or to_regclass('public.cities') is null then
    raise exception 'ad4_26 needs cities and weather_observations - run sql/ad4_00_preflight.sql first';
  end if;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 1. Every reading of the current local day, per city, newest first.
--
--    Restricted to the local day on purpose: a slope that reaches back across
--    last night's minimum describes the diurnal cycle, not today's afternoon.
-- --------------------------------------------------------------------------
create or replace view v_city_today_readings as
select
  o.city_key,
  o.valid_at,
  o.temp_c,
  o.source,
  (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date          as local_date,
  extract(epoch from (o.valid_at at time zone coalesce(c.timezone, 'UTC'))
                     - date_trunc('day', o.valid_at at time zone coalesce(c.timezone, 'UTC')))
    / 3600.0                                                           as local_hour,
  row_number() over (partition by o.city_key order by o.valid_at desc) as recency
from weather_observations o
join cities c on c.city_key = o.city_key
where o.temp_c is not null
  and o.valid_at > now() - interval '36 hours'
  and (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date
      = (now() at time zone coalesce(c.timezone, 'UTC'))::date;

comment on view v_city_today_readings is
  'Every observation of the current LOCAL day per city. A slope that reaches back past last night describes the diurnal cycle, not this afternoon.';


-- --------------------------------------------------------------------------
-- 2. Which way the line is pointing, and how fast.
--
--    Ordinary least squares on (hours since first reading, temp), computed
--    twice: over the last 3 readings and over the last 6. The short window is
--    what a trader reacts to; the long one says whether the short one is a
--    turn or a wobble. Disagreement between them IS the signal that the day is
--    rolling over.
--
--    regr_slope is exact here - Postgres computes it in one pass and needs no
--    subquery - and it handles uneven spacing correctly, which last-minus-first
--    does not.
-- --------------------------------------------------------------------------
create or replace view v_city_temp_trend as
with r as (
  select * from v_city_today_readings
),
last3 as (
  select city_key,
         regr_slope(temp_c, local_hour) as slope,
         count(*)::int                  as n
  from r where recency <= 3 group by city_key having count(*) >= 2
),
last6 as (
  select city_key,
         regr_slope(temp_c, local_hour) as slope,
         count(*)::int                  as n
  from r where recency <= 6 group by city_key having count(*) >= 3
),
newest as (
  select distinct on (city_key)
    city_key, temp_c as latest_temp_c, valid_at as latest_at, local_hour as latest_hour, source
  from r order by city_key, valid_at desc
),
day as (
  select city_key,
         max(temp_c)                                   as running_max_c,
         min(temp_c)                                   as running_min_c,
         count(*)::int                                 as n_readings,
         max(temp_c) filter (where recency <= 3)       as recent_max_c
  from r group by city_key
)
select
  n.city_key,
  n.latest_temp_c,
  n.latest_at,
  round(n.latest_hour::numeric, 2)                     as latest_local_hour,
  n.source                                             as latest_source,
  d.running_max_c,
  d.running_min_c,
  d.n_readings,
  round(l3.slope::numeric, 3)                          as slope_3_c_per_h,
  l3.n                                                 as slope_3_n,
  round(l6.slope::numeric, 3)                          as slope_6_c_per_h,
  l6.n                                                 as slope_6_n,
  -- The readable form. Thresholds are deliberately not zero: instrument
  -- resolution is 0.1C and a 0.05C/h "trend" is noise wearing a sign.
  case
    when l3.slope is null then 'unknown'
    when l3.slope >  0.35 then 'climbing fast'
    when l3.slope >  0.10 then 'climbing'
    when l3.slope < -0.35 then 'falling fast'
    when l3.slope < -0.10 then 'falling'
    else 'flat'
  end                                                  as direction,
  -- The turn. The short window has gone negative while the long one is still
  -- positive: the afternoon has peaked and the average has not caught up.
  (l3.slope is not null and l6.slope is not null
     and l3.slope < -0.10 and l6.slope > 0.10)         as rolling_over,
  -- How far below today's own high the latest reading sits. A day that has
  -- given back a degree from its max is not going to make a new one quietly.
  round((d.running_max_c - n.latest_temp_c)::numeric, 2) as below_running_max_c,
  extract(epoch from (now() - n.latest_at)) / 60.0     as reading_age_min
from newest n
join day d   on d.city_key = n.city_key
left join last3 l3 on l3.city_key = n.city_key
left join last6 l6 on l6.city_key = n.city_key;

comment on view v_city_temp_trend is
  'Which way today''s temperature is pointing and how fast, by least squares over the real timestamps. rolling_over is the turn: the 3-reading slope negative while the 6-reading slope is still positive.';


-- --------------------------------------------------------------------------
-- 3. How much this city has historically still climbed from this hour.
--
--    The proprietary half. For every past local day in the archive, take the
--    temperature at each hour and the day's eventual maximum; the difference
--    is how much was still to come. Averaged per city and hour, that is a
--    measured answer to "is it too late to buy the band above?" - and it is
--    a different answer in every city.
--
--    Days are required to have at least 12 readings, the same bar
--    v_city_day_features uses: a sparsely observed day has an understated
--    maximum and would make the remaining climb look smaller than it is.
-- --------------------------------------------------------------------------
-- ADAPTIVE. sql/ad4_28 caches this into derived_climb_profile because the live
-- computation is two years of hourly observations windowed per city-day - 2.0s
-- on a modest archive, which Supabase cancels. Re-running THIS file must not
-- quietly put the slow version back under the name the UI reads, so when the
-- cache exists the live definition goes to _live and the cache keeps the name.
create or replace view v_city_climb_profile_live as
with hourly as (
  select
    o.city_key,
    (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date            as local_date,
    floor(extract(hour from (o.valid_at at time zone coalesce(c.timezone, 'UTC'))))::int as local_hour,
    max(o.temp_c)                                                          as temp_c
  from weather_observations o
  join cities c on c.city_key = o.city_key
  where o.temp_c is not null
    and o.valid_at > now() - interval '2 years'
  group by 1, 2, 3
),
counted as (
  select h.*, count(*) over (partition by h.city_key, h.local_date) as n_hours
  from hourly h
),
-- The climb still AHEAD, not the distance to the day's maximum wherever it
-- fell. The difference matters and the first version got it wrong: at 18:00
-- on a day that peaked at 16:00 it reported "0.49C still to climb", because
-- the reading had fallen 0.49 below a maximum that was already two hours in
-- the past. A trader reads that as room above and there is none.
--
-- A window frame over the rest of the day gives the honest number: the
-- highest temperature from this hour ONWARD, minus this hour's. After the
-- peak that is zero, which is the answer.
ahead as (
  select
    city_key, local_date, local_hour, temp_c,
    max(temp_c) over (
      partition by city_key, local_date
      order by local_hour
      rows between current row and unbounded following
    ) - temp_c                                           as climb_left_c
  from counted
  where n_hours >= 12
)
select
  a.city_key,
  a.local_hour,
  count(*)::int                                          as n_days,
  round(avg(a.climb_left_c)::numeric, 2)                 as typical_climb_left_c,
  round(stddev_samp(a.climb_left_c)::numeric, 2)         as climb_left_sd_c,
  -- The pessimistic case, which is the one that matters when deciding whether
  -- a band above the current reading is still reachable.
  round(percentile_cont(0.10) within group (order by a.climb_left_c)::numeric, 2)
                                                         as climb_left_p10_c,
  round(percentile_cont(0.90) within group (order by a.climb_left_c)::numeric, 2)
                                                         as climb_left_p90_c,
  -- How often the day was already over by this hour: nothing further ahead
  -- beat the reading in hand.
  round(100.0 * count(*) filter (where a.climb_left_c < 0.1) / count(*), 1)
                                                         as pct_already_peaked
from ahead a
group by a.city_key, a.local_hour
having count(*) >= 20;

comment on view v_city_climb_profile_live is
  'Measured, per city and local hour: how much more the day still climbed, historically. Turns a slope into a decision - +0.9C/h at 13:00 is ordinary in Phoenix and remarkable in Seattle.';

do $ad4$
begin
  if to_regclass('public.derived_climb_profile') is not null then
    execute $v$
      create or replace view v_city_climb_profile as
      select city_key, local_hour, n_days, typical_climb_left_c, climb_left_sd_c,
             climb_left_p10_c, climb_left_p90_c, pct_already_peaked
      from derived_climb_profile $v$;
    raise notice 'ad4_26: v_city_climb_profile reads the ad4_28 cache (fast). The live computation is v_city_climb_profile_live.';
  else
    execute $v$
      create or replace view v_city_climb_profile as
      select * from v_city_climb_profile_live $v$;
    raise notice 'ad4_26: v_city_climb_profile computes live - run sql/ad4_28_feature_cache.sql to cache it, or a page load pays seconds for it.';
  end if;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 4. The two joined: everything a pre-peak entry decision needs, in one row.
-- --------------------------------------------------------------------------
create or replace view v_city_peak_approach as
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
  -- The desk's best estimate of where today ends up, from observation alone:
  -- what is on the board now plus what this city usually still adds from here.
  -- Deliberately NOT a forecast - it is the number to hold a forecast against.
  case when p.typical_climb_left_c is null then null
       else round((t.latest_temp_c + p.typical_climb_left_c)::numeric, 2)
  end                                                    as implied_max_c,
  case when p.climb_left_p10_c is null then null
       else round((t.latest_temp_c + p.climb_left_p10_c)::numeric, 2)
  end                                                    as implied_max_low_c,
  case when p.climb_left_p90_c is null then null
       else round((t.latest_temp_c + p.climb_left_p90_c)::numeric, 2)
  end                                                    as implied_max_high_c
from v_city_temp_trend t
left join v_city_climb_profile p
       on p.city_key = t.city_key
      and p.local_hour = floor(t.latest_local_hour)::int;


-- --------------------------------------------------------------------------
-- 5. How the market has MOVED, per bucket.
--
--    book_snapshots already holds this - one row per band per snapshot - but
--    it carries no city and no label, so every consumer had to join three
--    tables to draw a line. Bounded to 48 hours and to markets that have not
--    resolved, because an unbounded history of every band is a quarter of a
--    million rows and no page needs it.
--
--    Filtered by city and day this is about a dozen bands x 48 snapshots,
--    which is a chart.
-- --------------------------------------------------------------------------
create or replace view v_band_price_history as
select
  s.band_id,
  m.city_key,
  m.resolution_date,
  b.band_label,
  b.band_lo,
  b.band_hi,
  b.open_low,
  b.open_high,
  s.observed_at,
  s.mid,
  s.best_bid,
  s.best_ask,
  s.spread,
  s.market_state
from book_snapshots s
join bands b   on b.band_id = s.band_id
join markets m on m.market_id = b.market_id
where s.observed_at > now() - interval '48 hours'
  and m.resolution_date >= current_date - 1;

comment on view v_band_price_history is
  'Per-bucket price history for live markets, last 48h. Bounded on purpose: unbounded it is a quarter of a million rows and no page needs it.';


-- --------------------------------------------------------------------------
-- 6. Grants.
-- --------------------------------------------------------------------------
do $ad4$
declare o text; r text;
begin
  foreach o in array array['v_city_today_readings', 'v_city_temp_trend',
                           'v_city_climb_profile', 'v_city_climb_profile_live',
                           'v_city_peak_approach',
                           'v_band_price_history'] loop
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
-- 7. Report.
-- --------------------------------------------------------------------------
do $ad4$
declare n_trend int; n_prof int; n_cities int;
begin
  select count(*) into n_trend from v_city_temp_trend;
  select count(distinct city_key) into n_cities from v_city_climb_profile;
  select count(*) into n_prof from v_city_climb_profile;

  raise notice 'ad4_26: % city/cities have a live trend today', n_trend;
  raise notice 'ad4_26: climb profile built for % city/cities over % city-hour(s)', n_cities, n_prof;
  if n_trend = 0 then
    raise notice 'ad4_26: no readings for the current local day - run n8n P1.2 (NWS Monitor) or Actions -> Observations.';
  end if;
  if n_cities = 0 then
    raise notice 'ad4_26: the climb profile needs 20+ well-observed days per city-hour. Backfill with Actions -> Observations.';
  end if;
end
$ad4$;
