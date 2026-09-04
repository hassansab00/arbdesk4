-- ===========================================================================
-- ad4_20_schedules.sql - own your n8n execution budget from the UI.
--
-- n8n bills by execution. AD4's nine workflows carry hard-coded schedules
-- inside their own JSON, so changing one means opening n8n, finding the
-- Schedule Trigger, editing it, and saving - per workflow. In practice that
-- means nobody changes them, and the plan is spent on jobs nobody needed that
-- often.
--
-- This makes the cadence DATA. The Workflows page reads and writes it, and
-- each workflow's own Schedule Trigger checks it on entry: a workflow whose
-- schedule says "manual" exits immediately, costing one trivial execution
-- instead of a full run. That is the honest limit of what can be done from
-- outside n8n - a schedule trigger still fires - and it is the difference
-- between a run that fetches 54 cities and one that stops at the first node.
--
-- Run order: after sql/ad4_19_stats_cache.sql. Re-runnable.
-- ===========================================================================

insert into settings (key, value)
values ('workflow_schedules', '{
  "note": "Per-workflow cadence, owned here rather than inside each n8n file. mode: auto | manual | off. every_minutes is what the workflow enforces when mode=auto; its own Schedule Trigger should fire at or below this and the workflow skips runs that arrive early. Set manual to run only from the Run buttons in the UI.",
  "P0.2_market_discovery":     {"mode": "auto",   "every_minutes": 360},
  "P0.3_book_volume_snapshot": {"mode": "auto",   "every_minutes": 60},
  "P0.4_trade_history":        {"mode": "auto",   "every_minutes": 360},
  "P0.5_refresh_rules_text":   {"mode": "auto",   "every_minutes": 1440},
  "P1.1_live_weather_alerts":  {"mode": "auto",   "every_minutes": 0},
  "P1.2_nws_monitor":          {"mode": "auto",   "every_minutes": 120},
  "P1.3_nws_forecast":         {"mode": "auto",   "every_minutes": 360},
  "P3.1_email_digests":        {"mode": "auto",   "every_minutes": 720},
  "P4.1_health_watchdog":      {"mode": "auto",   "every_minutes": 360}
}'::jsonb)
on conflict (key) do nothing;

-- Let the UI write it.
update settings
   set value = value || '["workflow_schedules"]'::jsonb
 where key = 'ui_editable_keys'
   and jsonb_typeof(value) = 'array'
   and not (value @> '["workflow_schedules"]'::jsonb);


-- --------------------------------------------------------------------------
-- Should this workflow run right now?
--
-- Called by each workflow's first node. Returns the decision AND the reason,
-- so a skipped run says why in its own log rather than looking like a failure.
--
-- The last run is read from ingest_log, which every workflow already writes -
-- no new bookkeeping, and it stays correct whether the last run came from a
-- schedule, the UI, or a hand-click inside n8n.
-- --------------------------------------------------------------------------
create or replace function should_run(p_job text, p_trigger text default 'schedule')
returns jsonb language plpgsql security definer as $ad4$
declare
  v_cfg     jsonb;
  v_mode    text;
  v_every   int;
  v_last    timestamptz;
  v_mins    numeric;
begin
  select value -> p_job into v_cfg from settings where key = 'workflow_schedules';

  -- Unknown job, or no configuration at all: run. A missing setting must never
  -- silently disable a workflow.
  if v_cfg is null then
    return jsonb_build_object('run', true, 'reason', 'no schedule configured for ' || p_job);
  end if;

  v_mode  := coalesce(v_cfg ->> 'mode', 'auto');
  v_every := coalesce((v_cfg ->> 'every_minutes')::int, 0);

  -- A run the operator asked for always happens, whatever the schedule says.
  if p_trigger is distinct from 'schedule' then
    return jsonb_build_object('run', true, 'reason', 'manual trigger: ' || p_trigger);
  end if;

  if v_mode = 'off' then
    return jsonb_build_object('run', false, 'reason', p_job || ' is switched off');
  end if;
  if v_mode = 'manual' then
    return jsonb_build_object('run', false,
      'reason', p_job || ' is manual-only - use the Run button');
  end if;
  if v_every <= 0 then
    return jsonb_build_object('run', true, 'reason', 'no minimum interval set');
  end if;

  select max(logged_at) into v_last from ingest_log where job = p_job and status <> 'skipped';
  if v_last is null then
    return jsonb_build_object('run', true, 'reason', 'never run');
  end if;

  v_mins := extract(epoch from (now() - v_last)) / 60.0;
  if v_mins < v_every then
    return jsonb_build_object(
      'run', false,
      'reason', format('%s ran %s min ago; minimum interval is %s min',
                       p_job, round(v_mins), v_every),
      'minutes_since', round(v_mins), 'every_minutes', v_every);
  end if;

  return jsonb_build_object('run', true, 'reason', format('%s min since last run', round(v_mins)),
                            'minutes_since', round(v_mins), 'every_minutes', v_every);
end;
$ad4$;


-- --------------------------------------------------------------------------
-- Executions per month at the current settings, so the budget is visible
-- BEFORE the plan runs out rather than after.
-- --------------------------------------------------------------------------
create or replace view v_execution_budget as
with cfg as (
  select k as job, v ->> 'mode' as mode, coalesce((v ->> 'every_minutes')::int, 0) as every_minutes
  from settings s, lateral jsonb_each(s.value) e(k, v)
  where s.key = 'workflow_schedules' and jsonb_typeof(v) = 'object'
)
select
  job, mode, every_minutes,
  case
    when mode <> 'auto' then 0
    when every_minutes <= 0 then null            -- event-driven; not a cadence
    else round(43200.0 / every_minutes)::int      -- minutes in a 30-day month
  end as runs_per_month
from cfg
order by case when mode <> 'auto' then 1 else 0 end,
         coalesce(round(43200.0 / nullif(every_minutes, 0)), 0) desc;


do $ad4$
declare r text;
begin
  foreach r in array array['anon', 'authenticated', 'service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_execution_budget to %I', r);
    end if;
  end loop;
  -- should_run is read-only and safe for the browser to call, so the UI can
  -- show what a schedule change would do before it is saved.
  grant execute on function should_run(text, text) to anon, authenticated, service_role;
end
$ad4$;

do $ad4$
declare v_total int;
begin
  select coalesce(sum(runs_per_month), 0) into v_total from v_execution_budget;
  raise notice 'ad4_20: scheduled executions/month at current settings: %', v_total;
  raise notice 'ad4_20: set a workflow to manual on the Workflows page to stop it running on its own.';
end
$ad4$;
