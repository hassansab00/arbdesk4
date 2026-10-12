-- ===========================================================================
-- The tick has room for WXPredict's shadow (13 Oct).
--
-- wxrd-v2, the remaining-day model that counts a station report 8 minutes
-- after its reading (#394), runs as a step of the hourly tick
-- (tools/wxpredict/wxrd_live.py), beside every same-day call the tick writes.
-- The step has 3 minutes of its own (a cold virtualenv installs numpy, pandas,
-- scipy and lightgbm), so the tick job's timeout goes from 5 to 8 minutes.
--
-- clock_expected_jobs: each of the tick's three jobs was expected within 15
-- minutes of the dispatch; the window must cover the job's own timeout with
-- ten minutes to spare (tests/test_run_arrivals.py), so 20. The rows the
-- jobs write arrive as before (tick.py and the two beside it finish inside
-- the 110 s deadline); only the bound moves. Live before this: all three at
-- 15 (checked 12 Oct ~22:00Z).
--
-- The shadow's own job (P7.6_wxrd_shadow) joins after its first day of runs,
-- as the others did. Re-runnable.
-- ===========================================================================
insert into public.clock_expected_jobs (file, job, within_minutes, note) values
  ('tick.yml', 'tick',                20, 'tick.py'),
  ('tick.yml', 'P0.4_trade_history',  20, 'ingest_trades.py, beside the tick'),
  ('tick.yml', 'P4.7_confirm_recent', 20, 'confirm_recent.py, in the background beside the checkpoints')
on conflict (file, job) do update set within_minutes = excluded.within_minutes, note = excluded.note;
