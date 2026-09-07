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
  -- '-' rather than null: the resolver would otherwise pick the first
  -- timestamp column it finds, which on this table is nws_checked_at -
  -- when the NWS grid id was last looked up, not when the row changed.
  -- A city list has no cadence and no age worth reporting.
  ('cities',                    '-',   null, 'market',    'The cities the desk trades. Edited by hand, not by a job.'),
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
  ('derived_forecast_skill',    'computed_at',   200, 'model',     'How accurate the forecast has been, per city, per lead day. This is the number sigma is built from.'),
  ('derived_forecast_skill_model', 'computed_at', 200, 'model',    'The same measurement split out per model, so the blend cannot hide a bad one.'),
  ('derived_calibration_adjustment', 'computed_at', 200, 'model',   'How wide the desk''s stated confidence actually turned out to be, per city.'),
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
  for r in select table_name from data_freshness_spec where ts_column is null loop  -- '-' is skipped: it means "deliberately none"
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
-- --------------------------------------------------------------------------
-- COUNTING WITHOUT SCANNING.
--
-- This view renders on EVERY page, and it was asking `count(*)` of every
-- table it describes - which on this desk means a full scan of 507,610
-- observations, 329,553 forecasts and 127,585 trades, every page load, for a
-- number displayed as "507.6k". 1.3 seconds of the browser's few-second
-- budget spent on a figure nobody reads to four significant digits.
--
-- Two changes, and neither loses anything the view actually decides:
--
--   THE VERDICT stays exact. It only ever needed to know whether the table is
--   EMPTY, and `not exists (select 1 ...)` answers that by reading one row
--   rather than all of them.
--
--   THE COUNT is exact where counting is cheap and an estimate where it is
--   not. pg_class.reltuples is maintained by ANALYZE and is what the planner
--   itself trusts; on a table of half a million rows it is the right answer
--   to "how big is this". Anything under 50,000 rows is still counted
--   exactly, which is most of the schema. rows_estimated says which you are
--   looking at, so the UI can print the tilde rather than implying precision
--   it does not have.
-- --------------------------------------------------------------------------
create or replace function ad4_rowcount_is_estimate(p_table text)
returns text language sql stable as $ad4$
  select case when coalesce((select c.reltuples from pg_class c
                              join pg_namespace n on n.oid = c.relnamespace
                             where n.nspname = 'public' and c.relname = p_table), -1) > 50000
              then 'true' else 'false' end;
$ad4$;

create or replace function ad4_rowcount_expr(p_table text)
returns text language sql stable as $ad4$
  select case
    when coalesce((select c.reltuples from pg_class c
                    join pg_namespace n on n.oid = c.relnamespace
                   where n.nspname = 'public' and c.relname = p_table), -1) > 50000
      then format('(select greatest(c.reltuples, 0)::bigint from pg_class c '
                  'join pg_namespace n on n.oid = c.relnamespace '
                  'where n.nspname = ''public'' and c.relname = %L)', p_table)
    else format('(select count(*) from public.%I)::bigint', p_table)
  end;
$ad4$;

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
    elsif r.ts_column is null or r.ts_column = '-' then
      parts := parts || format(
        $q$select %L::text as table_name, %L::text as layer, %L::text as plain_english,
                  %s as rows, %s as rows_estimated,
                  null::timestamptz as newest, null::numeric as age_hours,
                  %s::numeric as fresh_hours,
                  (case when not exists (select 1 from public.%I) then 'empty' else 'ok' end)::text as state$q$,
        r.table_name, r.layer, r.plain_english,
        ad4_rowcount_expr(r.table_name), ad4_rowcount_is_estimate(r.table_name),
        coalesce(r.fresh_hours::text, 'null'), r.table_name);
    else
      parts := parts || format(
        $q$select %L::text as table_name, %L::text as layer, %L::text as plain_english,
                  %s as rows, %s as rows_estimated,
                  t.newest,
                  round(extract(epoch from (now() - t.newest)) / 3600.0, 1) as age_hours,
                  %s::numeric as fresh_hours,
                  (case when not exists (select 1 from public.%I) then 'empty'
                        when t.newest is null then 'ok'
                        when %s is null then 'ok'
                        when extract(epoch from (now() - t.newest)) / 3600.0 > %s then 'stale'
                        else 'ok' end)::text as state
             from (select max(%I) newest from public.%I) t$q$,
        r.table_name, r.layer, r.plain_english,
        ad4_rowcount_expr(r.table_name), ad4_rowcount_is_estimate(r.table_name),
        coalesce(r.fresh_hours::text, 'null'),
        r.table_name,
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
-- 4b. NUMBERS THAT WERE INVENTED, NOT MEASURED.
--
--     Several thresholds in `settings` carry provisional: true, and some also
--     carry origin: claude_invented. They are honest in the database and
--     invisible on the screen: a page shows "thin market" or "correlation
--     warning" in exactly the same type as a number that came out of the
--     archive, so a reader has no way to tell a measurement from a placeholder
--     that nobody has revisited.
--
--     This is the list, so the UI can say it once, in one place, on every
--     page - rather than each panel having to remember to caveat itself.
--     A setting drops off this list the moment someone sets provisional to
--     false, which is the point: replacing the number is what clears it.
-- --------------------------------------------------------------------------
create or replace view v_provisional_settings as
select
  s.key,
  coalesce(s.value ->> 'origin', 'unknown')                       as origin,
  s.value ->> 'note'                                              as note,
  s.value ->> '_doc'                                              as doc,
  -- what it actually drives, so the reader can judge whether it matters
  case s.key
    when 'risk_limits'                then 'How much of the bankroll a single band, city-day or open book may take, and the daily loss that stops trading.'
    when 'correlation_warn_threshold' then 'How correlated two cities have to be before the desk warns that two positions are one bet.'
    when 'max_slippage_cents'         then 'How far a fill may go against the quote before a signal is abandoned.'
    when 'volume_thresholds'          then 'What counts as a thin market, and how hard illiquidity discounts an opportunity''s rank.'
    when 'weather_alerts'             then 'The temperature moves that raise an alert, and when a day counts as decided.'
    when 'execution_limits'           then 'The venue''s order minimum, share step and price tick - what the exchange will actually accept.'
    else 'Not documented here.'
  end                                                             as drives,
  (s.value ->> 'origin') = 'claude_invented'                      as invented
from settings s
where coalesce((s.value ->> 'provisional')::boolean, false);

comment on view v_provisional_settings is
  'Every threshold currently driving a decision that has no evidential basis - provisional: true in settings. They read on screen exactly like a measured number, so the UI states them once, in one place. Setting provisional to false is what removes a row.';


-- --------------------------------------------------------------------------
-- 5. Grants. Read-only, and the browser needs it on every page.
-- --------------------------------------------------------------------------
do $ad4$
declare o text; r text;
begin
  foreach o in array array['v_data_freshness', 'v_data_health', 'data_freshness_spec',
                           'v_provisional_settings'] loop
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
