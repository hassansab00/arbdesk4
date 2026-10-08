-- ===========================================================================
-- ad4_weather_readers_read_the_caches.sql - THE LONG WEATHER READERS READ
-- THE CACHES (Fresh Supabase, part 2a; Hassan, 8 Oct: "offload Supabase
-- completely daily to the repo"; migration
-- 20261008200000_the_long_weather_readers_read_the_caches.sql).
--
-- Safe to run any time. After ad4_97, which keeps the caches these read:
-- derived_city_day_hours, derived_station_day_sources (ad4_29) and
-- derived_city_day_peak. Each reader takes the readings for the days they
-- hold whole and the cache before, so the readings' keep can fall to three
-- days without any of them reading less:
--
--   v_city_climb_profile_live  (first defined in ad4_26 and ad4_28, which run
--                               before the caches exist)
--   v_city_climate             (ad4_17)
--   refresh_weather_peak_city  (ad4_56): every day the readings hold, and
--                               derived_city_day_peak for the days they do not
--   v_city_utc_day_max         new: scripts/city_correlation.py's UTC days
-- ===========================================================================

-- ---------------------------------------------------------------------------
-- 3. The climb profile: the readings for whole days, the cached hours before.
-- ---------------------------------------------------------------------------
create or replace view v_city_climb_profile_live as
with held as (
  select min(valid_at) as oldest from weather_observations
),
-- THE FIRST WHOLE LOCAL DAY (Fresh Supabase, 8 Oct), as v_trajectory_evidence
-- takes it: the readings hold every day from here on whole; the days before
-- come from derived_city_day_hours, kept while each was whole.
whole_from as (
  select c.city_key,
         coalesce(case when ((h.oldest at time zone coalesce(c.timezone, 'UTC'))::date::timestamp
                              at time zone coalesce(c.timezone, 'UTC')) < h.oldest
                       then (h.oldest at time zone coalesce(c.timezone, 'UTC'))::date + 1
                       else (h.oldest at time zone coalesce(c.timezone, 'UTC'))::date
                  end, 'infinity'::date)                                   as first_whole,
         (now() at time zone coalesce(c.timezone, 'UTC'))::date            as local_today
    from cities c cross join held h
),
hourly as (
  select
    o.city_key,
    (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date            as local_date,
    floor(extract(hour from (o.valid_at at time zone coalesce(c.timezone, 'UTC'))))::int as local_hour,
    max(o.temp_c)                                                          as temp_c
  from weather_observations o
  join cities c on c.city_key = o.city_key
  join whole_from f on f.city_key = o.city_key
  where o.temp_c is not null
    -- THE LAST 30 WHOLE LOCAL DAYS (29 Sep, plan v2 P1.6 phase 2). This
    -- said two years, and read whatever retention left - 60 days lately.
    -- Scored out of sample on a year of the repository's raw readings
    -- (tools/p16_history_windows.py, docs/HISTORY_WINDOWS_2026-09-29.md,
    -- 259,715 city-day-hours): 30 days beats 60 by 0.0085 CRPS [0.0072,
    -- 0.0098], in every quarter, and all the history is worse than 60 by
    -- 0.032. The climb left in a day follows the season, so older days
    -- only blur it.
    --
    -- Today is left out, as it was in the scoring. The nightly refresh runs
    -- near 05:00Z, when an Asian city's day has 14 or 15 hours (to 13:00 or
    -- 14:00) and passes the 12-hour test, so any climb after that hour was
    -- counted as none. The readings valid before the 29 Sep refresh (05:02Z)
    -- give 16 cities such a day.
    and (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date >= f.first_whole
    and (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date
        between f.local_today - 30
            and f.local_today - 1
  group by 1, 2, 3
  union all
  -- The days before the first whole day, as refresh_city_day_hours kept them:
  -- slot s holds hour s - 1, null where the hour had no reading.
  select d.city_key, d.obs_date, (s.slot - 1)::int, s.temp_c
    from derived_city_day_hours d
    join whole_from f on f.city_key = d.city_key
    cross join lateral unnest(d.temp_c) with ordinality as s(temp_c, slot)
   where d.obs_date < f.first_whole
     and d.obs_date between f.local_today - 30 and f.local_today - 1
     and s.temp_c is not null
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
  'The live computation: the last 30 whole local days of hourly observations, windowed per city-day (the window measured best, 29 Sep) - the readings for every whole day they hold, derived_city_day_hours before (Fresh Supabase, 8 Oct). Only refresh_feature_cache() reads it; v_city_climb_profile serves the cache.';

-- ---------------------------------------------------------------------------
-- 4. The city climate: the same 30 days, the readings and the cache.
-- ---------------------------------------------------------------------------
create or replace view v_city_climate as
with today as (
  select c.city_key,
         (now() at time zone coalesce(c.timezone, 'UTC'))::date as local_today
    from cities c
),
held as (
  select min(valid_at) as oldest from weather_observations
),
whole_from as (
  select c.city_key,
         coalesce(case when ((h.oldest at time zone coalesce(c.timezone, 'UTC'))::date::timestamp
                              at time zone coalesce(c.timezone, 'UTC')) < h.oldest
                       then (h.oldest at time zone coalesce(c.timezone, 'UTC'))::date + 1
                       else (h.oldest at time zone coalesce(c.timezone, 'UTC'))::date
                  end, 'infinity'::date) as first_whole
    from cities c cross join held h
),
-- EACH DAY'S MAXIMUM OVER EVERY SOURCE, the 30 local days before today
-- (Fresh Supabase, 8 Oct): v_city_daily_max's figure, from the readings for
-- every whole day they hold and from derived_station_day_sources before.
-- Both branches below read only these days. The seasonal one has read no
-- more since the keep fell under a year, so it is never chosen.
daily as (
  select o.city_key,
         (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date as obs_date,
         max(o.temp_c) as max_c
    from weather_observations o
    join cities c on c.city_key = o.city_key
    join whole_from f on f.city_key = o.city_key
    join today t on t.city_key = o.city_key
   where o.temp_c is not null
     and (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date >= f.first_whole
     and (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date >= t.local_today - 30
     and (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date < t.local_today
   group by 1, 2
  union all
  select k.city_key, k.obs_date, max(k.max_c)
    from derived_station_day_sources k
    join whole_from f on f.city_key = k.city_key
    join today t on t.city_key = k.city_key
   where k.obs_date < f.first_whole
     and k.obs_date >= t.local_today - 30
     and k.obs_date < t.local_today
   group by 1, 2
),
seasonal as (
  select d.city_key,
         avg(d.max_c)::numeric(6,2)          as normal_max_c,
         stddev_samp(d.max_c)::numeric(6,2)  as sd_max_c,
         count(*)::int                       as n_days
    from daily d
    join today t on t.city_key = d.city_key
   where least(abs(extract(doy from d.obs_date) - extract(doy from t.local_today)),
               365 - abs(extract(doy from d.obs_date) - extract(doy from t.local_today))) <= 10
     and d.obs_date < t.local_today
   group by d.city_key
),
recent as (
  select d.city_key,
         avg(d.max_c)::numeric(6,2)          as normal_max_c,
         stddev_samp(d.max_c)::numeric(6,2)  as sd_max_c,
         count(*)::int                       as n_days
    from daily d
    join today t on t.city_key = d.city_key
   where d.obs_date >= t.local_today - 30
     and d.obs_date < t.local_today
   group by d.city_key
)
select
  t.city_key,
  t.local_today,
  case when coalesce(s.n_days, 0) >= 15 then 'seasonal'
       when coalesce(r.n_days, 0) >= 5  then 'trailing_30d'
       else 'none' end                                                  as baseline,
  case when coalesce(s.n_days, 0) >= 15 then s.normal_max_c else r.normal_max_c end as normal_max_c,
  case when coalesce(s.n_days, 0) >= 15 then s.sd_max_c     else r.sd_max_c     end as sd_max_c,
  case when coalesce(s.n_days, 0) >= 15 then s.n_days       else r.n_days       end as baseline_days
from today t
left join seasonal s on s.city_key = t.city_key
left join recent r   on r.city_key = t.city_key;

-- ---------------------------------------------------------------------------
-- 5. The peak hour: every day the readings hold, the cached peaks after.
-- ---------------------------------------------------------------------------
create or replace function public.refresh_weather_peak_city(p_city text, p_min_days int default 20)
returns int
language plpgsql
security definer
set search_path = public, extensions
as $ad4$
declare v_rows int;
begin
  with daily as (
    select
      o.city_key,
      (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date                          as local_date,
      extract(month from (o.valid_at at time zone coalesce(c.timezone, 'UTC')))::int       as month,
      extract(hour  from (o.valid_at at time zone coalesce(c.timezone, 'UTC')))
        + extract(minute from (o.valid_at at time zone coalesce(c.timezone, 'UTC'))) / 60.0 as local_hour,
      o.temp_c,
      count(*)      over (partition by (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date) as n_readings,
      row_number()  over (partition by (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date
                          order by o.temp_c desc, o.valid_at)                              as rk
    from weather_observations o
    join cities c on c.city_key = o.city_key
    where o.city_key = p_city
      and o.temp_c is not null
      and o.valid_at > now() - interval '3 years'
  ),
  peaks as (
    select city_key, month, local_date, local_hour
      from daily
     where rk = 1
       and n_readings >= 12
       -- A maximum at 02:00 is a front, not a diurnal peak.
       and local_hour between 8 and 22
    union all
    -- THE DAYS THE READINGS NO LONGER HOLD (Fresh Supabase, 8 Oct), as
    -- refresh_city_day_hours kept them while each was whole. A day the
    -- readings still hold, even one the prune has cut into, is read from the
    -- readings as before.
    select k.city_key, extract(month from k.obs_date)::int, k.obs_date, k.peak_local_hour
      from derived_city_day_peak k
      join cities c on c.city_key = k.city_key
     where k.city_key = p_city
       and k.obs_date > ((now() - interval '3 years') at time zone coalesce(c.timezone, 'UTC'))::date
       and k.n_readings >= 12
       and k.peak_local_hour between 8 and 22
       and not exists (select 1 from daily d where d.local_date = k.obs_date)
  ),
  agg as (
    select
      city_key, month,
      count(*)::int                                            as n_days,
      percentile_cont(0.5)  within group (order by local_hour)  as p50,
      percentile_cont(0.10) within group (order by local_hour)  as p10,
      percentile_cont(0.90) within group (order by local_hour)  as p90
    from peaks
    group by city_key, month
    having count(*) >= p_min_days
  )
  insert into derived_weather_peak (city_key, month, peak_hour_local, window_width_h, n_days, computed_at)
  select a.city_key, a.month,
         round(a.p50::numeric, 2),
         -- Never zero: see ad4_37.
         greatest(round((a.p90 - a.p10)::numeric, 2), 1.0),
         a.n_days, now()
  from agg a
  on conflict (city_key, month) do update
    set peak_hour_local = excluded.peak_hour_local,
        window_width_h  = excluded.window_width_h,
        n_days          = excluded.n_days,
        computed_at     = excluded.computed_at;

  get diagnostics v_rows = row_count;
  return v_rows;
end;
$ad4$;

comment on function public.refresh_weather_peak_city(text, int) is
  'One city''s peak hour and window width per calendar month, over three years: every day the readings hold, and derived_city_day_peak for the days they no longer hold (Fresh Supabase, 8 Oct). Sliced so each call is its own top-level statement with its own timeout budget.';

-- ---------------------------------------------------------------------------
-- 6. Each city's UTC-day maximum, for scripts/city_correlation.py.
-- ---------------------------------------------------------------------------
-- recompute_correlation grouped the readings by valid_at::date in a UTC
-- session, over every reading (a day whose readings all lack a temperature
-- still counts, with no maximum). The same, for the days the readings hold.
create or replace view v_city_utc_day_max as
select city_key,
       (valid_at at time zone 'UTC')::date as utc_date,
       max(temp_c)                         as max_c
  from weather_observations
 group by 1, 2;

comment on view v_city_utc_day_max is
  'Per city and UTC day the readings hold: the highest temperature (null when no reading had one). What scripts/city_correlation.py reads for the days the database holds; data/archive/observations holds the rest (Fresh Supabase, 8 Oct).';

revoke all on v_city_utc_day_max from anon, authenticated;
grant select on v_city_utc_day_max to service_role;
