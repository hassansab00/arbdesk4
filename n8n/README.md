# n8n workflow files

**Setup instructions: `docs/n8n_setup.md`.** Read that, not this.

10 workflow files, plus one snippet. All built to run three ways - their own schedule, the Execute
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
| P1.2 NWS Monitor | `/webhook/ad4-nws-monitor` |
| P1.3 NWS Forecast | `/webhook/ad4-nws-forecast` |
| P1.4 NWS Gridpoint | `/webhook/ad4-nws-gridpoint` |
| P3.1 Email Digests | `/webhook/ad4-email-digests` - body `{"digest":"morning"|"eod"}` |
| P4.1 Health Watchdog | `/webhook/ad4-health-watchdog` |

**Safe to commit**: every `Config` node ships with blank
`supabase_url`/`service_key`/`alert_email`. Fill them in after import, inside
n8n - never re-export and commit a filled-in copy (it would contain the
service key). Use `python scripts/sanitise_n8n_export.py <export>.json n8n/`
if you ever do need to capture one back.

## The ten AD4 workflows

| # | Workflow | Trigger | File | Status |
|---|---|---|---|---|
| P0.2 | Market Discovery | schedule | `P0.2_market_discovery.scaffold.json` | **scaffold** |
| P0.3 | Book + Volume Snapshot | schedule | `P0.3_book_volume_snapshot.scaffold.json` | **scaffold** |
| P0.4 | Trade History | schedule | `P0.4_trade_history.scaffold.json` | **scaffold** |
| P0.5 | Refresh Rules Text | schedule | `P0.5_refresh_rules_text.scaffold.json` | **scaffold** |
| P1.1 | Live Weather Alerts (notify half) | webhook | `P1.1_live_weather_alerts.template.json` | template |
| P1.2 | NWS Monitor | schedule 2h | `P1.2_nws_monitor.template.json` | template |
| P1.3 | NWS Forecast | schedule 6h | `P1.3_nws_forecast.template.json` | template |
| P1.4 | NWS Gridpoint | schedule 6h | `P1.4_nws_gridpoint.template.json` | template |
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

### `schedule_gate.snippet.json` - the gate, without the swap

The four P0.x that really run in Hassan's n8n predate `should_run()`, so
nothing in `settings.workflow_schedules` controls them. The obvious fix -
import the scaffolds instead - trades a **working** ingest for a
**reconstruction whose Polymarket endpoints were never verified against it**.
That is a bad trade and this file is the alternative.

It is a fragment, not a workflow: four nodes (*Gate config → Check schedule →
Run now? → Stop if skipped*) meant to be copied and pasted onto an existing
canvas, then wired between that workflow's Schedule Trigger and its first
working node. It carries its own two credential boxes rather than reading a
`Config` node, because it cannot know what the host workflow named its own.

`docs/DO_THIS_NOW.md` section C2 has the five steps.

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
- `P1.2_nws_monitor.template.json` - every 2 hours. Reads
  **api.weather.gov**, the National Weather Service's own JSON API, which is
  the service most of these markets settle on. The repo previously reached it
  by scraping an HTML page and that parser was never finished, so every live
  reading silently fell through to IEM. Three calls per US city: the
  observations of the **last 26 hours** (each with its quality-control flag
  and the station's own 24-hour maximum), any active heat advisory or
  warning, and today's **solar transit** - the sun's zenith, which is the
  physical anchor of the daily peak. It asks for the series, not
  `/observations/latest`, because these markets settle on a daily
  **maximum** and one reading can never produce one: taken every 2 hours,
  `latest` would miss the peak on most days and there would be nothing to
  go back to. The newest reading in the series drives `live_weather`;
  every reading is archived.
  Observations are written with `source='NWS'` **beside** the IEM rows for the
  same instant, not instead of them: the unique key is
  `(city_key, valid_at, source)`, so the two feeds coexist and can be
  compared - which is the evidence the settlement-source gate has been waiting
  for. Alerts land in `weather_events` as `kind='nws_alert'`, so P1.1 emails
  them and the UI feed shows them with no new plumbing, and an alert only
  raises an event when it **changes**, so re-running does not re-send it.
  A non-US city 404s, is marked `nws_supported = false`, and is skipped from
  then on. api.weather.gov imposes no rate limit, but **n8n does**: 30-minute
  polling would be 1,440 executions/month against a ~2,000 cap with ~930
  already committed, so the schedule is 2h (360) and the Workflows page's Run
  button covers refreshing sooner. Genuine 30-minute freshness belongs in a
  GitHub Action, which has no per-run quota.
- `P1.3_nws_forecast.template.json` - every 6 hours. The **second forecast
  model**. `v_forecast_divergence` measures how far apart two models are about
  the same day and `probability_engine.py` widens sigma by that spread - but
  with only Open-Meteo in `weather_forecasts` the spread is always 0 and the
  whole mechanism is dormant. This writes NWS's hourly gridpoint forecast as
  `model='nws'` and switches it on. The multiplier has a **floor of 1.0**: a
  second opinion can only ever make AD4 less confident than its measured
  historical skill says, never more. A day is only written if its hourly
  series actually covers the 12:00-18:00 local peak window - a day cut short
  at either end has a max below the real one, and a too-low max would
  manufacture disagreement that is not there.
- `P1.4_nws_gridpoint.template.json` - every 6 hours. P1.3 forecasts the
  **temperature**; this forecasts the **conditions that move** it. Cloud
  cover, dewpoint depression, wind, precipitation and the morning
  temperature are what `sql/ad4_21_weather_features.sql` measured on
  observed days (clear skies climb +11.4C from the morning reading,
  overcast +2.8), and api.weather.gov forecasts every one of them on the
  raw `/gridpoints/{wfo}/{x},{y}` endpoint. The columns it writes into
  `weather_forecast_features` **match `v_city_day_features` by name**, so
  the model `scripts/weather_model.py` fits on observed conditions applies
  to forecast ones with no translation - rename a column on either side and
  the two silently decouple, which is what
  `test_columns_match_the_observed_feature_names` exists to catch. Two unit traps it handles: gridpoint
  `validTime` is an **ISO interval** (`2026-09-04T12:00:00+00:00/PT6H`),
  which has to be expanded to the hours it covers, and an **accumulating**
  series (precipitation, snowfall, ice) carries a total for the whole block
  where an instantaneous one (temperature, sky cover) carries a level - so
  the total is divided across its hours and the level is not. Divide a
  30C six-hour temperature and you get 5C; don't divide a 1-inch six-hour
  QPF and you get 6 inches.
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
execution and the expected result for each one, in order. Import them
after the SQL and the GitHub Actions workflows are working - they read
data those produce.
