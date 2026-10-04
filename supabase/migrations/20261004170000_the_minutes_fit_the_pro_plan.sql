-- ===========================================================================
-- THE MINUTES FIT THE PRO PLAN (plan v2 P6.1, 4 Oct 2026)
--
-- Hassan, 4 Oct: the account is on GitHub Pro, 3,000 Actions minutes a month,
-- and the total is to stay under it. Measured from the Actions jobs API (each
-- job rounded up to a whole minute, as GitHub bills it):
--   - scheduled jobs, the full days 1-3 Oct: 114, 116 and 101 billed minutes,
--     about 3,310 a month;
--   - CI (tests.yml + web.yml), 4 Sep - 3 Oct: 2,240.
-- tests/test_github_actions.py holds the figures per workflow and the budget.
--
-- Two cadences change here.
--
-- 1. forecasts.yml leaves the clock. pipeline_daily's first step runs the same
--    script, ingest_forecasts.py, an hour later. On each of the last 7 nights
--    (28 Sep - 4 Oct, ingest_log) that step wrote 0 archive rows: the 03:36
--    chain had already written them. The chain cost 15-25 billed minutes a
--    night (1-4 Oct: 15, 25, 16, 15). The daily step now does that work and
--    still takes the night's newest current-run snapshot. forecasts.yml stays
--    for a backfill started by hand.
--    An empty hours_utc is never due (clock_due: = any('{}') is false), and
--    the row stays because clock_expected_jobs references it.
--
-- 2. pipeline_intraday runs every 6 hours (00, 06, 12, 18 UTC), not every 4
--    (3.95 billed minutes a run, 21 runs 1-4 Oct). The scored record does not
--    depend on it: the hourly tick prices every due checkpoint and re-prices a
--    same-day ladder whose floor moved (scripts/tick.py), and no city has a
--    promoted weather model (v_model_promoted: 0 rows on 4 Oct), so the
--    intraday model forecast prices nothing. It refreshes the board for
--    days with no checkpoint due, the edges and the paper cycle. Every
--    6 hours keeps those within their freshness limits (decisions 8 h, edges and
--    band_probabilities 12 h). 06:36 is the first cycle after pipeline_daily,
--    which it no longer overlaps at 04:36.
--
-- The daily run is expected to log ingest_forecasts. It did so within
-- 105 minutes of each of its 7 dispatches since 28 Sep, so the new
-- expectation reports no past run missing.
--
-- Re-runnable.
-- ===========================================================================

-- tests/test_github_actions.py applies this literal over the seed's
-- (20260926130000) when it counts the scheduled minutes.
update public.clock_schedule s
   set hours_utc  = array(select jsonb_array_elements_text(c.hours_utc)::int),
       updated_at = now()
  from jsonb_to_recordset(
'[{"file": "pipeline_intraday.yml", "hours_utc": [0, 6, 12, 18]}, {"file": "forecasts.yml", "hours_utc": []}]'::jsonb
  ) as c(file text, hours_utc jsonb)
 where s.file = c.file
   and s.hours_utc is distinct from array(select jsonb_array_elements_text(c.hours_utc)::int);

comment on column public.clock_schedule.hours_utc is
  'The UTC hours clock_tick() starts this workflow at: null = every hour, empty = never (started by hand).';

insert into public.clock_expected_jobs (file, job, within_minutes, note) values
  ('pipeline_daily.yml', 'ingest_forecasts', 105,
   'ingest_forecasts.py, the night''s forecast ingest since forecasts.yml left the clock (4 Oct)')
on conflict (file, job) do nothing;
