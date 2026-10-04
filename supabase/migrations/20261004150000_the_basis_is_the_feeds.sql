-- ===========================================================================
-- THE BASIS IS COUNTED OVER THE FEED THE MAXIMUM CAME FROM (4 Oct 2026)
--
-- 20261004120000 made the running maximum the settlement feed's (IEM) when
-- today has any, but two things around it still counted every source. Both
-- were found by a review of #297, after it merged:
--
--   * running_max_basis counted today's readings over every source, so a city
--     with ONE routine report beside many NWS five-minute readings was labelled
--     'series' while its maximum rested on that one report - a floor. s5 and
--     S10's lock read the basis to refuse a floor. Live at 08:50Z on 4 Oct:
--     los_angeles, san_francisco and seattle, 1 IEM reading against 17-19 in
--     all, all three 'series'. Now the basis is counted over the settlement
--     readings when the maximum is theirs, in the view and the stored row alike
--     (tests/database/observation-health.cjs holds them equal).
--   * stored_max_below_latest called a live reading above the stored maximum
--     "arithmetically impossible" whatever the reading was. Beside settlement
--     readings a warmer NWS reading is expected, and a model's value has been
--     kept out of the maximum since P2.7. Live at 08:50Z: 8 cities flagged -
--     san_francisco, seattle and denver for an NWS reading, buenos_aires,
--     chongqing, karachi, panama_city and tokyo for a model value. Now only a
--     station's reading on a day the settlement feed has not reported counts.
--
-- The statements are sql/ad4_71's v_city_observation_health and
-- sql/ad4_live_weather_timing's function, verbatim
-- (tests/test_the_basis_is_the_feeds.py holds them equal), as CREATE OR
-- REPLACE with the same columns, applied only where they exist. Re-runnable.
-- ===========================================================================

do $mig$
begin
  if to_regclass('public.v_city_observation_health') is not null
     and to_regclass('public.v_city_today_readings') is not null then
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
feed as (
  select t.city_key,
         (select max(o.valid_at)
            from weather_observations o
           where o.city_key = t.city_key
             and o.temp_c is not null)                                  as newest_reading
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
  -- reading the maximum is built from can expose it: a station's, on a day the
  -- settlement feed has not reported (4 Oct). A model's value has been kept out
  -- of the maximum since P2.7, and beside settlement readings a warmer NWS
  -- reading is expected, so neither is "impossible".
  (lw.running_max_c is not null
     and lw.source_kind is not distinct from 'station'
     and s.settlement_max_today_c is null
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
left join live_weather lw  on lw.city_key = t.city_key;$oh$;
  end if;
  if to_regprocedure('public.refresh_live_weather_timing()') is not null then
    execute $fn$create or replace function public.refresh_live_weather_timing()
returns integer
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  n integer := 0;
begin
  with clock as (
    select c.city_key,
           coalesce(c.timezone, 'UTC') as tz,
           (now() at time zone coalesce(c.timezone, 'UTC')) as local_now
      from cities c
     where coalesce(c.status, 'active') = 'active'
  ),
  timing as (
    select t.city_key, t.local_hour, t.window_opens_hour, t.window_closes_hour,
           t.minutes_to_peak, t.rolling_over, t.direction
      from v_trade_timing t
  ),
  today as (
    select r.city_key, r.valid_at, r.temp_c, r.source
      from v_city_today_readings r
  ),
  -- THE SETTLEMENT FEED (4 Oct; ad4_71's v_city_running_max reads the same).
  -- The venue settles on the routine reports (source 'IEM'). A US city's NWS
  -- five-minute readings run warm of them, and their maximum was outside the
  -- venue's winning bucket on 84 of 297 US city-days (5 Sep - 2 Oct).
  settle as (
    select distinct on (city_key) city_key, temp_c as settle_max_c, valid_at as settle_max_at,
           count(*) over (partition by city_key)::int as settle_readings
      from today where source = 'IEM'
     order by city_key, temp_c desc, valid_at asc
  ),
  cnt as (
    select city_key, count(*)::int as readings_today from today group by city_key
  ),
  newest as (
    select distinct on (city_key) city_key, valid_at as latest_at, temp_c as latest_temp_c
      from today order by city_key, valid_at desc
  ),
  peak as (
    select distinct on (city_key) city_key, temp_c as series_max_c, valid_at as series_max_at
      from today order by city_key, temp_c desc, valid_at asc
  ),
  low as (
    select city_key, min(temp_c) as series_min_c from today group by city_key
  ),
  lag1 as (
    select distinct on (t.city_key) t.city_key, t.temp_c
      from today t join newest nw using (city_key)
     where t.valid_at <= nw.latest_at - interval '50 minutes'
     order by t.city_key, t.valid_at desc
  ),
  lag3 as (
    select distinct on (t.city_key) t.city_key, t.temp_c
      from today t join newest nw using (city_key)
     where t.valid_at <= nw.latest_at - interval '170 minutes'
     order by t.city_key, t.valid_at desc
  ),
  -- The row as it stands. Weighing the stored maximum and the live
  -- thermometer against today's series HERE, rather than in the SET clause,
  -- is what lets running_max_at name the reading the maximum actually came
  -- from instead of being coalesced away from it.
  prev as (
    select city_key, running_max_c, running_max_at, running_min_c, temp_c, observed_at,
           -- ONLY A STATION'S OWN READING may become part of a maximum (plan
           -- v2 P2.7). For 37 of 48 active cities live_weather.temp_c is
           -- Open-Meteo MODEL output (source_kind = 'model', n8n P1.5).
           -- Folded in here it became the stored maximum and then the pricing
           -- floor: on 23 Sep 10 cities' floors sat above everything their
           -- station had measured, Seoul's by 2.2 C (23.2 vs 21.0). The kept
           -- extreme goes too, because a model value once kept was carried to
           -- the end of the local day. The station series (v_city_today_
           -- readings) is recomputed on every run, so nothing real is lost.
           source_kind is not distinct from 'station' as live_is_station
      from live_weather
  ),
  calc as (
    select k.city_key,
           k.tz,
           k.local_now::date as local_date,
           tm.minutes_to_peak,
           tm.local_hour, tm.window_opens_hour, tm.window_closes_hour,
           coalesce(cn.readings_today, 0) as readings_today,
           p.series_max_c, p.series_max_at, l.series_min_c,
           st.settle_max_c, st.settle_max_at, st.settle_readings,
           nw.latest_temp_c,
           -- A stored extreme counts only while the reading it came from
           -- belongs to this city's current local day.
           case when pv.live_is_station and (pv.running_max_at at time zone k.tz)::date = k.local_now::date
                then pv.running_max_c end  as kept_max_c,
           case when pv.live_is_station and (pv.running_max_at at time zone k.tz)::date = k.local_now::date
                then pv.running_max_at end as kept_max_at,
           case when pv.live_is_station and (pv.running_max_at at time zone k.tz)::date = k.local_now::date
                then pv.running_min_c end  as kept_min_c,
           -- THE LIVE THERMOMETER, which nothing here used to read. n8n keeps
           -- it current for all 54 cities; the observation feed is about a day
           -- behind for 37 of them. Leaving it out is what produced a stored
           -- maximum BELOW the current temperature for 14 cities - by up to
           -- 12.5 C - which is arithmetically impossible and made bands the
           -- day had already cleared look unreachable.
           case when pv.live_is_station and (pv.observed_at at time zone k.tz)::date = k.local_now::date
                then pv.temp_c end         as live_temp_c,
           case when pv.live_is_station and (pv.observed_at at time zone k.tz)::date = k.local_now::date
                then pv.observed_at end    as live_at,
           case when l1.temp_c is null then null else round(nw.latest_temp_c - l1.temp_c, 2) end as temp_change_1h,
           case when l3.temp_c is null then null else round(nw.latest_temp_c - l3.temp_c, 2) end as temp_change_3h,
           tm.direction,
           -- The day is DECIDED when the maximum can no longer move: the peak
           -- window has closed and the reading has fallen away from the day's
           -- high, or it is late evening by the local clock whatever the trend.
           -- Measured against the SERIES, because "has it turned over" is a
           -- question about a sequence of readings; a city with no series has
           -- no timing row, so this is false there and s5 cannot fire.
           coalesce(
             (tm.local_hour >= 21)
             or (tm.local_hour > tm.window_closes_hour + 1
                 and (coalesce(tm.rolling_over, false)
                      or (nw.latest_temp_c is not null
                          and p.series_max_c - nw.latest_temp_c >= 1.0))),
             false) as decided
      from clock k
      left join timing tm on tm.city_key = k.city_key
      left join cnt cn    on cn.city_key = k.city_key
      left join peak p    on p.city_key = k.city_key
      left join settle st on st.city_key = k.city_key
      left join low l     on l.city_key = k.city_key
      left join newest nw on nw.city_key = k.city_key
      left join lag1 l1   on l1.city_key = k.city_key
      left join lag3 l3   on l3.city_key = k.city_key
      left join prev pv   on pv.city_key = k.city_key
  ),
  -- THE INVARIANT, in one place: a maximum is never below anything the
  -- settlement feed measured today; with no settlement reading today, never
  -- below anything measured today. A minimum is never above anything measured
  -- today. greatest()/least() ignore nulls, so a city with only one of the
  -- three still gets the one it has.
  final as (
    select c.*,
           case when c.settle_max_c is not null then c.settle_max_c
                else greatest(c.series_max_c, c.kept_max_c, c.live_temp_c) end as max_c,
           least(c.series_min_c, c.kept_min_c, c.live_temp_c)    as min_c
      from calc c
  )
  update live_weather lw
     set minutes_to_peak   = f.minutes_to_peak,
         peak_window_state = case
             when f.decided then 'AFTER'
             when f.local_hour is null then null
             when f.local_hour < f.window_opens_hour then 'BEFORE'
             when f.local_hour <= f.window_closes_hour then 'INSIDE'
             else 'AFTER' end,
         day_decided       = f.decided,
         running_max_c     = f.max_c,
         -- Name the reading it came from. A measured series wins a tie: it is
         -- the better provenance even at the same temperature.
         running_max_at    = case
             when f.max_c is null then null
             when f.settle_max_c is not null then f.settle_max_at
             when f.series_max_c is not null and f.series_max_c = f.max_c then f.series_max_at
             when f.kept_max_c   is not null and f.kept_max_c   = f.max_c then f.kept_max_at
             else f.live_at end,
         running_min_c     = f.min_c,
         readings_today    = f.readings_today,
         -- The provenance travels with the number, so a consumer that needs a
         -- settled maximum (s5) can refuse a floor instead of trading on it.
         -- Counted over the feed the maximum came from (4 Oct), as ad4_71's
         -- view does: one routine report is a floor whatever else arrived.
         running_max_basis = public.ad4_running_max_basis(
                               case when f.settle_max_c is not null then f.settle_readings
                                    else f.readings_today end,
                               coalesce(f.live_temp_c, f.latest_temp_c)),
         temp_change_1h    = f.temp_change_1h,
         temp_change_3h    = f.temp_change_3h,
         trend             = f.direction,
         local_date        = f.local_date
    from final f
   where f.city_key = lw.city_key;
  get diagnostics n = row_count;
  return n;
end;
$$;$fn$;
  end if;
end
$mig$;
