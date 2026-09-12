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
      execute format('grant execute on function update_setting(text, jsonb) to %I', r);
    end if;
  end loop;
  if exists (select 1 from pg_roles where rolname = 'service_role') then
    grant select on v_workflow_runs to service_role;
  end if;
end
$ad4$;
