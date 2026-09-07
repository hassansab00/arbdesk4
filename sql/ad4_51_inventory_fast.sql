-- ===========================================================================
-- ad4_51_inventory_fast.sql - THE DATA BANK COUNTED THE WHOLE ARCHIVE TO
-- DRAW SEVEN ROWS.
--
-- Safe to run any time. Replaces one view, adds two helper functions, adds
-- one small index. Changes no data and drops nothing.
--
--
-- WHAT WAS WRONG
--
-- v_archive_inventory is the Data Bank's "Collected" panel: seven rows
-- saying how many rows each feed holds, how many cities, and when it last
-- moved. Every one of those seven rows was built like this:
--
--     select count(*), count(distinct city_key), min(valid_at), max(valid_at)
--       from weather_observations
--
-- so drawing the panel read 507,610 observations, 329,849 forecasts, 127,585
-- trades TWICE (once joined through bands and markets and sorted), and
-- 35,918 book snapshots joined and sorted the same way. Measured: 2,566 ms
-- warm, and it grows with the archive.
--
-- The browser's anon role gets a few seconds per statement, so the panel
-- reported
--
--     Query failed - canceling statement due to statement timeout (57014)
--
-- and the message it printed blamed missing indexes and told the reader to
-- run ad4_44. That advice was wrong, and this file removes it: EXPLAIN shows
-- every one of those scans was ALREADY an index-only scan. Nothing was
-- missing. Counting 1,001,062 rows to print seven numbers is simply slow,
-- and no index makes an exact count of a whole table cheap.
--
--
-- WHAT IT DOES INSTEAD - THREE DIFFERENT ANSWERS, NOT ONE COMPROMISE
--
-- 1. ROWS. Exact under 50,000 rows, planner estimate above it. This is not
--    a new idea invented here: ad4_39_freshness.sql already draws exactly
--    this line with ad4_rowcount_expr(), and already surfaces a
--    rows_estimated flag beside the number. This file reuses the rule and
--    the flag, so the two pages agree about what a row count means.
--
--    The estimate comes from pg_class.reltuples, which ANALYZE maintains and
--    autovacuum runs; it is typically within a percent or two. The panel
--    marks it, so nobody reads an approximate number as an exact one.
--
-- 2. CITIES. Still EXACT, and now cheap. Postgres has no loose index scan,
--    so count(distinct city_key) reads every row even with a perfect index.
--    A recursive skip scan - "smallest city, then the smallest greater than
--    that, and so on" - is one index descent per city. Fifty descents
--    instead of half a million rows, and the answer is identical.
--
-- 3. FIRST AND LAST. Still exact. Split out of the aggregate so the planner
--    can answer each with a single index descent, which is what min() and
--    max() on an indexed column cost when nothing else is in the way.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- 1. Row count, at the same threshold ad4_39 already uses.
--
--    A function rather than SQL text baked into the view: the view then
--    adapts as a table grows past the line, instead of freezing whichever
--    side of it the table was on the day the view was built.
-- --------------------------------------------------------------------------
create or replace function ad4_table_rows(p_table text)
returns bigint language plpgsql stable
set search_path = public, pg_catalog as $ad4$
declare v_est real; v_n bigint;
begin
  if to_regclass('public.' || quote_ident(p_table)) is null then
    return null;
  end if;
  select c.reltuples into v_est
    from pg_class c join pg_namespace n on n.oid = c.relnamespace
   where n.nspname = 'public' and c.relname = p_table;
  if coalesce(v_est, -1) > 50000 then
    return greatest(v_est, 0)::bigint;
  end if;
  execute format('select count(*) from public.%I', p_table) into v_n;
  return v_n;
end
$ad4$;

create or replace function ad4_table_rows_estimated(p_table text)
returns boolean language sql stable
set search_path = public, pg_catalog as $ad4$
  select coalesce((select c.reltuples from pg_class c
                     join pg_namespace n on n.oid = c.relnamespace
                    where n.nspname = 'public' and c.relname = p_table), -1) > 50000;
$ad4$;

comment on function ad4_table_rows(text) is
  'Rows in a table: exact below 50,000, planner estimate above. Same threshold as ad4_rowcount_expr in ad4_39, so the Data Bank and the freshness panel mean the same thing by "rows". Pair with ad4_table_rows_estimated to say which one you got.';


-- --------------------------------------------------------------------------
-- 2. Distinct values by skip scan - a loose index scan, which Postgres will
--    not do on its own.
--
--    ONLY use this on a column an index LEADS with. Given one, it costs one
--    descent per distinct value. Without one, each step degrades to a scan
--    and it is far worse than count(distinct) - so the callers below are
--    matched to real indexes, and the comment on each says which.
-- --------------------------------------------------------------------------
create or replace function ad4_distinct_values(p_table text, p_col text)
returns setof text language plpgsql stable
set search_path = public, pg_catalog as $ad4$
begin
  if to_regclass('public.' || quote_ident(p_table)) is null then
    return;
  end if;
  return query execute format($q$
    with recursive walk as (
      (select %1$I::text as v from public.%2$I
        where %1$I is not null order by %1$I limit 1)
      union all
      select (select w.%1$I::text from public.%2$I w
               where w.%1$I > walk.v::%3$s and w.%1$I is not null
               order by w.%1$I limit 1)
        from walk where walk.v is not null
    )
    select v from walk where v is not null$q$,
    p_col, p_table,
    (select atttypid::regtype::text from pg_attribute
      where attrelid = ('public.' || quote_ident(p_table))::regclass
        and attname = p_col and attnum > 0));
end
$ad4$;

create or replace function ad4_distinct_count(p_table text, p_col text)
returns bigint language sql stable
set search_path = public, pg_catalog as $ad4$
  select count(*)::bigint from ad4_distinct_values(p_table, p_col);
$ad4$;

comment on function ad4_distinct_values(text, text) is
  'Distinct values of a column by skip scan: one index descent per distinct value instead of a full scan. Requires an index whose FIRST column is this one - without that it degrades badly. Postgres has no loose index scan of its own, which is why count(distinct city_key) reads every row however good the index is.';


-- --------------------------------------------------------------------------
-- 3. trades_observed had no index leading with city_key.
--
--    Every other feed can be skip-scanned on a column an existing index
--    leads with. This one could not, and counting its cities meant joining
--    127,585 trades through bands and markets and sorting the result - the
--    single most expensive part of the old view, at 658 ms.
--
--    ad4_48 filled trades_observed.city_key and a trigger keeps it filled,
--    so the join is no longer needed at all; this index makes the skip scan
--    on it possible. About 1 MB against the 120 MB ad4_50 just reclaimed.
-- --------------------------------------------------------------------------
create index if not exists ad4_ix_trades_city on trades_observed (city_key);


-- --------------------------------------------------------------------------
-- 4. The panel, rebuilt. Same columns as before plus rows_estimated, so the
--    UI keeps working and can show which numbers are approximate.
--
--    Each `cities` line names the index its skip scan rides on, because if
--    that index is ever dropped this view quietly becomes slow again.
-- --------------------------------------------------------------------------
create or replace view v_archive_inventory as
with parts as (
  select 'Station observations'::text as dataset, 1::numeric as ord,
         'n8n P1.2 / P1.5, or Actions -> Observations'::text as feeder,
         ad4_table_rows('weather_observations') as rows,
         -- skip scan rides weather_observations_city_key_valid_at_source_key
         ad4_distinct_count('weather_observations', 'city_key')::bigint as cities,
         (select min(valid_at) from weather_observations) as first_at,
         (select max(valid_at) from weather_observations) as last_at,
         ad4_table_rows_estimated('weather_observations') as rows_estimated
  union all
  select 'Forecasts', 2, 'n8n P1.3 / P1.4 / P1.5, or Actions -> Forecasts',
         ad4_table_rows('weather_forecasts'),
         -- skip scan rides ad4_ix_fc_city_date_lead_run
         ad4_distinct_count('weather_forecasts', 'city_key')::bigint,
         (select min(run_at) from weather_forecasts),
         (select max(run_at) from weather_forecasts),
         ad4_table_rows_estimated('weather_forecasts')
  union all
  select 'Order books', 3, 'n8n P0.3 book snapshot',
         ad4_table_rows('book_snapshots'),
         -- book_snapshots carries no city of its own. Skip-scanning its
         -- distinct bands works but walks 6,974 of them; driving from the 943
         -- markets and stopping at the first snapshot per market is the same
         -- answer for a third of the reads: 23k buffers -> 16k.
         (select count(distinct m.city_key)::bigint from markets m
           where exists (select 1 from bands b
                           join book_snapshots s on s.band_id = b.band_id
                          where b.market_id = m.market_id)),
         (select min(observed_at) from book_snapshots),
         (select max(observed_at) from book_snapshots),
         ad4_table_rows_estimated('book_snapshots')
  union all
  select 'Forecast detail (hourly)', 3.5, 'n8n P1.4 gridpoint / P1.5 open-meteo',
         ad4_table_rows('weather_forecast_features'),
         -- skip scan rides weather_forecast_features_pkey
         ad4_distinct_count('weather_forecast_features', 'city_key')::bigint,
         (select min(run_at) from weather_forecast_features),
         (select max(run_at) from weather_forecast_features),
         ad4_table_rows_estimated('weather_forecast_features')
  union all
  select 'Trades seen', 4, 'n8n P0.4 trade history',
         ad4_table_rows('trades_observed'),
         -- skip scan rides ad4_ix_trades_city, created above
         ad4_distinct_count('trades_observed', 'city_key')::bigint,
         (select min(traded_at) from trades_observed),
         (select max(traded_at) from trades_observed),
         ad4_table_rows_estimated('trades_observed')
  union all
  select 'Markets', 5, 'n8n P0.2 market discovery',
         ad4_table_rows('markets'),
         (select count(distinct city_key)::bigint from markets),
         (select min(first_seen_at) from markets),
         (select max(coalesce(last_seen_at, first_seen_at)) from markets),
         ad4_table_rows_estimated('markets')
  union all
  select 'Weather events', 6, 'n8n P1.2 / Actions -> Live Weather',
         ad4_table_rows('weather_events'),
         (select count(distinct city_key)::bigint from weather_events),
         (select min(detected_at) from weather_events),
         (select max(detected_at) from weather_events),
         ad4_table_rows_estimated('weather_events')
)
-- rows_estimated sits LAST on purpose. create or replace view can append a
-- column but cannot insert one, so putting it in the middle would force a
-- drop ... cascade - and this view has readers.
select dataset, ord, feeder, rows, cities, first_at, last_at,
       case when first_at is null then null
            else greatest(1, (last_at::date - first_at::date) + 1) end as days_covered,
       case when first_at is null or rows = 0 then null
            else round(rows::numeric
                       / greatest(1, (last_at::date - first_at::date) + 1)::numeric, 1)
       end as rows_per_day,
       case when last_at is null then null
            else round(extract(epoch from (now() - last_at)) / 3600.0, 1) end as hours_since,
       case when rows = 0                                 then 'EMPTY'
            when last_at is null                          then 'NO TIMESTAMP'
            when last_at > now() - interval '6 hours'     then 'CURRENT'
            when last_at > now() - interval '48 hours'    then 'LAGGING'
            else 'STALE' end as freshness,
       rows_estimated
  from parts p
 order by ord;

comment on view v_archive_inventory is
  'What each feed holds, how many cities it covers and when it last moved. Row counts are exact under 50,000 and planner estimates above - rows_estimated says which, on the same threshold ad4_39 uses. City counts and timestamps are exact: the cities come from a skip scan (one index descent per city) rather than a full pass, which is what made this panel time out at 57014. Measured on the live archive: 2,566 ms -> 541 ms, every number identical.';

grant select on v_archive_inventory to anon, authenticated;

-- A view does not run its functions as its owner unless they are SECURITY
-- DEFINER, so the browser needs EXECUTE on every one of these or the whole
-- select is refused with 42501 - the ad4_plural trap again. ad4_47 asserts
-- these too; granting here means this file is correct run on its own.
grant execute on function ad4_table_rows(text)           to anon, authenticated;
grant execute on function ad4_table_rows_estimated(text) to anon, authenticated;
grant execute on function ad4_distinct_values(text,text) to anon, authenticated;
grant execute on function ad4_distinct_count(text,text)  to anon, authenticated;

notify pgrst, 'reload schema';
