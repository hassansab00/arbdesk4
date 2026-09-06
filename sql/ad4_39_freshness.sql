-- ===========================================================================
-- ad4_39_freshness.sql - IS THIS PANEL EMPTY, STALE, OR FINE?
--
-- THE PROBLEM
-- -----------
-- Every panel in the UI has three ways of showing nothing and they all look
-- identical:
--
--   1. the table does not exist            (a SQL file was never run)
--   2. the table exists and is empty       (a job has never run, or fails)
--   3. the table has rows but they are old (a job stopped running)
--
-- (1) is already answered - a missing relation names the file that creates it.
-- (2) and (3) were not answered anywhere, which is what "the data is
-- outdated" means and why it was impossible to act on: nothing on the page
-- said which job was supposed to fill it, when it last did, or whether the
-- silence was a fault at all.
--
-- WHAT THIS DOES
-- --------------
-- One view, v_data_freshness, with a row per table the UI depends on:
-- how many rows, the newest timestamp in it, how old that is, and a verdict.
-- The browser reads it once and every panel on every page can then say
-- something true about itself. web/lib/provenance.ts carries the other half -
-- WHICH job fills each table - derived from the repo by
-- tools/gen_provenance.py.
--
-- HOW IT SURVIVES A DIFFERENT SCHEMA
-- ----------------------------------
-- The table list and each table's timestamp column are resolved AGAINST THE
-- LIVE CATALOG when this file runs, and the view is assembled from what is
-- actually there. A table that does not exist is reported as absent instead
-- of erroring; a table whose timestamp column is named something else is
-- picked up from the candidate list. So this installs on a fresh database and
-- on production, and it does not need editing when a table is added.
--
-- STALENESS IS NOT ONE NUMBER. A market-discovery table going a day without a
-- write is normal; the live weather cache going a day without a write is
-- broken. Each row carries its own expectation, in hours, stated below.
--
-- RUN ORDER: after ad4_00_preflight.sql. Re-runnable. Read-only at run time.
-- ===========================================================================

drop view if exists v_data_freshness cascade;

-- --------------------------------------------------------------------------
-- 1. What we expect of each table.
--
--    fresh_hours is the age past which the newest row means something is
--    wrong. It is a claim about the JOB's cadence, not about the market:
--    a table filled by a daily action is not stale at 25 hours.
--
--    NULL fresh_hours = an accumulating record with no cadence (the ledger,
--    the backtest history). Age is reported, never judged.
-- --------------------------------------------------------------------------
create table if not exists data_freshness_spec (
  table_name    text primary key,
  ts_column     text,          -- resolved at install time if null
  fresh_hours   numeric,       -- null = never call it stale
  layer         text not null, -- what part of the desk this belongs to
  plain_english text not null  -- what it holds, in words, for the UI
);

insert into data_freshness_spec (table_name, ts_column, fresh_hours, layer, plain_english) values
  ('cities',                    null,  null, 'market',    'The cities the desk trades. Edited by hand, not by a job.'),
  ('markets',                   'last_seen_at',   26, 'market',    'One row per Polymarket day-market the desk knows about.'),
  ('bands',                     null,             26, 'market',    'The temperature buckets inside each market.'),
  ('book_snapshots',            'observed_at',     2, 'market',    'What the order book looked like, sampled through the day.'),
  ('trades_observed',           'traded_at',       6, 'market',    'Trades that actually happened, used for real traded volume.'),
  ('weather_observations',      'valid_at',        3, 'weather',   'The archive of what the temperature actually was.'),
  ('weather_forecasts',         'run_at',          8, 'weather',   'What each model said the temperature would be.'),
  ('weather_forecast_features', 'run_at',         12, 'weather',   'Hour-by-hour forecast detail: cloud, wind, humidity.'),
  ('live_weather',              'updated_at',      2, 'weather',   'The current reading per city, refreshed through the day.'),
  ('weather_events',            'detected_at',  null, 'weather',   'Notable weather worth an alert. Empty is good news.'),
  ('band_probabilities',        'computed_at',    12, 'model',     'The desk''s probability for each bucket.'),
  ('edges',                     'computed_at',    12, 'model',     'Where the desk''s probability differs from the market price.'),
  ('derived_city_day_features', 'computed_at',    30, 'model',     'One row per city per day: the shape of that day''s temperature.'),
  ('derived_climb_profile',     'computed_at',   200, 'model',     'How fast a city normally warms, hour by hour.'),
  ('derived_weather_peak',      'computed_at',   200, 'model',     'What hour each city normally peaks, per month.'),
  ('derived_city_climate',      'computed_at',    30, 'model',     'Each city''s normal for the time of year.'),
  ('derived_forecast_skill',    'computed_at',   200, 'model',     'How accurate each model has been, per city, per lead day.'),
  ('derived_weather_model',     'fitted_at',     400, 'model',     'The desk''s own fitted correction to the public forecast.'),
  ('derived_model_forecast',    'run_at',         30, 'model',     'The desk''s own forward prediction, after that correction.'),
  ('derived_capacity',          'computed_at',    30, 'model',     'How much money a market could absorb without moving.'),
  ('derived_city_correlation',  'computed_at',   200, 'model',     'Which cities move together, so two trades are not one bet.'),
  ('derived_city_day_volume',   'computed_at',    12, 'market',    'Dollars traded per city per day.'),
  ('derived_band_day_volume',   'computed_at',    12, 'market',    'Dollars traded per bucket per day.'),
  ('signals',                   'fired_at',       26, 'trading',   'Trades a strategy asked for.'),
  ('paper_trades',              'opened_at',    null, 'trading',   'Positions the paper desk holds or has closed.'),
  ('ledger',                    'recorded_at',  null, 'trading',   'Every cash movement. Accumulates; never stale.'),
  ('fact_forecast_outcome',     'captured_at',    30, 'databank',  'Settled evidence: what was forecast against what happened.'),
  ('fact_band_outcome',         'captured_at',    30, 'databank',  'Settled evidence: what a bucket was priced at against whether it won.'),
  ('fact_signal_outcome',       'captured_at',    30, 'databank',  'Settled evidence: what a signal claimed against what it made.'),
  ('backtest_runs',             'created_at',   null, 'databank',  'Backtests you have run.'),
  ('backtest_results',          null,           null, 'databank',  'What each backtest scored.'),
  ('backtest_trades',           null,           null, 'databank',  'Every trade a backtest would have taken.'),
  ('strategy_conflicts',        'detected_at',  null, 'trading',   'Where two strategies wanted opposite sides of the same bucket.'),
  ('anomalies',                 'detected_at',  null, 'health',    'Things the desk flagged as odd. Empty is good news.'),
  ('ingest_log',                'logged_at',       3, 'health',    'Every job run, with what it wrote.')
on conflict (table_name) do update set
  ts_column     = excluded.ts_column,
  fresh_hours   = excluded.fresh_hours,
  layer         = excluded.layer,
  plain_english = excluded.plain_english;


-- --------------------------------------------------------------------------
-- 2. Resolve the timestamp column for any row that did not name one, by
--    asking the catalog rather than assuming. First match wins.
-- --------------------------------------------------------------------------
do $ad4$
declare
  r   record;
  col text;
begin
  for r in select table_name from data_freshness_spec where ts_column is null loop
    if to_regclass('public.' || r.table_name) is null then continue; end if;
    select a.attname into col
      from pg_attribute a
     where a.attrelid = to_regclass('public.' || r.table_name)
       and a.attnum > 0 and not a.attisdropped
       and a.atttypid in ('timestamptz'::regtype, 'timestamp'::regtype)
     order by case a.attname
                when 'captured_at' then 1 when 'computed_at' then 2
                when 'observed_at' then 3 when 'updated_at'  then 4
                when 'ingested_at' then 5 when 'created_at'  then 6
                when 'logged_at'   then 7 else 99 end, a.attnum
     limit 1;
    if col is not null then
      update data_freshness_spec set ts_column = col where table_name = r.table_name;
    end if;
  end loop;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 3. Build the view.
--
--    Assembled as text and executed, because a view cannot decide at run time
--    which column to read. The alternative - a function returning setof -
--    cannot be selected from by PostgREST with a filter, and the browser
--    wants to filter.
-- --------------------------------------------------------------------------
do $ad4$
declare
  r     record;
  parts text[] := '{}';
  sql   text;
begin
  for r in select * from data_freshness_spec order by table_name loop
    if to_regclass('public.' || r.table_name) is null then
      -- ABSENT is a real answer, and a more useful one than a missing row.
      parts := parts || format(
        $q$select %L::text as table_name, %L::text as layer, %L::text as plain_english,
                  null::bigint as rows, null::timestamptz as newest,
                  null::numeric as age_hours, %s::numeric as fresh_hours,
                  'absent'::text as state$q$,
        r.table_name, r.layer, r.plain_english, coalesce(r.fresh_hours::text, 'null'));
    elsif r.ts_column is null then
      parts := parts || format(
        $q$select %L::text as table_name, %L::text as layer, %L::text as plain_english,
                  (select count(*) from public.%I)::bigint as rows,
                  null::timestamptz as newest, null::numeric as age_hours,
                  %s::numeric as fresh_hours,
                  (case when (select count(*) from public.%I) = 0 then 'empty' else 'ok' end)::text as state$q$,
        r.table_name, r.layer, r.plain_english, r.table_name,
        coalesce(r.fresh_hours::text, 'null'), r.table_name);
    else
      parts := parts || format(
        $q$select %L::text as table_name, %L::text as layer, %L::text as plain_english,
                  t.n as rows, t.newest,
                  round(extract(epoch from (now() - t.newest)) / 3600.0, 1) as age_hours,
                  %s::numeric as fresh_hours,
                  (case when t.n = 0 then 'empty'
                        when t.newest is null then 'ok'
                        when %s is null then 'ok'
                        when extract(epoch from (now() - t.newest)) / 3600.0 > %s then 'stale'
                        else 'ok' end)::text as state
             from (select count(*) n, max(%I) newest from public.%I) t$q$,
        r.table_name, r.layer, r.plain_english,
        coalesce(r.fresh_hours::text, 'null'),
        coalesce(r.fresh_hours::text, 'null'),
        coalesce(r.fresh_hours::text, '1e9'),
        r.ts_column, r.table_name);
    end if;
  end loop;

  sql := 'create view v_data_freshness as ' || array_to_string(parts, E'\nunion all\n');
  execute sql;
end
$ad4$;

comment on view v_data_freshness is
  'One row per table the UI depends on: how many rows it holds, the newest timestamp in it, how old that is, and whether that counts as absent / empty / stale / ok. The browser reads this once so every panel can say why it is showing nothing.';


-- --------------------------------------------------------------------------
-- 4. A one-line answer for the top of a page.
-- --------------------------------------------------------------------------
create or replace view v_data_health as
select
  count(*) filter (where state = 'absent')          as absent,
  count(*) filter (where state = 'empty')           as empty,
  count(*) filter (where state = 'stale')           as stale,
  count(*) filter (where state = 'ok')              as ok,
  count(*)                                          as tracked,
  max(newest)                                       as newest_write,
  case
    when count(*) filter (where state = 'absent') > 0 then 'sql missing'
    when count(*) filter (where state = 'empty')  > 0 then 'jobs have not run'
    when count(*) filter (where state = 'stale')  > 0 then 'jobs stopped'
    else 'healthy'
  end                                               as verdict
from v_data_freshness;

comment on view v_data_health is
  'The freshness view collapsed to one row, for a status strip: how many tracked tables are absent, empty, stale or fine, and a one-phrase verdict.';


-- --------------------------------------------------------------------------
-- 5. Grants. Read-only, and the browser needs it on every page.
-- --------------------------------------------------------------------------
do $ad4$
declare o text; r text;
begin
  foreach o in array array['v_data_freshness', 'v_data_health', 'data_freshness_spec'] loop
    foreach r in array array['anon', 'authenticated', 'service_role'] loop
      if exists (select 1 from pg_roles where rolname = r) then
        execute format('grant select on %I to %I', o, r);
      end if;
    end loop;
  end loop;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    execute 'grant all on data_freshness_spec to service_role';
  end if;
end
$ad4$;

do $ad4$
declare v record;
begin
  select * into v from v_data_health;
  raise notice 'ad4_39: % table(s) tracked - % ok, % stale, % empty, % absent. Verdict: %',
    v.tracked, v.ok, v.stale, v.empty, v.absent, v.verdict;
end
$ad4$;

select layer, table_name, rows, age_hours, state, plain_english
  from v_data_freshness
 order by case state when 'absent' then 0 when 'empty' then 1 when 'stale' then 2 else 3 end,
          layer, table_name;
