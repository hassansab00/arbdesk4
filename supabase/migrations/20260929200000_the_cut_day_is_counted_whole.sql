-- ===========================================================================
-- THE CUT DAY IS COUNTED WHOLE (plan v2 P1.6 phase 2, step 5 part (a),
-- 29 Sep, after 20260929190000)
--
-- 20260929190000 counts a backtest day from the weather tables from the
-- first day they hold, and from the caches only before it. The readings are
-- cut at an instant, so the UTC day the cut falls in is only partly held.
-- Simulated live after it was applied (one REPEATABLE READ snapshot, the readings cut at
-- 30 Aug 14:00Z and the forecasts before 30 Aug): readiness for 25 Jul to
-- today lost one market-day, Panama City 30 Aug, whose one reading on that
-- UTC day is at 00:00Z. Counting the cut day from the caches too, the same
-- simulation changes no market-day of 1,931 (475 of them on cut days) for
-- an observation or a forecast.
--
-- The body is sql/ad4_42_backtest.sql. Re-runnable.
-- ===========================================================================
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
  -- The first days the weather tables hold whole (plan v2 P1.6 phase 2,
  -- step 5): a day from them on is counted from the tables, as it always
  -- was; a day before them also from what the caches kept of it. The
  -- readings are cut at an instant, so the UTC day it falls in is part-held:
  -- Panama City's one reading on 30 Aug is at 00:00Z, and a cut at 14:00Z
  -- would have lost the day.
  v_obs_held    date := coalesce((select case when min(valid_at) = date_trunc('day', min(valid_at))
                                              then min(valid_at)::date else min(valid_at)::date + 1 end
                                    from weather_observations), 'infinity'::date);
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

grant execute on function backtest_readiness(date, date, text[]) to anon, authenticated, service_role;
