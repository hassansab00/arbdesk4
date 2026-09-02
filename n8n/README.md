# n8n workflow files

**Setup instructions: `docs/n8n_setup.md`.** Read that, not this.

7 files. All rebuilt to run three ways - their own schedule, the Execute
button in n8n, or the Run button on the AD4 Workflows page - and all of them
write a row to `ingest_log` when they finish, so `v_workflow_runs` shows the
last run of each whichever way it was started.

> **P0.2-P0.5 already run in your n8n.** These are new versions of those same
> four. Importing and activating them alongside the originals means two
> workflows writing the same tables. Import them OFF, test, then swap one at
> a time - `docs/n8n_setup.md` step 5.

| Workflow | Webhook path |
|---|---|
| P0.2 Market Discovery | `/webhook/ad4-market-discovery` |
| P0.3 Book + Volume Snapshot | `/webhook/ad4-book-snapshot` |
| P0.4 Trade History | `/webhook/ad4-trade-history` |
| P0.5 Refresh Rules Text | `/webhook/ad4-refresh-rules` |
| P1.1 Live Weather Alerts | `/webhook/ad4-weather-alert` |
| P3.1 Email Digests | `/webhook/ad4-email-digests` - body `{"digest":"morning"|"eod"}` |
| P4.1 Health Watchdog | `/webhook/ad4-health-watchdog` |

**Safe to commit**: every `Config` node ships with blank
`supabase_url`/`service_key`/`alert_email`. Fill them in after import, inside
n8n - never re-export and commit a filled-in copy (it would contain the
service key). Use `python scripts/sanitise_n8n_export.py <export>.json n8n/`
if you ever do need to capture one back.

## The seven AD4 workflows

| # | Workflow | Trigger | File | Status |
|---|---|---|---|---|
| P0.2 | Market Discovery | schedule | `P0.2_market_discovery.scaffold.json` | **scaffold** |
| P0.3 | Book + Volume Snapshot | schedule | `P0.3_book_volume_snapshot.scaffold.json` | **scaffold** |
| P0.4 | Trade History | schedule | `P0.4_trade_history.scaffold.json` | **scaffold** |
| P0.5 | Refresh Rules Text | schedule | `P0.5_refresh_rules_text.scaffold.json` | **scaffold** |
| P1.1 | Live Weather Alerts (notify half) | webhook | `P1.1_live_weather_alerts.template.json` | template |
| P3.1 | Email Digests | schedule ×2 | `P3.1_email_digests.template.json` | template |
| P4.1 | Health Watchdog | schedule | `P4.1_health_watchdog.template.json` | template |

Everything else the spec names (P2.1 Probability + Edge, P2.2 Signals,
P2.3 Settlement, P2.4 Derived Recompute, the backtest runner) runs as a
GitHub Action, not in n8n — see `docs/n8n_workflows.md` for why.

### template vs scaffold

**`.template.json`** — built and checked against this repo's own schema and
RPCs. Import, fill in Config, use.

**`.scaffold.json`** — the four P0.x workflows **already exist and run in
Hassan's n8n**; they were never captured here. These are reconstructions:
the Supabase half is grounded (table and column names come from
`sql/ad4_00_preflight.sql`, verified against a real Postgres), but the
Polymarket endpoint is **not** — this repo contains exactly one Polymarket
URL, in `scripts/settlement.py`, itself flagged unverified. So each
scaffold takes the endpoint as a **Config field** rather than asserting one.

> **Do not activate a scaffold alongside the P0.x workflow it reconstructs.**
> Two copies writing the same tables is worse than one.

Use a scaffold as a rebuild reference or to diff against the original.

### Better: capture the real four

```bash
# n8n -> open the workflow -> ... menu -> Download
python scripts/sanitise_n8n_export.py ~/Downloads/My_Workflow.json n8n/
```

That blanks every Config value, strips bound credentials, removes
instance metadata, forces `active: false`, and redacts anything
secret-shaped anywhere in the file — including a key hardcoded inside a
Code node, which is the easiest one to miss. If a secret-shaped string
survives, it **refuses to write** and tells you which node it is in.

The real workflows beat the reconstructions. Once captured, delete the
matching scaffold.

## Import

n8n → Workflows → Import from File → pick one of these `.template.json`
files. Node type versions here (`typeVersion`) match a recent n8n release
at the time this was written; if your instance is on a different
version, n8n's import will either auto-upgrade the node or flag it -
neither is destructive, just re-check the flagged node's parameters
against the spec in `docs/n8n_workflows.md` before activating.

### P0.4 is the one to check first

`trades_observed` is the only source of market volume in AD4 —
`v_band_volume`, `v_city_volume`, the thin-market flag on every band, the
`volume/(volume+k)` liquidity factor in the opportunity ranking, the
calculator's volume warning, the Goals feasibility read and the P4.1
watchdog's no-volume alarm all derive from it. If P0.4 is not running,
none of that breaks loudly — every figure reads **$0** and every band shows
as **thin**. Honest, but useless.

```sql
select count(*) as trades, max(observed_at) as newest from trades_observed;
select coalesce(sum(volume_usd),0) as vol_24h from v_city_volume;
```

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
