-- ===========================================================================
-- ad4_90_clock.sql - THE DESK'S CLOCK IS AN n8n WORKFLOW (plan v2 P6.1)
--
-- GitHub's cron started the hourly tick once in its first seven slots
-- (11:35-17:35Z, 24 Sep); the clock runs at :36 and none of intraday's 00:15, 08:15 and 16:15 runs
-- that day. n8n's schedules fired on the minute all week. So tick.yml and
-- pipeline_intraday.yml lost their GitHub crons and are dispatched by
-- n8n/P6.1_clock.template.json instead.
--
-- A fresh install gets the row from sql/ad4_20_schedules.sql; this adds it to
-- a database that already has workflow_schedules (ad4_20 never overwrites).
--
-- This registers the clock like every other workflow: a row in
-- settings.workflow_schedules, which is what the Workflows page edits and what
-- should_run() gates on. every_minutes 55, not 60: the gate compares against
-- the last run, and an hourly trigger that fires a few seconds early must not
-- be refused. Pausing it here pauses every tick and every intraday run.
--
-- Re-runnable: only adds the key when it is missing.
-- ===========================================================================
do $$ begin
  if to_regclass('public.settings') is null then return; end if;
  update public.settings set value = value || jsonb_build_object('P6.1_clock',
    jsonb_build_object('mode', 'auto', 'every_minutes', 55))
    where key = 'workflow_schedules' and not (value ? 'P6.1_clock');
end $$;

-- AND WHAT THE CLOCK LETS n8n STOP DOING (plan v2 P6.2: the instance must
-- come in under 1,800 executions a month; measured ~100 a day on 24 Sep).
--   P1.6 IEM observations: OFF. The hourly tick reads every station itself
--        (scripts/tick.py read_stations), so its 720 executions a month go.
--        The workflow is unpublished in n8n; its template stays, so it can
--        come back as a manual backfill.
--   P0.3 book snapshots: every 2 hours (the trigger changes in n8n); the gate
--        moves to 110 minutes so an on-time run is never skipped. -360 a month.
do $$ begin
  if to_regclass('public.settings') is null then return; end if;
  update public.settings
     set value = value
       || jsonb_build_object('P1.6_iem_observations', jsonb_build_object('mode', 'off', 'every_minutes', 50))
       || jsonb_build_object('P0.3_book_volume_snapshot', jsonb_build_object('mode', 'auto', 'every_minutes', 110))
   where key = 'workflow_schedules';
end $$;
