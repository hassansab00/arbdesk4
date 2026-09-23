-- ===========================================================================
-- ad4_87_market_settlement_gaps.sql - A DAY THAT ENDED WITHOUT A TEMPERATURE.
--
-- Safe to run any time. One view. Changes no data.
--
-- Plan v2 P2.4. 20260923140000_a_market_whose_day_ended_is_closed.sql closes
-- every market whose local day has ended. Hassan's rule for the other half,
-- 23 Sep: "if the temp wasn't settled, then there's a real issue". This view
-- is that list: every market whose local day ended more than GRACE ago and
-- for which nothing we hold says what the temperature was.
--
-- "Settled temperature" is any of the three places we keep one:
--   v_station_day_max.max_c_hourly    the primary source, live rows
--   fact_band_outcome.observed_max_c   banked per band, survives the archive
--   fact_forecast_outcome.observed_max_c banked per forecast, likewise
--
-- A day before the city's collection start (the earliest day any of the three
-- holds for that city) is not a gap: we never collected it, so its absence
-- says nothing about the pipeline. Measured 23 Sep: 164 ended markets from
-- 2025-01 to 2026-05 have no temperature and fall before their city's start.
--
-- ACTIVE CITIES ONLY. This is a list to act on, and a retired city is not
-- collected on purpose. Measured 23 Sep: before this filter the list held 34
-- markets, every one in a retired city - Jinan (21 Aug - 21 Sep; its last
-- observation is 24 Aug), Hong Kong (6 - 11 Sep; no observation ever) and
-- Taipei (22 Sep). The markets themselves are still closed by the migration,
-- which applies to every city.
--
-- Two more things are listed because they are also "not settled":
--   venue_disputed    v_venue_market_resolution found evidence naming more
--                     than one winning token for a band
--   winner_conflict   markets.winning_band_id is set and is not the band the
--                     venue confirmed
--
-- A market the venue has simply not confirmed yet is NOT listed. That is our
-- evidence collection's backlog, not a missing settlement: on 23 Sep the
-- median gap between a confirmed market's local day end and its evidence
-- capture was 7 days (851 markets). venue_state is on every row regardless.
--
-- GRACE is 3 hours: the observation ingest is hourly, so the day's last
-- report has had three chances to arrive. A choice, not a measurement.
--
-- RUN ORDER: after ad4_82_settlement_agreement.sql (v_station_day_max) and
-- 20260923140000 (market_day_ended).
-- ===========================================================================

create or replace view v_market_settlement_gaps as
with ended as (
  select m.market_id, m.city_key, m.resolution_date, m.event_slug, m.closed,
         m.closed_reason, m.winning_band_id,
         ((m.resolution_date + 1)::timestamp at time zone c.timezone) as day_ended_at
  from markets m
  join cities c on c.city_key = m.city_key
  where market_day_ended(m.city_key, m.resolution_date)
    and c.status = 'active'
),
collection_start as (
  select city_key, min(d) as first_day
  from (
    select o.city_key, min((o.valid_at at time zone c.timezone)::date) as d
      from weather_observations o join cities c on c.city_key = o.city_key
     group by 1
    union all
    select city_key, min(for_date) from fact_band_outcome
     where observed_max_c is not null group by 1
    union all
    select city_key, min(for_date) from fact_forecast_outcome
     where observed_max_c is not null group by 1
  ) x
  group by 1
),
judged as (
  select e.*,
         s.max_c_hourly                                   as station_max_c,
         (select max(f.observed_max_c) from fact_band_outcome f
           where f.city_key = e.city_key and f.for_date = e.resolution_date) as banked_band_max_c,
         (select max(o.observed_max_c) from fact_forecast_outcome o
           where o.city_key = e.city_key and o.for_date = e.resolution_date) as banked_forecast_max_c,
         cs.first_day                                     as collection_start,
         r.resolution_state                               as venue_state,
         r.winning_band_id                                as venue_winning_band_id
  from ended e
  left join collection_start cs on cs.city_key = e.city_key
  left join v_station_day_max s on s.city_key = e.city_key and s.for_date = e.resolution_date
  left join v_venue_market_resolution r on r.market_id = e.market_id
)
select market_id, city_key, resolution_date, event_slug, closed, closed_reason,
       day_ended_at, now() - day_ended_at as since_day_end,
       station_max_c, banked_band_max_c, banked_forecast_max_c, collection_start,
       venue_state, winning_band_id, venue_winning_band_id,
       issue
from (
  select j.*,
         case
           when coalesce(station_max_c, banked_band_max_c, banked_forecast_max_c) is null
                and resolution_date >= collection_start
                and now() - day_ended_at > interval '3 hours'
             then 'no_temperature'
           when venue_state = 'disputed' then 'venue_disputed'
           when winning_band_id is not null and venue_winning_band_id is not null
                and winning_band_id <> venue_winning_band_id
             then 'winner_conflict'
         end as issue
  from judged j
) g
where issue is not null;

comment on view v_market_settlement_gaps is
  'Plan v2 P2.4: ended markets that are not settled - no temperature anywhere we keep one (after a 3 h grace, from the city''s collection start), a disputed venue settlement, or a stored winner that disagrees with the venue. Empty is healthy.';

grant select on v_market_settlement_gaps to anon, authenticated, service_role;
