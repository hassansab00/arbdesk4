-- ===========================================================================
-- THE BACKTEST COUNTS WHAT IT COUNTED (plan v2 P1.6 phase 2, step 5 part (a),
-- 29 Sep, the same evening as 20260929180000)
--
-- 20260929180000 let v_backtest_window and backtest_readiness read the new
-- caches. Checked live after it was applied (14:48:26Z) and the caches
-- first filled (14:49:06Z), two answers moved on days the weather tables
-- still hold:
--
--   v_backtest_window.obs_from   31 Jul -> 30 Jul. It took the first cached
--                                LOCAL date; the readings' first UTC date is
--                                31 Jul (02:45Z), which is what it said before.
--   backtest_readiness, the 40 days to 28 Sep: with an observation 1,823 ->
--                                1,824. Taipei 21 Sep has 8 readings, the
--                                last at 07:00 local (20 Sep 23:00Z; none
--                                again before 22 Sep 16:00Z), none on the UTC date,
--                                and no verified outcome - the runner skips
--                                it; the cached local day counted it.
--
-- So: derived_station_day_sources keeps each day's first reading, and
-- obs_from is the UTC date of the first reading the readings or the cache
-- hold; readiness consults the caches only for days before the first day
-- the tables hold. Days the tables hold are counted exactly as before.
--
-- first_reading_at is filled from the readings for every cached row: none
-- has been pruned since the cache was first filled (14:49:06Z). The bodies are
-- sql/ad4_29_retention.sql, ad4_42_backtest.sql and ad4_97_evidence_cache.sql.
-- Re-runnable.
-- ===========================================================================
alter table derived_station_day_sources add column if not exists first_reading_at timestamptz;

update derived_station_day_sources k
   set first_reading_at = x.first_at
  from (select o.city_key, (o.valid_at at time zone coalesce(c.timezone, 'UTC'))::date as d,
               coalesce(o.source, '') as s, min(o.valid_at) as first_at
          from weather_observations o
          join cities c on c.city_key = o.city_key
         where o.temp_c is not null
         group by 1, 2, 3) x
 where k.city_key = x.city_key and k.obs_date = x.d and k.source = x.s
   and k.first_reading_at is null;

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
-- every day they held, with its first reading. Observations begin on the UTC
-- date of the first reading either holds - the date this always gave. The
-- count stays the table's rows.
o as (
  select (least((select min(valid_at) from weather_observations),
                (select min(first_reading_at) from derived_station_day_sources)))::date as obs_from,
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
  -- The first days the weather tables hold (plan v2 P1.6 phase 2, step 5):
  -- a day from them on is counted from the tables, as it always was; a day
  -- before them from what the caches kept of it.
  v_obs_held    date := coalesce((select min(valid_at) from weather_observations)::date, 'infinity'::date);
  v_fc_held     date := coalesce((select min(for_date) from weather_forecasts), 'infinity'::date);
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
       or (mk.resolution_date < v_obs_held
           and exists (select 1 from derived_station_day_sources s
                        where s.city_key = mk.city_key
                          and s.obs_date = mk.resolution_date)))               as has_obs,
      (exists (select 1 from weather_forecasts f
                where f.city_key = mk.city_key
                  and f.for_date = mk.resolution_date)
       or (mk.resolution_date < v_fc_held
           and exists (select 1 from derived_forecast_latest l
                        where l.city_key = mk.city_key
                          and l.for_date = mk.resolution_date)))               as has_fc,
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
    -- maximum in both units, the readings, the first and last one and the
    -- station. A null source is kept as ''; the view's primary-source test
    -- is false for both.
    insert into derived_station_day_sources
           (city_key, obs_date, source, max_c, max_f, n_readings, last_reading_at, station, computed_at,
            first_reading_at)
    select v_city, (o.valid_at at time zone v_tz)::date, coalesce(o.source, ''),
           max(o.temp_c), max(o.temp_f), count(*), max(o.valid_at), min(o.station), now(),
           min(o.valid_at)
      from weather_observations o
     where o.city_key = v_city and o.temp_c is not null
     group by 2, 3
    on conflict (city_key, obs_date, source) do update
       set max_c = excluded.max_c, max_f = excluded.max_f, n_readings = excluded.n_readings,
           last_reading_at = excluded.last_reading_at, station = excluded.station, computed_at = now(),
           first_reading_at = excluded.first_reading_at
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
