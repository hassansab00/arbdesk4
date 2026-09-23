-- ===========================================================================
-- ad4_86_trajectory.sql - THE DAY'S OWN TRAJECTORY, AS EVIDENCE AND AS A GATE.
--
-- scripts/trajectory.py holds the arithmetic and the derivation; this file is
-- the evidence it fits on and the table it writes. See that module's header
-- for why the floor the desk applies today is the wrong shape and what the
-- identity actually implies.
--
-- WHY THE BASELINE IS THE PUBLISHED DISTRIBUTION AND NOT A RECONSTRUCTED ONE.
-- band_probabilities carries forecast_max_c, bias_applied_c and sigma_c - the
-- three numbers the desk actually quoted. Rebuilding that centre from skill
-- tables would be a second implementation of what probability_engine does, and
-- two implementations of "what we published" drift apart and then nobody knows
-- which was real.
--
-- ONE PUBLISHED ROW PER CITY-DAY, THE LAST OF THE DAY, and the choice is
-- deliberately the unfavourable one. The intraday pipeline recomputes about
-- six times a day; within one day the forecast centre barely moves, so any of
-- them is representative. Taking the LAST means the trajectory at 09:00 is
-- scored against the sharpest forecast the desk ever managed for that day
-- rather than the one standing at 09:00. That makes the bar harder to clear,
-- which is the direction a gate should err.
--
-- THE TARGET IS THE VERIFIED STATION MAXIMUM where one exists, because that is
-- what the band settles on, and the observed series maximum otherwise. Which
-- of the two was used travels on the row as `final_is_verified` rather than
-- being silently mixed.
--
-- RUN ORDER: after ad4_28_feature_cache.sql (derived_climb_profile) and
-- ad4_18_databank.sql. One table, one evidence view, two read views.
-- ===========================================================================

create table if not exists derived_trajectory (
  city_key          text        not null,
  local_hour        int         not null,

  n_days            int         not null default 0,
  climb_n_days      int,                             -- behind the profile itself
  sd_ratio          numeric     not null default 1,  -- corrects the climatological width

  crps_trajectory   numeric,
  crps_forecast     numeric,
  crps_gain         numeric,
  applied           boolean     not null default false,
  reason            text        not null default '',

  computed_at       timestamptz not null default now(),
  primary key (city_key, local_hour)
);

comment on table derived_trajectory is
  'Per city and local hour: whether pricing the rest of the day from the climb profile beats the floored forecast the desk publishes, measured by held-out CRPS. Written by scripts/trajectory.py.';
comment on column derived_trajectory.sd_ratio is
  'Multiplies derived_climb_profile.climb_left_sd_c. That spread is climatological - how variable the remaining climb has been at this hour over ~89 days - and this is the factor that makes it match the errors actually seen.';
comment on column derived_trajectory.applied is
  'False keeps the hour in shadow: the engine prices it from the forecast exactly as it did before this table existed.';

create index if not exists dtj_applied on derived_trajectory (applied, city_key);
create index if not exists dtj_computed on derived_trajectory (computed_at desc);


-- --------------------------------------------------------------------------
-- The evidence: one row per city, settled day and local hour.
-- --------------------------------------------------------------------------
create or replace view v_trajectory_evidence as
with hourly as (
  select o.city_key,
         (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date            as local_date,
         extract(hour from o.valid_at at time zone coalesce(c.timezone, 'UTC'))::int as local_hour,
         max(o.temp_c)                                                          as temp_c
  from weather_observations o
  join cities c on c.city_key = o.city_key
  where o.temp_c is not null
  group by 1, 2, 3
),
walked as (
  select city_key, local_date, local_hour, temp_c,
         -- THE RUNNING MAXIMUM IS THE FLOOR, and it has to be the maximum of
         -- the hours SO FAR, not of the day: using the day's would hand the
         -- fit the answer it is being asked to predict.
         max(temp_c) over (partition by city_key, local_date order by local_hour
                           rows between unbounded preceding and current row)    as running_max_c,
         max(temp_c) over (partition by city_key, local_date)                   as series_max_c,
         count(*)    over (partition by city_key, local_date)                   as n_hours
  from hourly
),
published as (
  select distinct on (q.city_key, q.for_date)
         q.city_key, q.for_date,
         q.forecast_max_c - coalesce(q.bias_applied_c, 0) as centre_c,
         -- THE BASELINE IS THE FORECAST'S OWN WIDTH, NEVER THE PUBLISHED ONE.
         -- Where the trajectory fired, sigma_c IS the trajectory's sigma while
         -- forecast_max_c - bias_applied_c is still the forecast's centre.
         -- Pairing those two scores the trajectory against a baseline wearing
         -- the trajectory's own narrow spread: the baseline loses by
         -- construction, more hours get applied, more rows are written that
         -- way, and the layer ends up proving itself.
         --
         -- NULL forecast_sigma_c IS NOT COALESCED TO sigma_c, and the reason
         -- is that the two mean different things at different times. Every row
         -- written before derived_trajectory first existed (2026-09-22
         -- 17:50:46) has forecast_sigma_c = sigma_c as a matter of fact -
         -- nothing could have replaced it - and those 66,345 rows were
         -- stamped. The 1,056 rows written by the one intraday run between
         -- that fit and this column existing are the only rows where the
         -- forecast's width is genuinely unrecoverable, and guessing it from a
         -- neighbouring run would be inventing the number this view exists to
         -- report. They are dropped; those city-days fall back to the
         -- preceding run of the same day, which is a real published
         -- forecast-path row.
         q.forecast_sigma_c                                 as sigma_c,
         q.computed_at
  from (
    select m.city_key, m.resolution_date as for_date,
           bp.forecast_max_c, bp.bias_applied_c, bp.sigma_c,
           bp.forecast_sigma_c, bp.computed_at
    from band_probabilities bp
    join bands b   on b.band_id = bp.band_id
    join markets m on m.market_id = b.market_id
    where bp.sigma_c is not null and bp.sigma_c > 0
      and bp.forecast_sigma_c is not null and bp.forecast_sigma_c > 0
      and bp.forecast_max_c is not null
  ) q
  order by q.city_key, q.for_date, q.computed_at desc
)
select
  w.city_key,
  w.local_date,
  w.local_hour,
  w.temp_c,
  w.running_max_c,
  coalesce(v.observed_max_c, w.series_max_c)  as final_max_c,
  (v.observed_max_c is not null)              as final_is_verified,
  cp.typical_climb_left_c                     as climb_left_c,
  cp.climb_left_sd_c                          as climb_sd_c,
  cp.n_days                                   as climb_n_days,
  p.centre_c                                  as forecast_c,
  p.sigma_c                                   as forecast_sigma_c
from walked w
join derived_climb_profile cp
  on cp.city_key = w.city_key and cp.local_hour = w.local_hour
join published p
  on p.city_key = w.city_key and p.for_date = w.local_date
left join v_verified_weather_outcomes v
  on v.city_key = w.city_key and v.for_date = w.local_date
where w.n_hours >= 12
  and cp.typical_climb_left_c is not null
  and cp.climb_left_sd_c is not null;

comment on view v_trajectory_evidence is
  'One row per city, settled day and local hour: the reading and running maximum at that hour, what the climb profile says is still to come, what the desk actually published for that day, and what the day finally reached. The fitting set for scripts/trajectory.py.';


-- --------------------------------------------------------------------------
-- What the engine reads. Only the hours that beat the floored forecast.
-- --------------------------------------------------------------------------
create or replace view v_trajectory_applied as
select city_key, local_hour, sd_ratio, n_days, crps_gain, computed_at
  from derived_trajectory
 where applied;

comment on view v_trajectory_applied is
  'The city-hours the desk is entitled to price from the day''s trajectory. Empty until scripts/trajectory.py finds one that beats the floored forecast out of sample.';


-- --------------------------------------------------------------------------
-- What a human reads.
-- --------------------------------------------------------------------------
create or replace view v_trajectory_health as
select
  t.city_key,
  t.local_hour,
  t.n_days,
  t.climb_n_days,
  t.sd_ratio,
  t.crps_forecast,
  t.crps_trajectory,
  t.crps_gain,
  t.applied,
  cp.typical_climb_left_c,
  cp.climb_left_sd_c,
  cp.pct_already_peaked,
  case
    when not t.applied and t.n_days < 10       then 'waiting: too few settled days at this hour'
    when not t.applied                         then 'shadow: the forecast is still better here'
    when cp.pct_already_peaked >= 70           then 'applied: the day is usually over by now'
    when t.sd_ratio <= 0.85                    then 'applied: the profile was too wide'
    when t.sd_ratio >= 1.15                    then 'applied: the profile was too narrow'
    else                                            'applied'
  end                                          as verdict,
  t.reason,
  t.computed_at
from derived_trajectory t
left join derived_climb_profile cp
  on cp.city_key = t.city_key and cp.local_hour = t.local_hour
order by t.applied desc, abs(coalesce(t.crps_gain, 0)) desc, t.city_key, t.local_hour;

comment on view v_trajectory_health is
  'Every fitted city-hour with a plain-language verdict. An hour reading "shadow" is a measurement, not a failure: it says the morning forecast still describes that hour better than the day so far does.';


-- --------------------------------------------------------------------------
-- WHAT THE ENGINE READS AT PRICING TIME, in one request for the whole roster.
--
-- probability_engine needs five things to price the rest of a day and they
-- live in four places: the city's local hour (cities.timezone), what the
-- thermometer says now and the maximum so far (v_city_running_max), what is
-- typically left to climb at this hour (derived_climb_profile), and whether
-- this city-hour earned the right to be used at all (v_trajectory_applied).
-- Assembling that per city in Python would be four round trips times
-- forty-eight; assembled here it is one.
--
-- THE HOUR IS THE CITY'S, NOT THE SERVER'S. A daily maximum market resolves
-- on the local day, and "how much is left to climb" is a statement about the
-- local clock - 15:00 in Tokyo and 15:00 UTC are different afternoons.
-- --------------------------------------------------------------------------
create or replace view v_city_trajectory_now as
-- THE HOUR OF THE READING, NOT OF THE CLOCK (plan v2 P3.3). The climb profile
-- says how much a day still rises after a given local hour; applying the hour
-- of now() to a reading taken ninety minutes earlier adds the wrong climb to
-- the wrong temperature. local_hour is now the reading's own hour, and the
-- reading and its time travel with the row so the engine can refuse a stale one.
with at_now as (
  select c.city_key,
         coalesce(c.timezone, 'UTC')                                          as tz,
         (now() at time zone coalesce(c.timezone, 'UTC'))::date               as local_date
  from cities c
  where coalesce(c.status, 'active') = 'active'
),
at_reading as (
  select a.city_key, a.tz, a.local_date,
         rm.running_max_c, rm.latest_temp_today_c, rm.readings_today, rm.timing_trustworthy,
         rm.latest_reading_at, rm.latest_reading_c,
         extract(hour from rm.latest_reading_at at time zone a.tz)::int      as reading_hour
  from at_now a
  left join v_city_running_max rm
         on rm.city_key = a.city_key and rm.local_date = a.local_date
)
select
  a.city_key,
  a.tz,
  a.local_date,
  a.reading_hour                                as local_hour,
  a.running_max_c,
  a.latest_temp_today_c,
  a.readings_today,
  a.timing_trustworthy,
  cp.typical_climb_left_c,
  cp.climb_left_sd_c,
  cp.pct_already_peaked,
  cp.n_days                                     as climb_n_days,
  t.sd_ratio,
  t.crps_gain,
  (t.city_key is not null)                      as trajectory_applied,
  -- Appended (plan v2 P3.3).
  a.latest_reading_at,
  a.latest_reading_c
from at_reading a
left join derived_climb_profile cp
       on cp.city_key = a.city_key and cp.local_hour = a.reading_hour
left join v_trajectory_applied t
       on t.city_key = a.city_key and t.local_hour = a.reading_hour;

comment on view v_city_trajectory_now is
  'Per active city, right now: its local date, the newest station reading and the local hour it was taken (plan v2 P3.3 - not the hour of now()), the maximum so far, what the climb profile says is still to come after that hour, and whether that city-hour has earned the right to price from it. One request for the whole roster.';
