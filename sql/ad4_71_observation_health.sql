-- ===========================================================================
-- ad4_71_observation_health.sql - WHICH CITIES CAN THE DESK ACTUALLY TIME?
--
-- THE PROBLEM, IN THREE MEASURED PARTS
-- ------------------------------------
-- 1. THE OBSERVATION FEED IS A DAY BEHIND OUTSIDE THE AMERICAS, and that is
--    not a fault - it is what IEM serves. Measured 2026-09-19:
--
--      Americas  17 cities, 11 fresh inside 3h, ~340 readings/day (5-minutely)
--      Europe    11 cities,  0 fresh inside 3h,   24 readings/day (hourly)
--      Asia      23 cities,  0 fresh inside 3h,   24 readings/day (hourly)
--
--    The history is COMPLETE - exactly 24 hourly rows a day, no gaps - it
--    simply arrives about a day late. Training, settlement and outcome
--    scoring are unaffected. What is affected is anything that needs TODAY.
--
-- 2. SO 37 OF 54 CITIES HAVE NO INTRADAY TIMING AT ALL. v_city_today_readings
--    filters weather_observations to the city's local date; for those cities
--    it returns nothing, so v_city_temp_trend, v_city_peak_approach and
--    v_trade_timing all have no row, and s7 - which needs a reading under 90
--    minutes old - has never fired once in the platform's history.
--
-- 3. AND THE STORED RUNNING MAXIMUM IS NOT RELIABLE. live_weather is current
--    for all 54 cities from n8n, but its running_max_c was, on 2026-09-19:
--
--      25 cities  no running maximum at all
--      14 cities  a CURRENT TEMPERATURE ABOVE THE STORED MAXIMUM,
--                 worst by 12.5 C - which is arithmetically impossible
--      12 of 54   station-sourced; the other 42 are model values
--
--    Only 15 of 54 were even self-consistent. That field drives
--    holds_running_max, out_of_reach, day_decided, strategy s5 and the
--    observed-max floor in the probability engine, so an understated maximum
--    leaves probability on buckets the day has already passed.
--
-- WHAT THIS FILE DOES
-- -------------------
-- It does not try to fix the feed - IEM's international latency is not ours
-- to change, and live_weather has two writers (scripts/live_weather.py, and
-- n8n, which is the one currently running). It makes the consumers correct
-- and the situation visible:
--
--   ad4_running_max_basis()     ONE definition of what a running maximum
--                               rests on, so the view that reports it and the
--                               function that stores it cannot drift apart.
--   live_weather.readings_today two columns carrying the provenance NEXT TO
--   live_weather.running_max_basis   the number, because every consumer
--                               already reads this row and none of them would
--                               make a second request for it.
--   v_city_observation_health   one row per city: how old each feed is, how
--                               many readings today, and whether a running
--                               maximum can be trusted - with the reason.
--   v_city_running_max          the maximum the desk should USE, never below
--                               the latest reading, and honest about whether
--                               it rests on a series or a single point.
--
-- A SINGLE READING IS A FLOOR, NOT A MAXIMUM. If the only thing known about
-- today is the current temperature, then the day's maximum is AT LEAST that
-- and possibly much more. Presenting it as the maximum is what makes a band
-- look reachable when it is not, and it is why this view reports
-- 'floor_only' rather than quietly returning a number.
--
-- WHAT COUNTS AS "TODAY'S READINGS" IS NOT REDEFINED HERE. It is read from
-- v_city_today_readings, which is what v_city_temp_trend, v_city_peak_approach
-- and refresh_live_weather_timing() all count. A second definition here would
-- let this view call a city timeable that the timing chain finds empty, which
-- is the exact class of bug it exists to catch.
--
-- RUN ORDER: after ad4_26_temp_trend.sql and ad4_live_weather.sql, and BEFORE
-- ad4_live_weather_timing.sql, whose refresh function writes the two columns
-- added below. Re-runnable.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- 1. THE ONE DEFINITION.
--
-- Two places need to answer "what does this maximum rest on?": the view that
-- reports it to a person, and the function that stores it for a strategy. If
-- each carried its own CASE, the day they disagreed the view would say the
-- number was trustworthy while the strategy acted on something else. So there
-- is one function and both call it.
--
--   series      two or more readings of this city's own day. A maximum.
--   floor_only  one reading, or only the live thermometer. The day reached AT
--               LEAST this; it may have reached far more. NOT a maximum.
--   absent      nothing measured today at all.
-- --------------------------------------------------------------------------
create or replace function public.ad4_running_max_basis(
  p_readings_today integer,
  p_latest_temp_c  numeric
) returns text
language sql
immutable
as $$
  select case
           when coalesce(p_readings_today, 0) >= 2 then 'series'
           when coalesce(p_readings_today, 0) = 1
             or p_latest_temp_c is not null        then 'floor_only'
           else                                         'absent'
         end
$$;

comment on function public.ad4_running_max_basis(integer, numeric) is
  'What a city''s running maximum rests on: series (two or more of today''s readings - a real maximum), floor_only (one reading or only the live thermometer - the day reached at least this and possibly much more), absent (nothing measured today).';

grant execute on function public.ad4_running_max_basis(integer, numeric)
  to anon, authenticated, service_role;


-- --------------------------------------------------------------------------
-- 2. THE PROVENANCE TRAVELS WITH THE NUMBER.
--
-- live_weather already holds one row per city and every consumer reads it -
-- signal_engine with select *, the probability engine, six UI pages. Putting
-- the basis in a view they would each have to join means the ones that never
-- join it keep acting on a floor as though it were a maximum. These columns
-- are written by refresh_live_weather_timing() alongside running_max_c
-- itself, in the same statement, so the number and what it rests on cannot
-- come from different moments.
-- --------------------------------------------------------------------------
alter table public.live_weather
  add column if not exists readings_today    integer;
alter table public.live_weather
  add column if not exists running_max_basis text;

comment on column public.live_weather.readings_today is
  'How many observations of this city''s own local day v_city_today_readings holds. 0 for the 37 cities whose IEM feed runs about a day behind.';
comment on column public.live_weather.running_max_basis is
  'series | floor_only | absent - see ad4_running_max_basis(). A strategy that needs a settled maximum (s5) must require series; a floor under a probability (the observed-max floor) may use floor_only.';


-- --------------------------------------------------------------------------
-- 3. ONE ROW PER CITY, SAYING WHAT IS ACTUALLY KNOWN ABOUT TODAY.
--
-- v_data_freshness answers this per TABLE, and per table the answer is
-- green: weather_observations has rows from four minutes ago, because
-- seventeen American cities report every five minutes. The forty cities that
-- are a day behind are invisible inside that average. Freshness that matters
-- to a trade is per city or it is nothing.
-- --------------------------------------------------------------------------
drop view if exists v_city_running_max;
drop view if exists v_city_observation_health;

create view v_city_observation_health as
with tz as (
  select c.city_key,
         c.display_name,
         coalesce(c.timezone, 'UTC')                                   as timezone,
         (now() at time zone coalesce(c.timezone, 'UTC'))::date         as local_date,
         extract(hour from (now() at time zone coalesce(c.timezone, 'UTC')))::int as local_hour
  from cities c
  where coalesce(c.status, 'active') = 'active'
),
-- Today's readings, counted by the SAME view the timing chain reads, so this
-- cannot call a city timeable that v_city_temp_trend finds empty.
today as (
  select r.city_key,
         count(*)                                                       as readings_today,
         max(r.temp_c)                                                  as observed_max_today_c,
         max(r.valid_at)                                                as newest_today_at
  from v_city_today_readings r
  group by r.city_key
),
-- Feed age looks PAST today on purpose: a city whose newest reading is
-- thirty hours old has an age to report, not an absence.
feed as (
  select t.city_key,
         (select max(o.valid_at)
            from weather_observations o
           where o.city_key = t.city_key
             and o.temp_c is not null)                                  as newest_reading
  from tz t
)
select
  t.city_key,
  t.display_name,
  t.timezone,
  t.local_date,
  t.local_hour,

  -- ---- the two feeds, separately ---------------------------------------
  round(extract(epoch from (now() - f.newest_reading)) / 3600.0, 1)     as obs_feed_age_h,
  coalesce(d.readings_today, 0)                                         as readings_today,
  round(extract(epoch from (now() - lw.observed_at)) / 60.0)            as live_feed_age_min,
  lw.source_kind                                                        as live_source_kind,

  -- The live thermometer as stored, for display and for its age...
  lw.temp_c                                                             as latest_temp_c,
  -- ...and the same reading ONLY while it belongs to this city's current
  -- local day, which is the only version a maximum may be built from.
  --
  -- The sixteen Chinese and south-east Asian cities are why this is two
  -- columns. At 01:06 in Shanghai the newest live reading is 23:00 YESTERDAY;
  -- treating it as today's floor would carry 28.7 C into a day on which
  -- nothing has been measured, which is the same error as handing today's
  -- running maximum to tomorrow's market.
  case when (lw.observed_at at time zone t.timezone)::date = t.local_date
       then lw.temp_c end                                               as latest_temp_today_c,

  d.observed_max_today_c,
  lw.running_max_c                                                      as stored_running_max_c,

  -- ---- can the running maximum be believed? -----------------------------
  public.ad4_running_max_basis(
    coalesce(d.readings_today, 0)::integer,
    case when (lw.observed_at at time zone t.timezone)::date = t.local_date
         then lw.temp_c end)                                            as running_max_basis,

  -- The stored field disagreeing with a reading from the SAME day is its own
  -- fault and worth naming, because it is silent everywhere else.
  (lw.running_max_c is not null
     and (lw.observed_at at time zone t.timezone)::date = t.local_date
     and lw.temp_c is not null
     and lw.temp_c > lw.running_max_c)                                  as stored_max_below_latest,

  -- ---- the one question every consumer is really asking -----------------
  -- Two readings make a slope; ninety minutes is s7's own staleness gate.
  (coalesce(d.readings_today, 0) >= 2
     and extract(epoch from (now() - d.newest_today_at)) / 60.0 <= 90)  as timing_trustworthy,

  case
    when coalesce(d.readings_today, 0) = 0
     and (lw.observed_at is null
          or (lw.observed_at at time zone t.timezone)::date <> t.local_date) then
      format('Nothing measured on %s yet. The newest live reading belongs to another day, so '
             'nothing here can say where this one is going.', t.local_date)
    when coalesce(d.readings_today, 0) = 0 then
      format('The observation feed has nothing for %s yet - it runs about a day behind for this '
             'city - so the only thing known about today is the live reading. The day''s maximum '
             'is at least %s C and may be far higher.', t.local_date, lw.temp_c)
    when coalesce(d.readings_today, 0) = 1 then
      'One reading today. That is a floor under the maximum, not the maximum.'
    when extract(epoch from (now() - d.newest_today_at)) / 60.0 > 90 then
      format('%s readings today but the newest is %s minutes old - too stale for an entry window.',
             d.readings_today, round(extract(epoch from (now() - d.newest_today_at)) / 60.0))
    else
      format('%s readings today, newest %s minutes old.', d.readings_today,
             round(extract(epoch from (now() - d.newest_today_at)) / 60.0))
  end                                                                   as note
from tz t
left join today d          on d.city_key  = t.city_key
left join feed f           on f.city_key  = t.city_key
left join live_weather lw  on lw.city_key = t.city_key;

comment on view v_city_observation_health is
  'One row per city: how old each weather feed is, how many readings exist for that city''s own day, and whether a running maximum rests on a series, a single point or nothing. The table-level freshness view cannot see this - a handful of fresh cities keep weather_observations green while forty are a day behind.';


-- --------------------------------------------------------------------------
-- 4. THE MAXIMUM THE DESK SHOULD USE.
--
-- NEVER BELOW THE LATEST READING OF THE SAME DAY. That invariant is not a
-- refinement, it is arithmetic, and it was violated for 14 of 54 cities - by
-- up to 12.5 C. A maximum below the current temperature makes bands the day
-- has already cleared look unreachable, and leaves probability mass on
-- buckets that cannot happen.
--
-- Consumers get the basis alongside the number so they can refuse a floor
-- where they need a maximum. s5 ("the day is over and the maximum is locked
-- in this band") must only ever run on 'series'.
-- --------------------------------------------------------------------------
create view v_city_running_max as
select
  h.city_key,
  h.local_date,
  h.running_max_basis,
  h.timing_trustworthy,
  -- greatest() ignores nulls, so a city with only one of the three still gets
  -- the one it has - and every one of the three is about TODAY.
  greatest(h.observed_max_today_c, h.stored_running_max_c, h.latest_temp_today_c) as running_max_c,
  h.observed_max_today_c,
  h.stored_running_max_c,
  h.latest_temp_today_c,
  h.latest_temp_c,
  h.readings_today,
  h.stored_max_below_latest,
  h.note
from v_city_observation_health h;

comment on view v_city_running_max is
  'The running maximum a consumer should use, guaranteed never below the latest reading of the same day, with the basis it rests on. running_max_basis = series means a real maximum; floor_only means the day reached at least this and possibly much more; absent means nothing is known about today.';

grant select on v_city_observation_health to anon, authenticated, service_role;
grant select on v_city_running_max        to anon, authenticated, service_role;
