# n8n workflow templates

Importable skeletons for the three workflows that genuinely live in n8n
(everything else moved to GitHub Actions - see `docs/n8n_workflows.md`
for the full mapping and rationale).

**Safe to commit**: every `Config` node here has blank
`supabase_url`/`service_key`/`alert_email` values. Fill those in after
import, inside n8n - never re-export and commit a filled-in copy (§7.0
rule 1: it would contain the service key).

## Import

n8n → Workflows → Import from File → pick one of these `.template.json`
files. Node type versions here (`typeVersion`) match a recent n8n release
at the time this was written; if your instance is on a different
version, n8n's import will either auto-upgrade the node or flag it -
neither is destructive, just re-check the flagged node's parameters
against the spec in `docs/n8n_workflows.md` before activating.

- `P1.1_live_weather_alerts.template.json` - webhook-triggered, called by
  `scripts/live_weather.py` when it detects a high/critical event. After
  import and activation, copy the workflow's webhook URL into
  `settings.weather_alert_webhook.url` (via the `update_setting` RPC or
  directly in the SQL editor).
- `P3.1_email_digests.template.json` - morning brief (04:00 UTC) and
  end-of-day report (21:00 UTC), both via `sql/ad4_rpc.sql`'s
  `build_morning_brief`/`build_eod_report` RPCs.
- `P4.1_health_watchdog.template.json` - every 6 hours, emails only on
  failure. The one check it can't do (n8n's own execution count vs. the
  plan limit) is a manual/n8n-side check, noted in the workflow's own
  Evaluate Thresholds node and in `docs/n8n_workflows.md` - Postgres has
  no visibility into n8n's own usage.

## After importing

1. Fill in each workflow's `Config` node.
2. Run once via the Manual Trigger to confirm the Supabase calls succeed
   (401/403 usually means the service key wasn't pasted in, not a bug).
3. Activate the workflow (top-right toggle) so its real trigger
   (Schedule/Webhook) takes over.
