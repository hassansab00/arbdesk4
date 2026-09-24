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
