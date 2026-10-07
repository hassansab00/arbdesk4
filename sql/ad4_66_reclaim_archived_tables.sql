-- ===========================================================================
-- ad4_66_reclaim_archived_tables.sql - THE HALF THAT RETURNS THE MEGABYTES.
--
-- Safe to run any time. Schedules three pg_cron jobs. Run it twice and you
-- get three jobs, not six - cron.schedule replaces a job by name.
--
--
-- A PRUNE DOES NOT SHRINK A DATABASE. That is the part the archive never did.
--
-- `delete` marks rows dead. The pages they occupied stay in the table's file
-- and are reused by future inserts; plain VACUUM makes them reusable and
-- returns nothing to the operating system. pg_database_size - which is the
-- number the 500 MB tier is measured against, and the number on the bill -
-- does not move. Only VACUUM FULL rewrites the table into a fresh file
-- containing just the live rows and hands the old one back.
--
-- archive_observations.py has always ended by printing
--
--     note: run VACUUM FULL weather_observations to return the space to the OS
--
-- and nothing has ever run it. Every committed prune so far was followed by a
-- human remembering, or not. On 14 Sep a real prune moved 292k observation
-- rows to a Release and the database still measured 462 MB afterwards; it
-- reached 436 only after a VACUUM FULL run by hand.
--
--
-- WHY pg_cron AND NOT THE WORKFLOW. VACUUM cannot run inside a transaction
-- block, so it cannot live in a plpgsql function, and PostgREST wraps every
-- RPC in one - which is the only way scripts/ reaches this database. pg_cron
-- executes its command directly and is the one path in this system that can
-- issue it.
--
--
-- WHY SMALLEST FIRST, AND WHY TWENTY MINUTES APART. VACUUM FULL builds the
-- new file BEFORE dropping the old, so during the rebuild the database holds
-- both. Doing trades_observed first, while the database is at its fullest,
-- means a peak of roughly 474 MB against a 500 MB ceiling. Doing forecasts
-- first frees 13 MB before the next one starts, and the worst moment in the
-- whole sequence is about 456 MB:
--
--     forecasts       33 MB -> ~20 MB     peak ~456
--     observations    57 MB -> ~30 MB     peak ~453
--     trades          91 MB -> ~38 MB     peak ~434   ending near 343 MB
--
-- The gaps are so a slow rebuild cannot overlap the next one. Each takes
-- seconds on tables this size; twenty minutes is there to be wrong in.
--
-- It takes an ACCESS EXCLUSIVE lock, so reads of THAT table block while it
-- runs. 04:00-05:00 UTC on a Monday is the quietest hour the desk has: the
-- intraday pipeline fires at 00:15 and 04:15 and touches none of these three.
--
--
-- THE SCHEDULE FOLLOWS THE ARCHIVE. .github/workflows/archive_observations.yml
-- runs 03:00 UTC Monday and takes a few minutes. These start an hour later,
-- so a slow or retried archive still finishes first. If the archive did not
-- run, these are cheap no-ops - VACUUM FULL on a table with nothing dead in
-- it rewrites the same rows and returns the same size.
--
-- WEEKLY RECLAIM AGAINST A DAILY PRUNE IS WHY THE TIER WAS BREACHED.
--
-- Everything above was true and stopped being sufficient. When it was
-- written the archive ran WEEKLY over THREE tables, so one reclaim a week
-- met one prune a week. Since then the archive went daily, and research
-- (17 Sep) and resolution (19 Sep) were added to it. Neither ever got a
-- reclaim job, and the weekly cadence stayed.
--
-- What that costs, measured: research sheds about 7,400 rows a day and
-- resolution about 4,000 at ~7 KB each - roughly 40 MB of dead space per day
-- against a 500 MB free tier. A week of that is 280 MB. The database was
-- measured at 591 MB on 20 Sep, over a limit whose enforcement is read-only
-- mode, and a one-off reclaim of the whole set brought it to 515 MB
-- immediately - 76 MB that was dead pages and nothing else:
--
--     research_captures       68 -> 54 MB      bands            26 -> 10 MB
--     book_snapshots         102 -> 76 MB      weather_obs      38 -> 32 MB
--     trades_observed         60 -> 56 MB      edges            41 -> 38 MB
--
-- So the cadence now follows the prune that feeds it. The two tables the
-- archive prunes EVERY DAY are reclaimed every day, half an hour behind it.
-- The three that rarely shed anything stay weekly, and book_snapshots joins
-- them: its ladders are stripped hourly by prune_dead_book_detail, which
-- makes dead tuples continuously rather than in one daily lump.
--
-- AND OFF THE 04:00 COLLISION. The weekly jobs started at 04:00 Monday, which
-- is exactly when pipeline_daily fires - and that pipeline runs
-- ingest_forecasts.py, which writes weather_forecasts, the first table in the
-- old sequence. An ACCESS EXCLUSIVE lock against a live writer is a stall
-- waiting to happen. 06:20 onward on a Monday is genuinely clear: the 06:07
-- observations collector has finished, the weather model is at 08:00 and the
-- intraday pipeline at 08:15.
-- ===========================================================================

-- DAILY, because the archive prunes these daily. 03:00 archive, 03:30 here:
-- the 20 Sep run took under two minutes, so half an hour is room to be wrong
-- in, and both finish well before the 04:00 daily pipeline.
select cron.schedule(
  'ad4_reclaim_research_captures',
  '30 3 * * *',
  'VACUUM (FULL, ANALYZE) public.research_captures'
);

-- The heaviest of the lot and the reason the tier broke: 86 MB of which 75 is
-- TOASTed Gamma and CLOB payloads. Those megabytes cannot come back until the
-- rows are pruned, and cannot come back from a prune alone.
select cron.schedule(
  'ad4_reclaim_paper_resolution_evidence',
  '35 3 * * *',
  'VACUUM (FULL, ANALYZE) public.paper_resolution_evidence'
);

-- WEEKLY, smallest first, on tables that shed little. The order is the
-- original one and for the original reason: VACUUM FULL holds both the old
-- file and the new one while it rebuilds, so freeing the small tables first
-- lowers the peak the big one has to fit inside.
select cron.schedule(
  'ad4_reclaim_weather_forecasts',
  '20 6 * * 1',
  'VACUUM (FULL, ANALYZE) public.weather_forecasts'
);

select cron.schedule(
  'ad4_reclaim_weather_observations',
  '35 6 * * 1',
  'VACUUM (FULL, ANALYZE) public.weather_observations'
);

-- DAILY SINCE 28 SEP (plan v2 P1.6 phase 1): trades keep one day, so the
-- archive sheds a day of them every night (40,000-57,000 rows at full
-- capture). 03:20, after the 02:36 archive and its own reclaim request, and
-- before the 03:30 research reclaim, outside pipeline_daily's hour.
select cron.schedule(
  'ad4_reclaim_trades_observed',
  '20 3 * * *',
  'VACUUM (FULL, ANALYZE) public.trades_observed'
);

-- DAILY NOW, NOT WEEKLY, and the weekly cadence is what put the tier over.
-- Measured 2026-09-22 with the last run six days old: 22,737 dead rows on
-- book_snapshots and 15,234 on edges, and reclaiming the two of them took the
-- database from 529.4 MB to 499.4 MB - thirty megabytes of a five hundred
-- megabyte plan, sitting in files nothing could read.
--
-- The original comment said their dead rows arrive steadily rather than in a
-- daily lump, and that is still true; what changed is the rate. The
-- observation feed went hourly and the desk now prices 48 cities, so "steady"
-- is five megabytes a day, and a week of steady is a tier.
--
-- 03:45, after the 03:00 archive and the two nightly reclaims that follow it,
-- and well before the 04:00 daily pipeline.
select cron.schedule(
  'ad4_reclaim_book_snapshots',
  '45 3 * * *',
  'VACUUM (FULL, ANALYZE) public.book_snapshots'
);

-- Daily for the same reason and on the same evidence: 15,234 dead rows in six
-- days. edge_engine writes one row per band per side every four hours and
-- most are superseded within the day. 03:50 keeps it behind book_snapshots so
-- the two rewrites never hold their old and new files at the same time.
select cron.schedule(
  'ad4_reclaim_edges',
  '50 3 * * *',
  'VACUUM (FULL, ANALYZE) public.edges'
);

-- Past forecast features (plan v2 P1.6 phase 1, 28 Sep): the archive sheds a
-- day of them every night (~3,300 rows). Daily at 03:15, ahead of the trades
-- backstop, after the 02:36 archive.
select cron.schedule(
  'ad4_reclaim_weather_forecast_features',
  '15 3 * * *',
  'VACUUM (FULL, ANALYZE) public.weather_forecast_features'
);

-- Book proof (plan v2 P1.6 phase 1, 28 Sep): kept one day, so the archive
-- sheds a day of it every night (~340 rows at ~6.4 KB). Daily at 03:25,
-- between the trades and research backstops.
select cron.schedule(
  'ad4_reclaim_paper_book_evidence',
  '25 3 * * *',
  'VACUUM (FULL, ANALYZE) public.paper_book_evidence'
);

-- Signals' decision inputs (plan v2 P1.6 phase 1, 28 Sep): the archive strips
-- a day of them every night (~300 payloads, ~3 KB each on disk); the rows
-- stay. Daily at 03:40, between the observations and edges backstops.
select cron.schedule(
  'ad4_reclaim_signals',
  '40 3 * * *',
  'VACUUM (FULL, ANALYZE) public.signals'
);

-- Each model's forecasts (plan v2 P1.6 phase 2, 29 Sep): pruned by for_date
-- as weather_forecasts is, so reclaimed as it is - weekly, Monday 06:50,
-- after the two other weather tables. Nothing writes the table then (the
-- ingest runs at 03:36 and 04:36).
select cron.schedule(
  'ad4_reclaim_weather_forecast_models',
  '50 6 * * 1',
  'VACUUM (FULL, ANALYZE) public.weather_forecast_models'
);

-- Prices no reader selects, 30 days past their market (plan v2 P1.6 phase 2,
-- step 6, 29 Sep): the archive sheds about a day of them a night and asks for
-- its own reclaim, so this is only the backstop - weekly, Monday 07:05, after
-- the weather tables and outside the hourly tick (:36) and the engine runs.
select cron.schedule(
  'ad4_reclaim_band_probabilities',
  '5 7 * * 1',
  'VACUUM (FULL, ANALYZE) public.band_probabilities'
);

-- Superseded correlations (WXPredict build 2.A, 7 Oct): the archive takes
-- every row but each pair's newest once it is two days old, about 1,326 a
-- night, and asks for its own reclaim, so this is the backstop. Daily, as
-- every dataset kept three days or less is; 03:10, after the 02:36 archive
-- and ahead of the other backstops. Nothing writes the table then (the
-- recompute runs in pipeline_daily, about 05:00).
select cron.schedule(
  'ad4_reclaim_derived_city_correlation',
  '10 3 * * *',
  'VACUUM (FULL, ANALYZE) public.derived_city_correlation'
);

-- The decision log (plan v2 P5.11): about 2,600 rows a day, pruned past 30
-- days. Small, so the rewrite is short; 03:55, behind edges, for the same
-- one-rewrite-at-a-time reason.
select cron.schedule(
  'ad4_reclaim_decisions',
  '55 3 * * *',
  'VACUUM (FULL, ANALYZE) public.decisions'
);

-- Did they take, and did the last run work?
--   select jobname, schedule, active from cron.job where jobname like 'ad4_reclaim%';
--   select j.jobname, d.status, d.start_time, d.return_message
--     from cron.job_run_details d join cron.job j using (jobid)
--    where j.jobname like 'ad4_reclaim%' order by d.start_time desc limit 10;


-- ===========================================================================
-- THE RECLAIM FOLLOWS THE PRUNE BY CONSTRUCTION, NOT BY CLOCK.
--
-- Every schedule above assumes the archive has already pruned by the time it
-- fires: "03:00 archive, 03:30 here". That assumption is about GitHub's clock,
-- and GitHub does not keep it. Measured 2026-09-22: the 03:00 archive cron
-- fired at 08:14. The 03:30 reclaim of research_captures ran first, rewrote a
-- table nothing had been pruned from, and returned nothing - and the 26,734
-- rows the archive did prune at 08:14 sat as dead pages until 03:30 the next
-- day. Two jobs that must run in order were scheduled on two clocks, and only
-- one of the clocks is reliable.
--
-- So the archive now asks for its own reclaim, the moment its prune commits.
-- VACUUM cannot run inside a transaction, and every RPC is one - which is why
-- this does not vacuum. It schedules: cron.schedule is an ordinary function
-- call that inserts a row into cron.job, and pg_cron executes the VACUUM
-- outside any transaction a couple of minutes later.
--
-- The job is named per table and REPLACED by name each time, so there is at
-- most one per table. Its expression pins a minute of a day of a month, which
-- means that if the archive ever stops, the job fires once a year on that
-- date - a VACUUM FULL of a table with nothing dead in it, which rewrites the
-- same rows and changes nothing. The daily schedules above stay as the
-- backstop for a day the archive does not run at all.
--
-- ALLOW-LISTED, because this is a SECURITY DEFINER function that builds a
-- statement from its argument. Only the eight tables the archive prunes can
-- be named, and the name is quoted with %I regardless.
-- ===========================================================================
-- NOT IN THE MIDDLE OF THE DAY (plan v2 P6.5). Two minutes after the prune
-- meant 08:08 on 24 Sep, when GitHub ran the 03:00 archive five hours late:
-- eight tables rewritten at once under 9-24 s ACCESS EXCLUSIVE locks and 14
-- page panels timed out. Straight away only inside 00:00-06:00 UTC, otherwise
-- the next 01:00 UTC, staggered two minutes a table.
create or replace function public.request_reclaim(p_table text)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_allowed constant text[] := array[
    'research_captures', 'paper_resolution_evidence', 'book_snapshots', 'edges',
    'weather_observations', 'weather_forecasts', 'trades_observed', 'decisions',
    -- appended (28 Sep), so every table above keeps its two-minute slot
    'paper_book_evidence', 'weather_forecast_features', 'signals',
    -- appended (29 Sep, P1.6 phase 2)
    'weather_forecast_models', 'band_probabilities',
    -- appended (7 Oct, WXPredict build 2.A)
    'derived_city_correlation'];
  v_now   timestamptz := now();
  v_hour  int := extract(hour from (v_now at time zone 'UTC'))::int;
  v_at    timestamptz;
  v_utc   timestamp;
  v_job   text;
  v_expr  text;
begin
  if p_table is null or not (p_table = any (v_allowed)) then
    raise exception 'request_reclaim: % is not a table the archive prunes', p_table
      using errcode = '22023';
  end if;

  if v_hour < 6 then
    v_at := v_now + make_interval(mins => 2 + 2 * (array_position(v_allowed, p_table) - 1));
  else
    v_at := ((date_trunc('day', v_now at time zone 'UTC') + interval '1 day' + interval '1 hour')
             at time zone 'UTC')
            + make_interval(mins => 2 * (array_position(v_allowed, p_table) - 1));
  end if;
  v_utc := v_at at time zone 'UTC';

  v_job  := 'ad4_reclaim_after_archive_' || p_table;
  v_expr := format('%s %s %s %s *',
                   extract(minute from v_utc)::int, extract(hour  from v_utc)::int,
                   extract(day    from v_utc)::int, extract(month from v_utc)::int);

  perform cron.schedule(v_job, v_expr,
                        format('VACUUM (FULL, ANALYZE) public.%I', p_table));

  return jsonb_build_object('ok', true, 'job', v_job, 'cron', v_expr, 'fires_at', v_at);
end $$;

comment on function public.request_reclaim(text) is
  'Schedules a VACUUM FULL of one archive-pruned table in the next quiet window (straight away inside 00:00-06:00 UTC, otherwise the next 01:00 UTC), staggered two minutes a table, so the space a prune frees comes back the same night without locking pages mid-day (plan v2 P6.5).';

revoke all on function public.request_reclaim(text) from public, anon, authenticated;
grant execute on function public.request_reclaim(text) to service_role;
