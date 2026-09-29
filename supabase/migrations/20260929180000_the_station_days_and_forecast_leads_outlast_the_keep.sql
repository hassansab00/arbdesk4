-- ===========================================================================
-- THE STATION'S DAYS AND THE FORECAST STANDING AT EACH LEAD OUTLAST THE
-- WEATHER TABLES' KEEP (plan v2 P1.6 phase 2, step 5 part (a), 29 Sep)
--
-- Hassan, 29 Sep: "do step 6 ten step 5". Step 5 lowers the weather tables'
-- keep to about 30 days. After step 6, four SQL readers still look further
-- back than that (pg_depend and the function sources, 29 Sep):
--
--   v_station_day_max      every day weather_observations holds. Read by the
--                          settlement agreement behind each city's
--                          observation trust (refresh_observation_trust,
--                          nightly), the settlement-gap report and databank's
--                          late proofs.
--   v_forecast_convergence, v_forecast_convergence_all
--                          45 days of weather_forecasts: the Predictive page
--                          and the daily research capture.
--   v_backtest_window, backtest_readiness
--                          where observations and forecasts begin, and whether
--                          a backtest window has them.
--
-- 1. derived_station_day_sources: each city's local day per source, written
--    by refresh_city_day_hours every night while the day is whole, never
--    rewritten once the prune has cut into it (refresh_feature_cache's rule).
--    v_station_day_max reads the readings for whole days and this before
--    them, and still picks the primary source when it is read.
-- 2. derived_forecast_latest and freeze_forecast_latest: the newest run per
--    city, day, model and lead for every day that has passed. v_forecast_latest
--    serves the table for the days it holds and the frozen rows before; both
--    convergence views read it. v_forecast_convergence keeps security_invoker
--    (a CREATE OR REPLACE without it resets it - checked on Postgres 17.5).
--    v_forecast_convergence_all_live is replaced only where it exists
--    (sql/ad4_89 builds it from sql/ad4_62).
-- 3. v_backtest_window and backtest_readiness read both caches too.
-- 4. prune_observations and prune_forecasts refuse to delete what these have
--    not kept. Their bodies are the live ones (20260929160000) with that one
--    guard added to each.
--
-- The bodies are the ones in sql/ad4_29_retention.sql, ad4_31_predictive.sql,
-- ad4_42_backtest.sql, ad4_62_settled_history_ungated.sql, ad4_63_prune_
-- forecasts.sql, ad4_82_settlement_agreement.sql and ad4_97_evidence_cache.sql.
-- Nothing is deleted and nothing changes what a reader returns: the caches
-- fill on the next refresh. Re-runnable.
-- ===========================================================================
create table if not exists derived_station_day_sources (
  city_key        text        not null,
  obs_date        date        not null,   -- the city's local date
  source          text        not null,   -- weather_observations.source, '' where it is null
  max_c           numeric,
  max_f           numeric,
  n_readings      bigint      not null,   -- readings with a temperature
  last_reading_at timestamptz not null,
  station         text,                   -- min(station), as v_station_day_max takes it
  computed_at     timestamptz not null default now(),
  primary key (city_key, obs_date, source)
);

comment on table derived_station_day_sources is
  'Per city, local day and source: the maximum in both units, the readings, the last reading and the station, cached from weather_observations while the day is whole and never rewritten once the prune has cut into it. What v_station_day_max reads for the days the readings no longer hold (plan v2 P1.6 phase 2).';

alter table derived_station_day_sources enable row level security;
revoke all on derived_station_day_sources from public, anon, authenticated;
grant select, insert, update, delete on derived_station_day_sources to service_role;

create table if not exists derived_forecast_latest (
  city_key       text        not null,
  for_date       date        not null,
  model          text        not null,
  lead_days      int         not null,
  forecast_max_c numeric     not null,
  run_at         timestamptz,
  frozen_at      timestamptz not null default now(),
  primary key (city_key, for_date, model, lead_days)
);

comment on table derived_forecast_latest is
  'The newest forecast with a maximum per city, day, model and lead, frozen from weather_forecasts each night for the days that have passed. What v_forecast_latest serves for the days the forecast table no longer holds (plan v2 P1.6 phase 2). Written by freeze_forecast_latest.';

alter table derived_forecast_latest enable row level security;
revoke all on derived_forecast_latest from public, anon, authenticated;
grant select, insert, update, delete on derived_forecast_latest to service_role;

create or replace view v_forecast_latest as
-- The forecast STANDING at that lead, which is the one that could have been
-- acted on. A later re-run of the same lead is hindsight. A filter on
-- for_date reaches the index: it is a DISTINCT ON key, so Postgres applies it
-- before the DISTINCT ON, and to both halves of the UNION.
select l.city_key, l.for_date, l.model, l.lead_days, l.forecast_max_c, l.run_at
  from (select distinct on (f.city_key, f.for_date, f.model, f.lead_days)
               f.city_key, f.for_date, f.model, f.lead_days, f.forecast_max_c, f.run_at
          from weather_forecasts f
         where f.forecast_max_c is not null
         order by f.city_key, f.for_date, f.model, f.lead_days, f.run_at desc) l
union all
select d.city_key, d.for_date, d.model, d.lead_days, d.forecast_max_c, d.run_at
  from derived_forecast_latest d
 where d.for_date < coalesce((select min(w.for_date) from weather_forecasts w), 'infinity'::date);

comment on view v_forecast_latest is
  'The newest forecast with a maximum per city, day, model and lead: from weather_forecasts for the days it holds, from derived_forecast_latest for the days before (plan v2 P1.6 phase 2). Both convergence views read it.';

grant select on v_forecast_latest to anon, authenticated, service_role;

create or replace view v_station_day_max as
with
-- THE FIRST WHOLE DAY OF EACH CITY (plan v2 P1.6 phase 2, step 5). The prune
-- cuts every city at one instant; a local day that began before the oldest
-- reading held is cut. From that day on the readings are whole and read
-- directly; before it each source's day comes from derived_station_day_sources
-- (sql/ad4_29), written every night while it was whole - the rule
-- v_trajectory_evidence and refresh_feature_cache follow. A source's day
-- before it that was never cached is read from the readings: the prune
-- deletes no reading of a day it has not cached, so they are all there.
held as (
  select min(valid_at) as oldest from weather_observations
),
whole_from as (
  select c.city_key,
         coalesce(case when ((h.oldest at time zone coalesce(c.timezone, 'UTC'))::date::timestamp
                              at time zone coalesce(c.timezone, 'UTC')) < h.oldest
                       then (h.oldest at time zone coalesce(c.timezone, 'UTC'))::date + 1
                       else (h.oldest at time zone coalesce(c.timezone, 'UTC'))::date end,
                  'infinity'::date) as first_whole
  from cities c cross join held h
),
-- Each source's day, so the primary source is chosen below, when the view is
-- read, for the cached days as for the rest.
by_source as (
  select o.city_key,
         (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date  as for_date,
         o.source,
         max(o.temp_c)    as max_c,
         max(o.temp_f)    as max_f,
         count(*)         as n_readings,
         max(o.valid_at)  as last_reading_at,
         min(o.station)   as station
  from weather_observations o
  join cities c on c.city_key = o.city_key
  join whole_from f on f.city_key = o.city_key
  where o.temp_c is not null
    and ((o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date >= f.first_whole
         or not exists (
              select 1 from derived_station_day_sources k
               where k.city_key = o.city_key
                 and k.obs_date = (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date
                 and k.source = coalesce(o.source, '')))
  group by 1, 2, 3
  union all
  select d.city_key, d.obs_date, d.source, d.max_c, d.max_f, d.n_readings, d.last_reading_at, d.station
  from derived_station_day_sources d
  join whole_from f on f.city_key = d.city_key
  where d.obs_date < f.first_whole
)
select
  city_key,
  for_date,
  max(max_c)                                                   as max_c,
  max(max_f)                                                   as max_f,
  sum(n_readings)::bigint                                      as n_readings,
  max(last_reading_at)                                         as last_reading_at,
  min(station) filter (where source = obs_primary_source())    as station,
  -- THE PRIMARY SOURCE ONLY (plan v2 P2.2), which carries the reports the
  -- station itself filed and that the venue settles on. This was a minute
  -- window around report_minute over EVERY source, which let the NWS
  -- five-minute feed in (Dallas 13 Sep: 100.4F from KDAL at :55 against a
  -- routine 99.0F). 20260922180000_the_venue_reads_the_hourly_column.sql is
  -- why the five-minute feed must stay out; the window was the wrong way to
  -- keep it out, because it also drops the half-hourly and special reports.
  max(max_c) filter (where source = obs_primary_source())      as max_c_hourly,
  max(max_f) filter (where source = obs_primary_source())      as max_f_hourly,
  coalesce(sum(n_readings) filter (where source = obs_primary_source()), 0)::bigint as n_hourly
from by_source
group by 1, 2;

comment on view v_station_day_max is
  'The station feed''s daily maximum per city-day, in both units. max_f is the station''s native reading; max_c is the conversion. Comparing an F market''s bands against max_f rather than against a Celsius round trip is worth 2.9 points of settlement agreement. Whole days from weather_observations, the days before from derived_station_day_sources (plan v2 P1.6 phase 2).';

-- The live body (20260913100000_phase2a_verified_outcome_truth.sql, which
-- sql/ad4_31 predates): observed from v_verified_fact_forecast_outcome.
create or replace view public.v_forecast_convergence
with (security_invoker = true)
as
with latest as (
  -- The forecast standing at each lead, past the forecast table's keep.
  select city_key, for_date, model, lead_days, forecast_max_c, run_at
  from public.v_forecast_latest
  where for_date >= current_date - 45
    and for_date <= current_date + 16
), observed as (
  select city_key, for_date, max(observed_max_c) as observed_max_c
  from public.v_verified_fact_forecast_outcome
  where for_date >= current_date - 45
  group by city_key, for_date
)
select l.city_key, l.for_date, l.model, l.lead_days, l.forecast_max_c, l.run_at,
       o.observed_max_c,
       case when o.observed_max_c is not null
            then round(o.observed_max_c - l.forecast_max_c, 2) end as error_c,
       l.for_date < current_date as is_past,
       o.observed_max_c is not null as is_settled
from latest l
left join observed o on o.city_key = l.city_key and o.for_date = l.for_date;

-- What the Predictive page's stored rows are built from (sql/ad4_89 wraps
-- sql/ad4_62's v_forecast_convergence_all as _live -> mv_ -> the view). Only
-- where the page cache exists; its columns are unchanged, so the
-- materialized view over it stands.
do $mig$
begin
  if to_regclass('public.v_forecast_convergence_all_live') is not null then
    execute $v$
create or replace view public.v_forecast_convergence_all_live as
with latest as (
  -- The forecast standing at each lead, past the forecast table's keep
  -- (v_forecast_latest, sql/ad4_31; plan v2 P1.6 phase 2).
  select city_key, for_date, model, lead_days, forecast_max_c, run_at
    from v_forecast_latest
   where for_date >= current_date - 45
     and for_date <= current_date + 16
),
-- ONE VERIFICATION PER CITY-DAY VALUE, NOT PER FORECAST ROW. fact_forecast_
-- outcome holds a row per model and lead for every city-day - 11,325 in the
-- 45-day window - and the corroboration test below ran once for each of them,
-- though it depends only on (city, day, observed value): 1,111 distinct
-- triples. It is the "Actual against predicted" panel's query, and on
-- 2026-09-22 it was one of the 57014s. Deduplicated first, the same answer
-- costs 8,701 buffers instead of 44,066: max() over the distinct values is
-- the max over all rows, and bool_or() of a test that depends only on the
-- triple is the same over the triples as over the rows. Verified identical,
-- 24,213 rows, before it was applied.
observed as (
  select g.city_key, g.for_date,
         max(g.observed_max_c) as observed_max_c,
         bool_or(exists (select 1 from v_verified_weather_outcomes w
                          where w.city_key = g.city_key and w.for_date = g.for_date
                            and abs(g.observed_max_c - w.observed_max_c) <= 0.01)) as verified
    from (select distinct f.city_key, f.for_date, f.observed_max_c
            from fact_forecast_outcome f
           where f.for_date >= current_date - 45) g
   group by g.city_key, g.for_date
)
select l.city_key, l.for_date, l.model, l.lead_days, l.forecast_max_c, l.run_at,
       o.observed_max_c,
       case when o.observed_max_c is not null
            then round(o.observed_max_c - l.forecast_max_c, 2) end as error_c,
       l.for_date < current_date        as is_past,
       o.observed_max_c is not null     as is_settled,
       coalesce(o.verified, false)      as verified
from latest l
left join observed o on o.city_key = l.city_key and o.for_date = l.for_date;
$v$;
  end if;
end $mig$;

create or replace view v_backtest_window as
-- NO FULL COUNTS. This counted every row of book_snapshots (192,000) and
-- weather_observations (146,000) to draw one line of text on the Backtest
-- page: 3.8 s cold on 22 Sep, and nothing on the page needs the exact number.
-- The dates are exact - min() and max() are one index probe each - and
-- emptiness is decided from them, which is exact too, because observed_at,
-- valid_at and resolution_date are all NOT NULL. The counts follow the rule
-- ad4_39 and ad4_51 use everywhere else: exact up to 5,000 rows, the planner's
-- estimate above it, with *_estimated saying which (appended at the end so
-- `create or replace` can extend a live view).
with b as (
  select (select min(observed_at) from book_snapshots)::date as book_from,
         (select max(observed_at) from book_snapshots)::date as book_to,
         (select case when coalesce(c.reltuples, -1) > 5000
                      then greatest(c.reltuples, 0)::bigint
                      else (select count(*) from book_snapshots) end
            from pg_class c where c.oid = 'public.book_snapshots'::regclass)::bigint as n_books,
         (select coalesce(c.reltuples, -1) > 5000
            from pg_class c where c.oid = 'public.book_snapshots'::regclass) as n_books_estimated
),
-- THE STATION'S DAYS PAST THE READINGS' KEEP (plan v2 P1.6 phase 2, step 5).
-- The readings keep about 30 days; derived_station_day_sources (ad4_29) keeps
-- every day they held, by the city's local date. The first day either holds
-- is where observations begin. The count stays the table's rows.
o as (
  select least((select min(valid_at) from weather_observations)::date,
               (select min(obs_date) from derived_station_day_sources))      as obs_from,
         (select max(valid_at) from weather_observations)::date as obs_to,
         (select case when coalesce(c.reltuples, -1) > 5000
                      then greatest(c.reltuples, 0)::bigint
                      else (select count(*) from weather_observations) end
            from pg_class c where c.oid = 'public.weather_observations'::regclass)::bigint as n_obs,
         (select coalesce(c.reltuples, -1) > 5000
            from pg_class c where c.oid = 'public.weather_observations'::regclass) as n_obs_estimated
),
m as (
  select min(resolution_date) as mkt_from, max(resolution_date) as mkt_to,
         count(*)::bigint as n_markets
    from markets
)
select
  b.book_from, b.book_to, b.n_books,
  o.obs_from,  o.obs_to,  o.n_obs,
  m.mkt_from,  m.mkt_to,  m.n_markets,
  greatest(b.book_from, o.obs_from, m.mkt_from)          as usable_from,
  least(coalesce(b.book_to, current_date),
        coalesce(o.obs_to, current_date),
        coalesce(m.mkt_to, current_date))                as usable_to,
  case
    when b.book_from is null
      then 'No book snapshots at all. Strategy profitability cannot be backtested until P0.3 has run - Polymarket publishes no depth history, so the archive starts the day you first collected it.'
    when m.n_markets = 0
      then 'No markets on record, so there is nothing to backtest against. P0.2 fills them.'
    when o.obs_from is null
      then 'No observations on record, so no day can be scored. P1.2 or the Observations action fills them.'
    when greatest(b.book_from, o.obs_from, m.mkt_from)
       > least(b.book_to, o.obs_to, m.mkt_to)
      then 'The market, observation and book archives do not overlap on a single day yet.'
    else null
  end                                                    as blocked_because,
  b.n_books_estimated,
  o.n_obs_estimated
from b, o, m;

comment on view v_backtest_window is
  'The widest date range that could produce trades, and the first reason it could not. Bounded by the book archive: Polymarket publishes no depth history, so strategy profitability cannot be tested earlier than the day P0.3 first ran.';

create or replace function backtest_readiness(
  p_start date,
  p_end   date,
  p_cities text[] default null
) returns jsonb
language plpgsql stable security definer
-- Pinned as it is live (29 Sep); a plain CREATE OR REPLACE would drop it.
set search_path = public, pg_temp as $ad4$
declare
  v_markets     int;
  v_with_bands  int;
  v_with_obs    int;
  v_with_fc     int;
  v_with_book   int;
  v_days        int := greatest(1, (p_end - p_start) + 1);
  v_blocked     text;
begin
  if p_start is null or p_end is null or p_end < p_start then
    return jsonb_build_object('ok', false,
      'blocked_because', 'The end date is before the start date.');
  end if;

  with mk as (
    select m.market_id, m.city_key, m.resolution_date
      from markets m
     where m.resolution_date between p_start and p_end
       and (p_cities is null or cardinality(p_cities) = 0 or m.city_key = any(p_cities))
  ),
  scored as (
    select
      mk.*,
      exists (select 1 from bands b where b.market_id = mk.market_id)          as has_bands,
      -- The readings for the days they hold; the station's cached day
      -- (ad4_29) and the frozen forecasts (ad4_31) for the days before
      -- (plan v2 P1.6 phase 2, step 5).
      (exists (select 1 from weather_observations o
                where o.city_key = mk.city_key
                  and o.valid_at::date = mk.resolution_date)
       or exists (select 1 from derived_station_day_sources s
                   where s.city_key = mk.city_key
                     and s.obs_date = mk.resolution_date))                     as has_obs,
      (exists (select 1 from weather_forecasts f
                where f.city_key = mk.city_key
                  and f.for_date = mk.resolution_date)
       or exists (select 1 from derived_forecast_latest l
                   where l.city_key = mk.city_key
                     and l.for_date = mk.resolution_date))                     as has_fc,
      exists (select 1 from book_snapshots s
                join bands b2 on b2.band_id = s.band_id
               where b2.market_id = mk.market_id
                 and s.observed_at::date <= mk.resolution_date)                as has_book
    from mk
  )
  select count(*),
         count(*) filter (where has_bands),
         count(*) filter (where has_bands and has_obs),
         count(*) filter (where has_bands and has_obs and has_fc),
         count(*) filter (where has_bands and has_obs and has_fc and has_book)
    into v_markets, v_with_bands, v_with_obs, v_with_fc, v_with_book
    from scored;

  -- THE FIRST THING THAT WOULD MAKE IT EMPTY, in the order runner.py hits it.
  v_blocked := case
    when v_markets = 0
      then format('No market resolves between %s and %s%s. Pick a window the desk actually has markets for.',
                  p_start, p_end,
                  case when p_cities is null or cardinality(p_cities) = 0 then ''
                       else format(' in %s', array_to_string(p_cities, ', ')) end)
    when v_with_bands = 0
      then format('%s market(s) in the window and none of them has buckets. P0.2 writes markets and bands together, so this is a partial discovery run.', v_markets)
    when v_with_obs = 0
      then format('%s market-day(s) with buckets, none with an observation of what the day actually did. Without that a trade cannot be scored.', v_with_bands)
    when v_with_fc = 0
      then format('%s scoreable day(s), none with a forecast on record. There is nothing for a strategy to have acted on.', v_with_obs)
    when v_with_book = 0
      then format('%s day(s) have a forecast and an outcome, none has a book snapshot. Every entry price the simulation pays comes from that book, so it would take no trades. The book archive starts the day P0.3 first ran.', v_with_fc)
    else null
  end;

  return jsonb_build_object(
    'ok',              v_blocked is null,
    'days',            v_days,
    'markets',         v_markets,
    'with_bands',      v_with_bands,
    'with_observation', v_with_obs,
    'with_forecast',   v_with_fc,
    'simulatable',     v_with_book,
    'blocked_because', v_blocked,
    'summary',
      case when v_blocked is not null then v_blocked
           else format('%s of %s market-day(s) in this window have everything the simulation needs. That is what it will evaluate.',
                       v_with_book, v_markets) end
  );
end;
$ad4$;

comment on function backtest_readiness(date, date, text[]) is
  'Counts, for the exact window about to be queued, how many market-days have buckets, an observation, a forecast and a book snapshot - and returns the first thing that would make the run come back empty. Called as the dates change so the answer arrives before the run, not after it.';

grant select on v_backtest_window to anon, authenticated, service_role;
grant execute on function backtest_readiness(date, date, text[]) to anon, authenticated, service_role;

create or replace function public.refresh_city_day_hours(p_city text default null)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
  t0 timestamptz := clock_timestamp();
  v_oldest  timestamptz;
  v_city    text;
  v_tz      text;
  v_first   date;
  v_n       int;
  v_written int := 0;
  v_station int := 0;
  v_cities  int := 0;
begin
  select min(valid_at) into v_oldest from weather_observations;
  if v_oldest is null then
    return jsonb_build_object('ok', true, 'city', p_city, 'days_written', 0,
                              'note', 'weather_observations holds no readings');
  end if;

  for v_city, v_tz in
    select city_key, coalesce(timezone, 'UTC') from cities
     where p_city is null or city_key = p_city
     order by city_key
  loop
    -- The first whole local day: the one the oldest reading held falls on,
    -- unless it began before that reading (refresh_feature_cache's rule).
    v_first := (v_oldest at time zone v_tz)::date;
    if (v_first::timestamp at time zone v_tz) < v_oldest then
      v_first := v_first + 1;
    end if;

    -- Every local day the readings hold, as 24 slots of hourly maxima. A cut
    -- day is inserted when it was never cached and never updated.
    insert into derived_city_day_hours (city_key, obs_date, temp_c, n_hours, computed_at)
    select v_city, x.d, array_agg(h.temp_c order by g.slot), count(h.temp_c)::int, now()
      from (select distinct (o.valid_at at time zone v_tz)::date as d
              from weather_observations o
             where o.city_key = v_city and o.temp_c is not null) x
      cross join generate_series(0, 23) as g(slot)
      left join (select (o.valid_at at time zone v_tz)::date as d,
                        extract(hour from o.valid_at at time zone v_tz)::int as hr,
                        max(o.temp_c) as temp_c
                   from weather_observations o
                  where o.city_key = v_city and o.temp_c is not null
                  group by 1, 2) h
        on h.d = x.d and h.hr = g.slot
     group by x.d
    on conflict (city_key, obs_date) do update
       set temp_c = excluded.temp_c, n_hours = excluded.n_hours, computed_at = now()
     where excluded.obs_date >= v_first;
    get diagnostics v_n = row_count;
    v_written := v_written + v_n;

    -- The same days per source, for v_station_day_max (sql/ad4_82): the
    -- maximum in both units, the readings, the last one and the station.
    -- A null source is kept as ''; the view's primary-source test is false
    -- for both.
    insert into derived_station_day_sources
           (city_key, obs_date, source, max_c, max_f, n_readings, last_reading_at, station, computed_at)
    select v_city, (o.valid_at at time zone v_tz)::date, coalesce(o.source, ''),
           max(o.temp_c), max(o.temp_f), count(*), max(o.valid_at), min(o.station), now()
      from weather_observations o
     where o.city_key = v_city and o.temp_c is not null
     group by 2, 3
    on conflict (city_key, obs_date, source) do update
       set max_c = excluded.max_c, max_f = excluded.max_f, n_readings = excluded.n_readings,
           last_reading_at = excluded.last_reading_at, station = excluded.station, computed_at = now()
     where excluded.obs_date >= v_first;
    get diagnostics v_n = row_count;
    v_station := v_station + v_n;

    v_cities := v_cities + 1;
  end loop;

  return jsonb_build_object(
    'ok', true, 'city', p_city, 'cities', v_cities, 'days_written', v_written,
    'station_days_written', v_station,
    'whole_from_instant', v_oldest,
    'ms', round(extract(epoch from (clock_timestamp() - t0)) * 1000));
end;
$fn$;

comment on function public.refresh_city_day_hours(text) is
  'Write each city''s local days as 24 hourly maxima into derived_city_day_hours, and per source into derived_station_day_sources: every day the readings hold whole, and a cut day only if it was never cached (plan v2 P1.6 phase 2). Called once a night for every city - retired ones too, since the prune cuts theirs - by common.refresh_feature_cache.';

revoke all on function public.refresh_city_day_hours(text) from public, anon, authenticated;
grant execute on function public.refresh_city_day_hours(text) to service_role;

create or replace function public.freeze_forecast_latest()
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $fn$
declare
  t0 timestamptz := clock_timestamp();
  v_from    date;
  v_removed int;
  v_written int;
begin
  -- The oldest for_date the forecast table holds: from it on the table's days
  -- are whole (the prune cuts by date and the ingest writes nothing older -
  -- scripts/ingest_forecasts.py), and they replace what was frozen for them.
  select min(for_date) into v_from from weather_forecasts;
  if v_from is null then
    return jsonb_build_object('ok', true, 'rows_written', 0,
                              'note', 'weather_forecasts holds no row');
  end if;

  delete from derived_forecast_latest where for_date >= v_from;
  get diagnostics v_removed = row_count;

  -- Days that have passed: a day still ahead is read from the table.
  insert into derived_forecast_latest (city_key, for_date, model, lead_days, forecast_max_c, run_at, frozen_at)
  select distinct on (city_key, for_date, model, lead_days)
         city_key, for_date, model, lead_days, forecast_max_c, run_at, now()
    from weather_forecasts
   where forecast_max_c is not null
     and for_date < current_date
   order by city_key, for_date, model, lead_days, run_at desc;
  get diagnostics v_written = row_count;

  return jsonb_build_object(
    'ok', true, 'from', v_from, 'rows_replaced', v_removed, 'rows_written', v_written,
    'rows_total', (select count(*) from derived_forecast_latest),
    'ms', round(extract(epoch from (clock_timestamp() - t0)) * 1000));
end;
$fn$;

comment on function public.freeze_forecast_latest() is
  'Copy the forecast standing at each lead - the newest run per city, day, model and lead - into derived_forecast_latest for every day weather_forecasts holds that has passed, replacing what was frozen for them; the days before are left as frozen (plan v2 P1.6 phase 2). Called once a night by common.refresh_feature_cache.';

revoke all on function public.freeze_forecast_latest() from public, anon, authenticated;
grant execute on function public.freeze_forecast_latest() to service_role;

create or replace function public.prune_observations(
  p_keep_days integer,
  p_dry_run boolean default true,
  p_before timestamptz default null,
  p_expected_rows bigint default null
)
returns jsonb
language plpgsql
security definer
set search_path to 'public', 'pg_temp'
as $ad4$
declare
  v_before timestamptz := coalesce(
    p_before,
    (current_date - p_keep_days)::timestamptz
  );
  v_cut date := (v_before at time zone 'UTC')::date;
  v_doomed bigint;
  v_cached_before bigint;
  v_uncovered bigint;
  v_freed text;
  v_unkept bigint;
  v_unkept_station bigint;
begin
  if p_keep_days < 30 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 30 - the model needs months, not days'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune'
    );
  end if;

  if not p_dry_run then
    lock table public.weather_observations in share row exclusive mode;
  end if;

  select count(*) into v_doomed
    from public.weather_observations
   where valid_at < v_before;

  if p_expected_rows is not null and v_doomed <> p_expected_rows then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        'archive row count mismatch: verified %s rows but prune would delete %s',
        p_expected_rows,
        v_doomed
      ),
      'expected_rows', p_expected_rows,
      'would_delete', v_doomed
    );
  end if;

  if v_doomed = 0 then
    return jsonb_build_object(
      'ok', true,
      'deleted', 0,
      'note', format('nothing older than %s', v_before)
    );
  end if;

  select count(*) into v_uncovered
  from (
    select distinct
           o.city_key,
           (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date as d
      from public.weather_observations o
      join public.cities c on c.city_key = o.city_key
     where o.valid_at < v_before
  ) x
  where not exists (
    select 1
      from public.derived_city_day_features f
     where f.city_key = x.city_key
       and f.obs_date = x.d
  );

  if v_uncovered > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        '%s city-day(s) older than %s are not in derived_city_day_features. Run select refresh_feature_cache(); first - pruning now would destroy them.',
        v_uncovered,
        v_cut
      ),
      'uncovered_city_days', v_uncovered
    );
  end if;

  -- ...and in derived_city_day_hours, which v_trajectory_evidence reads for
  -- every day the readings no longer hold (plan v2 P1.6 phase 2). A day with
  -- no temperature has no hours to keep.
  select count(*) into v_unkept
  from (
    select distinct
           o.city_key,
           (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date as d
      from public.weather_observations o
      join public.cities c on c.city_key = o.city_key
     where o.valid_at < v_before
       and o.temp_c is not null
  ) x
  where not exists (
    select 1
      from public.derived_city_day_hours h
     where h.city_key = x.city_key
       and h.obs_date = x.d
  );

  if v_unkept > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        '%s city-day(s) older than %s are not in derived_city_day_hours. Run common.refresh_feature_cache first (it refreshes the hours) - the trajectory evidence reads them after the prune.',
        v_unkept,
        v_cut
      ),
      'unkept_city_days', v_unkept
    );
  end if;

  -- ...and in derived_station_day_sources, which v_station_day_max reads for
  -- those days (plan v2 P1.6 phase 2, step 5): every source of every day.
  select count(*) into v_unkept_station
  from (
    select distinct
           o.city_key,
           (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date as d,
           coalesce(o.source, '') as s
      from public.weather_observations o
      join public.cities c on c.city_key = o.city_key
     where o.valid_at < v_before
       and o.temp_c is not null
  ) x
  where not exists (
    select 1
      from public.derived_station_day_sources k
     where k.city_key = x.city_key
       and k.obs_date = x.d
       and k.source = x.s
  );

  if v_unkept_station > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        '%s city-day source(s) older than %s are not in derived_station_day_sources. Run common.refresh_feature_cache first (it refreshes them) - the settlement agreement reads them after the prune.',
        v_unkept_station,
        v_cut
      ),
      'unkept_station_days', v_unkept_station
    );
  end if;

  select count(*) into v_cached_before
    from public.derived_city_day_features;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true,
      'dry_run', true,
      'would_delete', v_doomed,
      'older_than', v_before,
      'cached_city_days', v_cached_before,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.weather_observations
   where valid_at < v_before;

  v_freed := pg_size_pretty(
    pg_total_relation_size('public.weather_observations')
  );

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'older_than', v_before,
    'cached_city_days', v_cached_before,
    'expected_rows', p_expected_rows,
    'table_now', v_freed,
    'note', 'run VACUUM FULL weather_observations to return the space to the OS'
  );
end;
$ad4$;

comment on function public.prune_observations(integer, boolean, timestamptz, bigint) is
  'Delete archived observations only when p_expected_rows matches the verified export count and every affected city-local day is cached - in derived_city_day_features, derived_city_day_hours (the trajectory evidence) and, per source, derived_station_day_sources (the station''s day). Committed calls require the expected count.';

revoke all on function public.prune_observations(integer, boolean, timestamptz, bigint)
  from public, anon, authenticated;
grant execute on function public.prune_observations(integer, boolean, timestamptz, bigint)
  to service_role;


create or replace function public.prune_forecasts(
  p_keep_days integer,
  p_dry_run boolean default true,
  p_before date default null,
  p_expected_rows bigint default null
)
returns jsonb
language plpgsql
security definer
set search_path to 'public', 'pg_temp'
as $ad4$
declare
  v_before date := coalesce(p_before, current_date - p_keep_days);
  v_doomed bigint;
  v_keep bigint;
  v_skill bigint;
  v_skill_at timestamptz;
  v_newest_doomed date;
  v_freed text;
  v_unfrozen bigint;
  v_unfrozen_latest bigint;
begin
  if p_keep_days < 30 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 30 - skill is measured over months'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune'
    );
  end if;

  if not p_dry_run then
    lock table public.weather_forecasts in share row exclusive mode;
  end if;

  select count(*), max(for_date) into v_doomed, v_newest_doomed
    from public.weather_forecasts
   where for_date < v_before;

  if p_expected_rows is not null and v_doomed <> p_expected_rows then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        'archive row count mismatch: verified %s rows but prune would delete %s',
        p_expected_rows,
        v_doomed
      ),
      'expected_rows', p_expected_rows,
      'would_delete', v_doomed
    );
  end if;

  if v_doomed = 0 then
    return jsonb_build_object(
      'ok', true,
      'deleted', 0,
      'note', format('nothing older than %s', v_before)
    );
  end if;

  select count(*), max(computed_at) into v_skill, v_skill_at
    from public.derived_forecast_skill;

  if v_skill = 0 then
    return jsonb_build_object(
      'ok', false,
      'error', 'derived_forecast_skill is empty - these forecasts have never been scored, and the score is what survives the prune. Run the daily pipeline first.',
      'would_delete', v_doomed
    );
  end if;

  if v_skill_at is null or v_skill_at::date < v_newest_doomed then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        'derived_forecast_skill was last computed %s, before the newest forecast being removed (%s). Their contribution was never measured. Run the daily pipeline first.',
        coalesce(v_skill_at::date::text, 'never'),
        v_newest_doomed
      ),
      'would_delete', v_doomed
    );
  end if;

  -- Every v_hit_forecasts row for the days going is frozen (plan v2 P1.6
  -- phase 2): hit_tournament.py reads 120 days, the table keeps about 30.
  select count(*) into v_unfrozen
  from (
    select city_key, for_date, lane, model, forecast_max_c, known_at
      from public.v_hit_forecasts_live where for_date < v_before
    except
    select city_key, for_date, lane, model, forecast_max_c, known_at
      from public.derived_hit_forecasts where for_date < v_before
  ) x;

  if v_unfrozen > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        '%s v_hit_forecasts row(s) dated before %s are not in derived_hit_forecasts. Run common.refresh_feature_cache first (it freezes them) - the hit tournament reads them after the prune.',
        v_unfrozen,
        v_before
      ),
      'unfrozen_rows', v_unfrozen,
      'would_delete', v_doomed
    );
  end if;

  -- ...and so is the forecast standing at each lead on those days (plan v2
  -- P1.6 phase 2, step 5): both convergence views read 45 days back through
  -- v_forecast_latest, which serves derived_forecast_latest for them.
  select count(*) into v_unfrozen_latest
  from (
    (select distinct on (city_key, for_date, model, lead_days)
            city_key, for_date, model, lead_days, forecast_max_c, run_at
       from public.weather_forecasts
      where for_date < v_before
        and forecast_max_c is not null
      order by city_key, for_date, model, lead_days, run_at desc)
    except
    select city_key, for_date, model, lead_days, forecast_max_c, run_at
      from public.derived_forecast_latest
     where for_date < v_before
  ) x;

  if v_unfrozen_latest > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        '%s forecast(s) standing at a lead on a day before %s are not in derived_forecast_latest. Run common.refresh_feature_cache first (it freezes them) - the convergence views read them after the prune.',
        v_unfrozen_latest,
        v_before
      ),
      'unfrozen_latest_rows', v_unfrozen_latest,
      'would_delete', v_doomed
    );
  end if;

  select count(*) into v_keep
    from public.weather_forecasts
   where for_date >= v_before;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true,
      'dry_run', true,
      'would_delete', v_doomed,
      'would_keep', v_keep,
      'older_than', v_before,
      'skill_rows', v_skill,
      'skill_computed_at', v_skill_at,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.weather_forecasts
   where for_date < v_before;

  v_freed := pg_size_pretty(
    pg_total_relation_size('public.weather_forecasts')
  );

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'kept', v_keep,
    'older_than', v_before,
    'skill_rows', v_skill,
    'expected_rows', p_expected_rows,
    'table_now', v_freed,
    'note', 'run VACUUM FULL weather_forecasts to return the space to the OS'
  );
end;
$ad4$;

comment on function public.prune_forecasts(integer, boolean, date, bigint) is
  'Delete archived forecasts only when p_expected_rows matches the verified export count, derived_forecast_skill proves the rows were scored, and what v_hit_forecasts and v_forecast_latest read for the days going is frozen (derived_hit_forecasts, derived_forecast_latest). Committed calls require the expected count.';

revoke all on function public.prune_forecasts(integer, boolean, date, bigint)
  from public, anon, authenticated;
grant execute on function public.prune_forecasts(integer, boolean, date, bigint)
  to service_role;
