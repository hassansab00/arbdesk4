-- ===========================================================================
-- ad4_42_backtest.sql - SAY WHETHER A BACKTEST CAN WORK BEFORE IT IS RUN.
--
-- WHAT WAS WRONG
-- --------------
-- Queueing a backtest was free and told you nothing. You picked a window,
-- pressed Queue, went to GitHub, pressed Run, waited, and got back either
-- "0 trades" or a failure - and neither told you the actual reason, which is
-- almost always the same one: THE WINDOW HAS NO DATA IN IT.
--
-- scripts/backtest/runner.py needs four things to exist for a city-day before
-- it can simulate it, and it skips silently when any is missing:
--
--   a market with bands on that date        (else there is nothing to price)
--   an observation of what the day did      (else the trade cannot be scored)
--   a forecast made before the entry point  (else there is nothing to trade on)
--   a book snapshot at or before that point (else there is no price to pay)
--
-- Book depth history is the binding one and always will be: Polymarket
-- publishes no depth history, so the desk's own book archive starts the day
-- P0.3 was first run. Forecast accuracy can be backtested over years; strategy
-- profitability cannot be backtested earlier than that date, and no amount of
-- picking a longer window changes it.
--
-- WHAT THIS ADDS
-- --------------
-- backtest_readiness(start, end, cities) - counts each of the four, per the
-- exact window about to be queued, and returns the first thing that would
-- make the run empty. The page calls it as the dates change, so the answer
-- arrives before the run rather than twenty minutes after it.
--
-- v_backtest_window - the widest window that could possibly work, so the
-- form can default to something that returns trades instead of to a date
-- somebody typed once.
--
-- RUN ORDER: after ad4_00_preflight.sql. Re-runnable. Creates a function and
-- a view; writes nothing.
-- ===========================================================================

drop view if exists v_backtest_window cascade;

-- --------------------------------------------------------------------------
-- 1. The widest window with any chance of producing trades.
--
--    Bounded by the book archive, because that is the one input with no
--    history before this desk started collecting it.
-- --------------------------------------------------------------------------
create view v_backtest_window as
with b as (
  select min(observed_at)::date as book_from, max(observed_at)::date as book_to,
         count(*)::bigint as n_books
    from book_snapshots
),
o as (
  select min(valid_at)::date as obs_from, max(valid_at)::date as obs_to,
         count(*)::bigint as n_obs
    from weather_observations
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
    when b.n_books = 0
      then 'No book snapshots at all. Strategy profitability cannot be backtested until P0.3 has run - Polymarket publishes no depth history, so the archive starts the day you first collected it.'
    when m.n_markets = 0
      then 'No markets on record, so there is nothing to backtest against. P0.2 fills them.'
    when o.n_obs = 0
      then 'No observations on record, so no day can be scored. P1.2 or the Observations action fills them.'
    when greatest(b.book_from, o.obs_from, m.mkt_from)
       > least(b.book_to, o.obs_to, m.mkt_to)
      then 'The market, observation and book archives do not overlap on a single day yet.'
    else null
  end                                                    as blocked_because
from b, o, m;

comment on view v_backtest_window is
  'The widest date range that could produce trades, and the first reason it could not. Bounded by the book archive: Polymarket publishes no depth history, so strategy profitability cannot be tested earlier than the day P0.3 first ran.';


-- --------------------------------------------------------------------------
-- 2. Readiness for THE window about to be queued.
--
--    A function rather than a view because the window is an argument, and a
--    view would mean the browser fetching every city-day to count them.
--
--    Every count below is the same condition runner.py uses to decide whether
--    to skip a city-day, so a green answer here means the run will do work -
--    not that it will be profitable, which is what the run is for.
-- --------------------------------------------------------------------------
create or replace function backtest_readiness(
  p_start date,
  p_end   date,
  p_cities text[] default null
) returns jsonb
language plpgsql stable security definer as $ad4$
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
      exists (select 1 from weather_observations o
               where o.city_key = mk.city_key
                 and o.valid_at::date = mk.resolution_date)                    as has_obs,
      exists (select 1 from weather_forecasts f
               where f.city_key = mk.city_key
                 and f.for_date = mk.resolution_date)                          as has_fc,
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


-- --------------------------------------------------------------------------
-- 3. Grants. The readiness check is read-only and the form needs it on every
--    keystroke, so anon may call it.
-- --------------------------------------------------------------------------
do $ad4$
declare r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_backtest_window to %I', r);
      execute format('grant execute on function backtest_readiness(date, date, text[]) to %I', r);
    end if;
  end loop;
end
$ad4$;

do $ad4$
declare v record;
begin
  select * into v from v_backtest_window;
  if v.blocked_because is not null then
    raise notice 'ad4_42: backtesting is not possible yet - %', v.blocked_because;
  else
    raise notice 'ad4_42: usable backtest window is % to % (% book snapshot(s), % market(s))',
      v.usable_from, v.usable_to, v.n_books, v.n_markets;
  end if;
end
$ad4$;
