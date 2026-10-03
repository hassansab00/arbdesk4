-- ===========================================================================
-- THE LADDER QUEUE IS EXPECTED TO LOG (plan v2.2 P4.7, 3 Oct 2026)
--
-- 20260930100000 left these rows out on purpose: v_run_arrivals joins every
-- dispatch of the last 7 days to the expected jobs and the watchdog counts a
-- miss due in the last 24 h, so rows added before the code ran would have
-- reported every earlier dispatch as a failure. The code has now run for
-- three days (ingest_log, 30 Sep 12:00Z - 3 Oct 16:00Z):
--   pipeline_intraday  19 runs; venue_confirm_queue 19 rows, databank_bands
--                      19 rows, every one ok
--   tick               76 tick rows; P4.7_confirm_recent 76 rows (47 ok,
--                      29 partial), none missing
-- A run that logs neither (the background step killed by its timeout, a
-- crash before log_run) now reads missing, which is a real failure.
--
-- Windows: each workflow's own timeout + at least 10 minutes, the same as the
-- seed's rows for these workflows (tick 15, intraday 45).
-- ===========================================================================

insert into public.clock_expected_jobs (file, job, within_minutes, note) values
  ('tick.yml',              'P4.7_confirm_recent', 15, 'confirm_recent.py, in the background beside the checkpoints'),
  ('pipeline_intraday.yml', 'venue_confirm_queue', 45, 'confirm_queue.py'),
  ('pipeline_intraday.yml', 'databank_bands',      45, 'databank.py --bands-only')
on conflict (file, job) do nothing;
