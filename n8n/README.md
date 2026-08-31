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
  `build_morning_brief`/`build_eod_report` RPCs. The morning brief's
  table carries **Depth 5c** and **Vol 24h** beside price and edge, and
  shades any row on a thin-volume band - an edge on a band nobody has
  traded in 24h is a different proposition from the same edge on a busy
  one, and the brief has to say which it is.
- `P4.1_health_watchdog.template.json` - every 6 hours, emails only on
  failure. Five checks: stale book snapshots, stale forecast runs, failed
  ingest jobs, anomalies, and **traded market volume**. The volume check
  exists because none of the others can see it: `book_snapshots` keeps
  updating whether or not anyone is trading, so a desk with live books
  and zero volume looks healthy to every other check while being
  untradeable. It fails on zero trades across all cities in 24h, and
  reports the total either way as a context line on the alert email, so
  a clean run still tells you the size of the market you are trading
  into. The one check it can't do (n8n's own execution count vs. the
  plan limit) is a manual/n8n-side check, noted in the workflow's own
  Evaluate Thresholds node and in `docs/n8n_workflows.md` - Postgres has
  no visibility into n8n's own usage.

## After importing

1. Fill in each workflow's `Config` node.
2. Run once via the Manual Trigger to confirm the Supabase calls succeed
   (401/403 usually means the service key wasn't pasted in, not a bug).
3. Activate the workflow (top-right toggle) so its real trigger
   (Schedule/Webhook) takes over.

## Where these fit in the first run

`docs/GO_LIVE.md` step 5 walks the import, the Config fields, the test
execution and the expected result for each of the three, in order. Import
them after the SQL and the GitHub Actions workflows are working - all
three read data those produce.
