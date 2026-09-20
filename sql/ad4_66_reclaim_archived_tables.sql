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

select cron.schedule(
  'ad4_reclaim_trades_observed',
  '50 6 * * 1',
  'VACUUM (FULL, ANALYZE) public.trades_observed'
);

-- Never reclaimed before, and the largest table on the desk at 102 MB.
-- prune_dead_book_detail nulls a ladder every hour, which frees TOAST that
-- only a rewrite returns - 26 MB of it on the first run.
select cron.schedule(
  'ad4_reclaim_book_snapshots',
  '5 7 * * 1',
  'VACUUM (FULL, ANALYZE) public.book_snapshots'
);

-- Also never reclaimed, and the fourth-largest table at 38 MB growing 2.36 MB
-- a day. Like book_snapshots its dead rows arrive steadily rather than in a
-- daily lump - edge_engine writes one row per band per side every four hours
-- and 121,644 of 136,184 are already superseded - so it joins the weekly set
-- rather than the nightly one. 07:20 keeps it clear of the 08:00 Monday
-- weather model, which is the next thing to touch the database.
select cron.schedule(
  'ad4_reclaim_edges',
  '20 7 * * 1',
  'VACUUM (FULL, ANALYZE) public.edges'
);

-- Did they take, and did the last run work?
--   select jobname, schedule, active from cron.job where jobname like 'ad4_reclaim%';
--   select j.jobname, d.status, d.start_time, d.return_message
--     from cron.job_run_details d join cron.job j using (jobid)
--    where j.jobname like 'ad4_reclaim%' order by d.start_time desc limit 10;
