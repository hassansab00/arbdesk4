-- ===========================================================================
-- THE CLIMB PROFILE READS THE LAST 30 DAYS (plan v2 P1.6 phase 2, step 1;
-- 29 Sep).
--
-- v_city_climb_profile_live said two years and has always read whatever
-- retention left in weather_observations - 60 days lately, 30 once phase 2
-- lowers the keep. Phase 2 was going to give it a durable hourly cache of the
-- repository's history. Measured first (tools/p16_history_windows.py,
-- docs/HISTORY_WINDOWS_2026-09-29.md), on every raw reading the repository
-- holds, each test day predicted only from days before it:
--
--     window    CRPS of the climb left (259,715 city-day-hours, Sep 2025-Sep 2026)
--     30 days   0.5359
--     60 days   0.5444    60 minus 30: +0.0085, 90% [+0.0072, +0.0098]
--     90 days   0.5568
--     all       0.5767    60 minus all: -0.0323
--
-- 30 days is best in every quarter. The climb left in a day follows the
-- season; older days blur it. So the cache is not built, and the window is
-- stated in the view: the last 30 whole local days, whatever the table
-- keeps. Today is left out as the scoring left it out: it used to count once
-- it had 12 hours. The readings valid before the 29 Sep refresh (05:02Z) give
-- 16 Asian and Pacific cities such a day, cut at 13:00-18:00 local, with no
-- climb counted after the cut.
-- On 29 Sep the window serves 1,217 city-hour cells against 1,248 at 60; the
-- 31 it loses are lagos and jakarta, both retired cities with no markets.
--
-- The same body is in sql/ad4_26_temp_trend.sql and sql/ad4_28_feature_cache.sql
-- (tests/test_climb_profile_window.py holds the three identical). Same columns,
-- so create or replace is legal and refresh_feature_cache, which rebuilds
-- derived_climb_profile from this view, needs no change. Re-runnable.
-- ===========================================================================
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
    -- THE LAST 30 WHOLE LOCAL DAYS (29 Sep, plan v2 P1.6 phase 2). This
    -- said two years, and read whatever retention left - 60 days lately.
    -- Scored out of sample on a year of the repository's raw readings
    -- (tools/p16_history_windows.py, docs/HISTORY_WINDOWS_2026-09-29.md,
    -- 259,715 city-day-hours): 30 days beats 60 by 0.0085 CRPS [0.0072,
    -- 0.0098], in every quarter, and all the history is worse than 60 by
    -- 0.032. The climb left in a day follows the season, so older days
    -- only blur it. Stated here so the window no longer depends on how
    -- long weather_observations is kept; the archive keeps at least 32
    -- days (tests/test_climb_profile_window.py).
    --
    -- Today is left out, as it was in the scoring. The nightly refresh runs
    -- near 05:00Z, when an Asian city's day has 14 or 15 hours (to 13:00 or
    -- 14:00) and passes the 12-hour test, so any climb after that hour was
    -- counted as none. The readings valid before the 29 Sep refresh (05:02Z)
    -- give 16 cities such a day.
    and o.valid_at > now() - interval '32 days'
    and (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date
        between (now() at time zone coalesce(c.timezone, 'UTC'))::date - 30
            and (now() at time zone coalesce(c.timezone, 'UTC'))::date - 1
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
  'The live computation: the last 30 whole local days of hourly observations, windowed per city-day (the window measured best, 29 Sep). Only refresh_feature_cache() reads it; v_city_climb_profile serves the cache.';
