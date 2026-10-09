-- ===========================================================================
-- THE LIVE PAGE'S FEED AGE, HELD OR CACHED (Fresh Supabase, part 2b, 9 Oct).
--
-- 20261009090000 cuts weather_observations to three days. v_city_observation_
-- health read each city's newest reading from the readings alone, so a city
-- silent longer than that would show no age at all: 'never' on the Live page,
-- sorted as fresh. It now falls back to the newest day's last reading in
-- derived_station_day_sources, kept every night by refresh_city_day_hours,
-- which prune_observations refuses to outrun.
--
-- Measured 9 Oct before the change: the same 47 rows both ways (EXCEPT ALL,
-- one statement), no active city without a cached day. The statement is
-- sql/ad4_71's, verbatim (tests/test_the_weather_tables_keep_three_days.py
-- holds them equal), as CREATE OR REPLACE with the same columns, applied only
-- where the view exists. The view is not security_invoker: it reads the
-- private cache with its owner's rights, as it reads the readings. Nothing is
-- deleted. Re-runnable.
-- ===========================================================================

do $mig$
begin
  if to_regclass('public.v_city_observation_health') is not null
     and to_regclass('public.v_city_today_readings') is not null
     and to_regclass('public.derived_station_day_sources') is not null then
    execute $oh$create or replace view public.v_city_observation_health as
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
-- The newest reading of today's series, and when it was taken (plan v2 P3.3):
-- what the trajectory stands on, and how old it is.
newest as (
  select distinct on (r.city_key) r.city_key,
         r.valid_at                                                     as latest_reading_at,
         r.temp_c                                                       as latest_reading_c
  from v_city_today_readings r
  order by r.city_key, r.valid_at desc
),
-- HELD OR CACHED (Fresh Supabase, part 2b, 9 Oct). The readings keep three
-- days, so a city silent longer had none held and its age came out null:
-- 'never' on the Live page, sorted as fresh. refresh_city_day_hours keeps
-- every day's last reading per source in derived_station_day_sources (ad4_29)
-- and prune_observations refuses a day it has not kept, so the newest of
-- those is the newest reading once the readings hold none.
feed as (
  select t.city_key,
         coalesce((select max(o.valid_at)
                     from weather_observations o
                    where o.city_key = t.city_key
                      and o.temp_c is not null),
                  (select max(k.last_reading_at)
                     from derived_station_day_sources k
                    where k.city_key = t.city_key))                     as newest_reading
  from tz t
),
-- THE SETTLEMENT FEED (4 Oct). The venue settles on the station's routine
-- reports, which is what source 'IEM' holds. The US cities also carry NWS
-- five-minute readings (whole C, about 270 a day), whose maximum runs warm of
-- the reports the venue reads: on 5 Sep - 2 Oct the IEM daily maximum was in
-- the venue's winning bucket on 286 of 297 US city-days and the maximum over
-- every source on 213. Houston on 1 Oct: IEM 31.67 C (89 F, the winner's
-- bucket 88-90 F), NWS 33 C at 20:25Z.
settled as (
  select r.city_key,
         count(*)                                                       as settlement_readings_today,
         max(r.temp_c)                                                  as settlement_max_today_c
  from v_city_today_readings r
  where r.source = 'IEM'
  group by r.city_key
),
-- ...and the part of it the refresh has had time to take in: readings known
-- (observed_at, when we received them) at least 15 minutes ago, since pg_cron
-- refreshes every 10 and a new reading does not trigger one. A stored maximum
-- below these is stale, not merely behind.
settled_due as (
  select o.city_key,
         max(o.temp_c)                                                  as settlement_max_due_c
  from weather_observations o
  join tz t on t.city_key = o.city_key
  where o.source = 'IEM'
    and o.temp_c is not null
    and o.valid_at > now() - interval '36 hours'
    and (o.valid_at at time zone t.timezone)::date = t.local_date
    and o.observed_at <= now() - interval '15 minutes'
  group by o.city_key
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
  --
  -- AND ONLY A STATION'S (plan v2 P2.7). For 37 of 48 active cities the live
  -- row is Open-Meteo model output; as a floor it put Seoul's at 23.2 C while
  -- its station had measured 21.0 (23 Sep). latest_temp_c above still shows it,
  -- labelled by live_source_kind; no maximum may be built from it.
  case when lw.source_kind is not distinct from 'station'
        and (lw.observed_at at time zone t.timezone)::date = t.local_date
       then lw.temp_c end                                               as latest_temp_today_c,

  d.observed_max_today_c,
  case when lw.source_kind is not distinct from 'station'
       then lw.running_max_c end                                        as stored_running_max_c,

  -- ---- can the running maximum be believed? -----------------------------
  -- Counted over the feed the maximum is taken from (4 Oct): when today has
  -- settlement readings the maximum is theirs, so ONE routine report beside
  -- sixty NWS readings is a floor, not a series - s5 must not lock on it.
  public.ad4_running_max_basis(
    case when s.settlement_max_today_c is not null then s.settlement_readings_today
         else coalesce(d.readings_today, 0) end::integer,
    case when lw.source_kind is not distinct from 'station'
          and (lw.observed_at at time zone t.timezone)::date = t.local_date
         then lw.temp_c end)                                            as running_max_basis,

  -- The stored field disagreeing with a reading from the SAME day is its own
  -- fault and worth naming, because it is silent everywhere else. Only a
  -- reading the maximum is built from can expose it (4 Oct): a settlement
  -- reading the refresh has had time to take in, or, on a day the settlement
  -- feed has not reported, a station's live reading. A model's value has been
  -- kept out of the maximum since P2.7, and beside settlement readings a
  -- warmer NWS reading is expected, so neither is "impossible".
  ((sd.settlement_max_due_c is not null
      and (lw.running_max_c is null or sd.settlement_max_due_c > lw.running_max_c))
   or (lw.running_max_c is not null
       and lw.source_kind is not distinct from 'station'
       and s.settlement_max_today_c is null
       and (lw.observed_at at time zone t.timezone)::date = t.local_date
       and lw.temp_c is not null
       and lw.temp_c > lw.running_max_c))                               as stored_max_below_latest,

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
    when coalesce(d.readings_today, 0) = 0
     and lw.source_kind is distinct from 'station' then
      format('The observation feed has nothing for %s yet, and the live value (%s C) is model '
             'output, not a measurement - nothing is known about today''s maximum.',
             t.local_date, lw.temp_c)
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
  end                                                                   as note,
  -- Appended (plan v2 P3.3).
  n.latest_reading_at,
  n.latest_reading_c,
  -- Appended (4 Oct): today's maximum from the settlement feed alone.
  s.settlement_max_today_c,
  coalesce(s.settlement_readings_today, 0)                              as settlement_readings_today
from tz t
left join newest n         on n.city_key  = t.city_key
left join today d          on d.city_key  = t.city_key
left join feed f           on f.city_key  = t.city_key
left join settled s        on s.city_key  = t.city_key
left join settled_due sd   on sd.city_key = t.city_key
left join live_weather lw  on lw.city_key = t.city_key;$oh$;
  end if;
end $mig$;
