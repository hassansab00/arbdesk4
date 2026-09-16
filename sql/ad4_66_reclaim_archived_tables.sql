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
-- ===========================================================================

select cron.schedule(
  'ad4_reclaim_weather_forecasts',
  '0 4 * * 1',
  'VACUUM (FULL, ANALYZE) public.weather_forecasts'
);

select cron.schedule(
  'ad4_reclaim_weather_observations',
  '20 4 * * 1',
  'VACUUM (FULL, ANALYZE) public.weather_observations'
);

select cron.schedule(
  'ad4_reclaim_trades_observed',
  '40 4 * * 1',
  'VACUUM (FULL, ANALYZE) public.trades_observed'
);

-- Did they take, and did the last run work?
--   select jobname, schedule, active from cron.job where jobname like 'ad4_reclaim%';
--   select j.jobname, d.status, d.start_time, d.return_message
--     from cron.job_run_details d join cron.job j using (jobid)
--    where j.jobname like 'ad4_reclaim%' order by d.start_time desc limit 10;
