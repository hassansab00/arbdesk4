-- ===========================================================================
-- ad4_14_workflows.sql - let the UI run the n8n workflows and see how they went.
--
-- Three things, all additive:
--
--   1. settings.n8n_webhooks - where each workflow's production webhook URL
--      lives, so the Workflows page can fire it. Seeded empty; you paste the
--      URLs in from the UI after importing the workflows.
--
--   2. update_setting's whitelist becomes data-driven. It was a hard-coded
--      IN-list, so every new UI-editable key meant editing the function.
--      It now reads settings.ui_editable_keys and falls back to the original
--      list when that key is absent - adding a key is a settings write, not
--      a schema change.
--
--   3. v_workflow_runs - the latest ingest_log row per job, which is what the
--      workflows now write to on every run (n8n "Log run" node -> log_ingest).
--
-- Run order: after sql/ad4_13_reconcile.sql. Re-runnable. Every statement is
-- independent - no temp tables, no cross-statement state.
--
-- SECURITY NOTE, read this before pasting a webhook URL in:
-- settings is anon-readable, so anything with your publishable key can read
-- these URLs and POST to them. n8n webhook paths are unguessable, and every
-- workflow here is idempotent, so the realistic worst case is someone burning
-- n8n executions - not corrupting data. If that is not a trade you want, leave
-- n8n_webhooks empty and run the workflows from inside n8n instead; the
-- Workflows page degrades to read-only status and says so.
-- ===========================================================================


-- --------------------------------------------------------------------------
-- 1. Which settings keys the UI may write. Seeded with the original
--    hard-coded list plus the two the Workflows and Board pages need.
-- --------------------------------------------------------------------------
insert into settings (key, value)
values ('ui_editable_keys', '[
  "bankroll", "auto_approve", "max_slippage_cents",
  "tradeability_yes", "tradeability_no", "risk_limits",
  "correlation_warn_threshold", "weather_alerts", "email_recipient", "goal",
  "volume_thresholds", "n8n_webhooks"
]'::jsonb)
on conflict (key) do nothing;


-- --------------------------------------------------------------------------
-- 2. Where the workflow webhooks live. One key per workflow, empty until you
--    paste the production URLs in. The paths are fixed by the workflow files
--    (n8n/*.json), so a URL is always <your n8n host>/webhook/<path>.
-- --------------------------------------------------------------------------
insert into settings (key, value)
values ('n8n_webhooks', '{
  "note": "Production webhook URLs, one per workflow. Paste from n8n after import: open the workflow, click the Webhook Trigger node, copy the Production URL. Empty means the Run button is disabled for that workflow.",
  "P0.2_market_discovery":     {"url": "", "path": "ad4-market-discovery",  "label": "Market Discovery"},
  "P0.3_book_volume_snapshot": {"url": "", "path": "ad4-book-snapshot",     "label": "Book + Volume Snapshot"},
  "P0.4_trade_history":        {"url": "", "path": "ad4-trade-history",     "label": "Trade History"},
  "P0.5_refresh_rules_text":   {"url": "", "path": "ad4-refresh-rules",     "label": "Refresh Rules Text"},
  "P1.1_live_weather_alerts":  {"url": "", "path": "ad4-weather-alert",     "label": "Live Weather Alerts"},
  "P1.2_nws_monitor":          {"url": "", "path": "ad4-nws-monitor",      "label": "NWS Monitor"},
  "P1.3_nws_forecast":         {"url": "", "path": "ad4-nws-forecast",     "label": "NWS Forecast"},
  "P3.1_email_digests":        {"url": "", "path": "ad4-email-digests",     "label": "Email Digests"},
  "P2.2_paper_maintenance":    {"url": "", "path": "ad4-paper-maintenance", "label": "Paper Maintenance"},
  "P4.1_health_watchdog":      {"url": "", "path": "ad4-health-watchdog",   "label": "Health Watchdog"}
}'::jsonb)
on conflict (key) do nothing;


-- --------------------------------------------------------------------------
-- 3. update_setting, with the whitelist read from settings instead of frozen
--    into the function body. Same refusal behaviour, same return shape.
-- --------------------------------------------------------------------------
create or replace function update_setting(p_key text, p_value jsonb) returns jsonb
language plpgsql security definer as $ad4$
declare
  v_allowed text[];
begin
  -- data-driven whitelist; the hard-coded list is the fallback, so a database
  -- that never ran this file behaves exactly as it did before.
  select array_agg(x) into v_allowed
  from settings s, lateral jsonb_array_elements_text(s.value) x
  where s.key = 'ui_editable_keys' and jsonb_typeof(s.value) = 'array';

  v_allowed := coalesce(v_allowed, array[
    'bankroll', 'auto_approve', 'max_slippage_cents', 'tradeability_yes',
    'tradeability_no', 'risk_limits', 'correlation_warn_threshold',
    'weather_alerts', 'email_recipient', 'goal'
  ]);

  if not (p_key = any(v_allowed)) then
    return jsonb_build_object('ok', false,
      'error', 'key not editable from the UI: ' || p_key,
      'editable', to_jsonb(v_allowed));
  end if;

  insert into settings (key, value) values (p_key, p_value)
  on conflict (key) do update set value = excluded.value;
  return jsonb_build_object('ok', true);
end;
$ad4$;


-- --------------------------------------------------------------------------
-- 4. The latest run of each workflow. Every workflow's "Log run" node calls
--    log_ingest(job, status, rows, detail) at the end of every execution -
--    scheduled, manual or webhook - so this is the one place to look for
--    "did it run, when, and did it work".
--
--    detail.trigger says which of the three fired it.
-- --------------------------------------------------------------------------
create or replace view v_workflow_runs as
select distinct on (l.job)
  l.job,
  l.status,
  l."rows",
  l.detail,
  l.logged_at,
  l.detail->>'trigger'  as trigger,
  l.detail->>'summary'  as summary
from ingest_log l
where l.job is not null
order by l.job, l.logged_at desc;


-- --------------------------------------------------------------------------
-- 5. Grants. Section 8 of ad4_13_reconcile.sql revoked everything and granted
--    back a fixed list, so a view created after it needs SELECT re-granted.
-- --------------------------------------------------------------------------
do $ad4$
declare r text;
begin
  foreach r in array array['anon','authenticated'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_workflow_runs to %I', r);
    end if;
  end loop;
  -- update_setting is a write: service_role only, reached through the site's
  -- operator route (plan v2 P1.2, 20260923130000_writes_need_an_operator.sql).
  revoke execute on function update_setting(text, jsonb) from public;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant execute on function update_setting(text, jsonb) to service_role;
  end if;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant select on v_workflow_runs to service_role;
  end if;
end
$ad4$;


-- --------------------------------------------------------------------------
-- 6. THE WORKFLOW THAT FIRES ON TIME AND DOES NOTHING.
--
--    Every n8n workflow's first node calls should_run(job, 'schedule'), and a
--    refusal is not a failure: the execution logs `skipped` and finishes
--    green. That is correct behaviour and it is also a perfect hiding place.
--
--    Found on 2026-09-22. P1.6_iem_observations had been moved to an hourly
--    Schedule Trigger and activated, and its n8n executions read `success`
--    every hour at :10 - two seconds each, because the gate still said
--    {"mode": "manual"} and every run stopped at node four. The observation
--    feed sat 104 minutes stale with 45 of 48 cities last seen four hours
--    ago, while every dashboard that asks "did it run" said yes.
--
--    Nothing in the stack could see it. v_workflow_runs shows the LATEST run
--    per job, and one skipped run is ordinary. What is not ordinary is a job
--    skipping every scheduled attempt for hours: that means the trigger and
--    the gate disagree about whether this workflow exists.
--
--    THE TWO HALVES HAVE DIFFERENT OWNERS, which is why they drift. The
--    cadence lives in n8n (a Schedule Trigger inside a workflow file); the
--    permission lives here (settings.workflow_schedules, editable from the
--    UI). Changing one is not changing the other, and no error is raised
--    when they contradict - so the contradiction has to be a row somebody
--    can read.
--
--    Idempotent: one view, no writes.
-- --------------------------------------------------------------------------
create or replace view v_workflow_gate_health as
with spec as (
  select e.key                                                as job,
         coalesce(e.value ->> 'mode', 'auto')                 as mode,
         coalesce((e.value ->> 'every_minutes')::int, 0)      as every_minutes
    from settings s
    cross join lateral jsonb_each(s.value) e
   where s.key = 'workflow_schedules'
     and jsonb_typeof(e.value) = 'object'
),
runs as (
  select job,
         max(logged_at)                                       as last_attempt,
         max(logged_at) filter (where status <> 'skipped')    as last_real_run,
         count(*) filter (where status = 'skipped'
                            and logged_at > now() - interval '24 hours') as skipped_24h,
         count(*) filter (where status <> 'skipped'
                            and logged_at > now() - interval '24 hours') as ran_24h
    from ingest_log
   where job is not null
     and logged_at > now() - interval '7 days'
   group by job
)
select
  sp.job,
  sp.mode,
  sp.every_minutes,
  r.last_attempt,
  r.last_real_run,
  coalesce(r.skipped_24h, 0)                                  as skipped_24h,
  coalesce(r.ran_24h, 0)                                      as ran_24h,
  case when r.last_real_run is null then null
       else round(extract(epoch from (now() - r.last_real_run)) / 60.0)::int
  end                                                         as minutes_since_real_run,
  -- THE ONE THAT MATTERS is the second arm. Three or more scheduled attempts
  -- refused in a day is a Schedule Trigger firing into a closed gate - the
  -- workflow is on in n8n and off here, and it reports success either way.
  case
    when sp.mode = 'off'                                      then 'switched off deliberately'
    when sp.mode = 'manual' and coalesce(r.skipped_24h, 0) >= 3
         then format('GATED: its schedule fired %s times in 24h and the gate refused every one - '
                     || 'n8n thinks this workflow is on, settings.workflow_schedules says manual',
                     r.skipped_24h)
    when sp.mode = 'manual'                                   then 'manual-only, and nothing is trying to run it'
    when r.last_real_run is null                              then 'auto, but it has never completed a run'
    when sp.every_minutes > 0
     and extract(epoch from (now() - r.last_real_run)) / 60.0 > 3 * sp.every_minutes
         then format('STALLED: auto every %s min, last completed run was %s min ago',
                     sp.every_minutes,
                     round(extract(epoch from (now() - r.last_real_run)) / 60.0))
    else                                                           'running'
  end                                                         as verdict,
  (sp.mode = 'manual' and coalesce(r.skipped_24h, 0) >= 3)    as gate_contradicts_trigger
from spec sp
left join runs r on r.job = sp.job
order by (sp.mode = 'manual' and coalesce(r.skipped_24h, 0) >= 3) desc,
         sp.job;

comment on view v_workflow_gate_health is
  'Whether each n8n workflow''s Schedule Trigger and its should_run() gate agree. A refused scheduled run logs `skipped` and finishes green, so a workflow can fire on time for days and do nothing - which is how P1.6 left the observation feed 104 minutes stale while every execution read success.';

do $ad4$
declare r text;
begin
  foreach r in array array['anon','authenticated','service_role'] loop
    if exists (select 1 from pg_roles where rolname = r) then
      execute format('grant select on v_workflow_gate_health to %I', r);
    end if;
  end loop;
end
$ad4$;
