# n8n workflow files

**12 workflow files, plus one snippet.** All run three ways — their own
schedule, the Execute button in n8n, or the Run button on the AD4 Workflows
page — and all write a row to `ingest_log` when they finish, so
`v_workflow_runs` shows the last run of each however it was started.

## Building n8n from scratch: import in this order

### First, make the credential — once, not twelve times

The service key is **no longer in these files**. Every Supabase node
authenticates with an n8n credential instead:

**n8n → Credentials → New → Supabase API**

| Field | Value |
|---|---|
| **Name** | **`AD4 Supabase`** — this name exactly |
| Host | `https://<your-project-ref>.supabase.co` |
| Service Role Secret | Supabase → Project Settings → API → `service_role` (or a key beginning `sb_secret_`) |

**Do this before importing anything.** All 57 Supabase nodes across the twelve
workflows are already bound to a credential named `AD4 Supabase`, and n8n links
a reference by name when it does not recognise the id — which it never does on
a fresh instance. Create it first and every node comes in wired. Import first
and the nodes come in unbound; you then pick the credential on each one, which
works but is 57 dropdowns.

### And, if you want the emails, one more

P1.1, P3.1 and P4.1 send mail. Same pattern:

**n8n → Credentials → New → SMTP** — name it **`AD4 SMTP`**, exactly. Those
three workflows' Send Email nodes are already bound to that name.

### Then, per workflow

Fill **Config → `supabase_url`** (the same host; an expression has to build the
request URL and cannot read the credential's host, so it stays). On P1.1, P3.1
and P4.1 also fill **`from_email`** — an address the SMTP account is allowed to
send as; no provider accepts a From it does not own. On P1.1 and P4.1 fill
**`alert_email`**, the address alerts go to. On P2.1 fill the four GitHub
fields.

Never put the service key anywhere starting `NEXT_PUBLIC_` — that compiles it
into the browser bundle.

| # | File | What it does | Cadence | Needs |
|---|---|---|---|---|
| 1 | `P0.2_market_discovery.template.json` | Polymarket events → `markets` + `bands`. **Everything else is empty without this.** | 1 h | — |
| 2 | `P0.3_book_volume_snapshot.template.json` | One order book per band → `book_snapshots`. Prices, depth, the fill model. | 15 min | 1 |
| 3 | `P1.5_open_meteo.template.json` | Live reading + 7-day forecast for **every** city, in one request. | 3 h | — |
| 4 | `P0.4_trade_history.template.json` | Trade tape per band → `trades_observed`. Volume, thin-market flags. | 1 h | 1 |
| 5 | `P1.2_nws_monitor.template.json` | api.weather.gov observations + alerts. **US only.** | 2 h | — |
| 6 | `P1.3_nws_forecast.template.json` | api.weather.gov hourly forecast → daily max. **US only.** | 6 h | — |
| 7 | `P1.4_nws_gridpoint.template.json` | api.weather.gov gridpoint → cloud, dewpoint, wind, rain. **US only.** | 6 h | — |
| 8 | `P4.1_health_watchdog.template.json` | Is anything stale or failing. Also wants `alert_email`. | 6 h | — |
| 9 | `P3.1_email_digests.template.json` | Morning brief and end-of-day report. Wants `alert_email`. | 2×/day | — |
| 10 | `P1.1_live_weather_alerts.template.json` | Webhook → email when a weather event fires. | event | — |
| 11 | `P0.5_refresh_rules_text.template.json` | Watches the settlement rules for a mid-market change. Least urgent. | 1 day | 1 |
| 12 | `P2.1_relearn.template.json` | Fires the GitHub Actions relearn run — refits the model on the evidence collected since the last one. | 1 week | — |

**Import 1–3 first and get them green before importing anything else.** Those
three fill the tables every page reads; the rest add accuracy and alerting to a
desk that is already working.

`schedule_gate.snippet.json` is the two-node gate to paste onto any workflow of
your own that should honour the schedules set on the AD4 Workflows page.

### The Polymarket endpoints

P0.2–P0.5 ship with Polymarket's **documented** public endpoints filled in.
They could not be called from the machine that built these files — that sandbox
blocks polymarket.com — so each carries a sticky note saying exactly that, and
what to check on its first run. If one is wrong, the workflow stops at its
guard node and names the field to change; it never writes a partial table.

## If you are re-importing P1.2, P1.3 or P1.4

They failed on every city until now, for one reason worth knowing about if you
ever add another weather.gov node.

n8n's HTTP Request node decides how to decode a response from `Content-Type`,
and its autodetect asks whether that header contains `application/json`.
api.weather.gov answers `application/geo+json`, and `application/problem+json`
on an error. Neither contains that substring, so every response was decoded as
**text**: the Code node received `{ data: "{\"properties\":..." }` with no
`properties`, no `status` and no `title` — nothing to read, and nothing to
report beyond a count of failures.

Every node that calls api.weather.gov now sets **Options → Response → Response
Format = JSON** explicitly. Do the same on any node you add. The Code nodes also
re-parse a string body, so a build of n8n that ignores the option still works.

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

**Safe to commit**: no file here contains a key at all. Every `Config` node
ships with blank `supabase_url`/`alert_email` and there is no `service_key`
field to leave filled in - the key lives in the n8n credential, encrypted at
rest, which an export cannot carry. Fill the Config values in after import,
inside n8n. Use `python scripts/sanitise_n8n_export.py <export>.json n8n/` if
you ever capture one back, and it still refuses to write a file with a
secret-shaped string in it.

## The twelve AD4 workflows

| # | Workflow | Trigger | File |
|---|---|---|---|
| P0.2 | Market Discovery | schedule 1h | `P0.2_market_discovery.template.json` |
| P0.3 | Book + Volume Snapshot | schedule 15m | `P0.3_book_volume_snapshot.template.json` |
| P0.4 | Trade History | schedule 1h | `P0.4_trade_history.template.json` |
| P0.5 | Refresh Rules Text | schedule 1d | `P0.5_refresh_rules_text.template.json` |
| P1.1 | Live Weather Alerts (notify half) | webhook | `P1.1_live_weather_alerts.template.json` |
| P1.2 | NWS Monitor | schedule 2h | `P1.2_nws_monitor.template.json` |
| P1.3 | NWS Forecast | schedule 6h | `P1.3_nws_forecast.template.json` |
| P1.4 | NWS Gridpoint | schedule 6h | `P1.4_nws_gridpoint.template.json` |
| P1.5 | Open-Meteo | schedule 3h | `P1.5_open_meteo.template.json` |
| P2.1 | Relearn trigger | schedule 1w | `P2.1_relearn.template.json` |
| P3.1 | Email Digests | schedule x2/day | `P3.1_email_digests.template.json` |
| P4.1 | Health Watchdog | schedule 6h | `P4.1_health_watchdog.template.json` |

Everything else the spec names (P2.2 Signals, P2.3 Settlement, P2.4 Derived
Recompute, the backtest runner) runs as a GitHub Action, not in n8n - see
`docs/n8n_workflows.md` for why. P2.1 is the exception that spans both: the
model refit itself is an Action, and this workflow is the schedule that
fires it, so the cadence lives with every other cadence on the Workflows
page rather than in a `cron:` line nobody looks at.

### All twelve are templates now

Every file here is built and checked against this repo's own schema and RPCs,
and every one is exercised by the test suite in `tests/test_n8n_code_nodes.py`
against a Node harness that runs the real Code nodes. Import, pick the
credential, fill Config, use.

The P0.x four were once shipped as `.scaffold.json` reconstructions, because
the Polymarket half could not be called from the machine that built them. That
is still true of the endpoints - see **The Polymarket endpoints** above - but
the workflows themselves are now complete, guarded and tested, so there is no
longer a scaffold/template split and no second copy to avoid activating.

### `schedule_gate.snippet.json` - the gate on its own

For a workflow of your own that should honour the cadences set on the AD4
Workflows page. Nothing here needs it; it exists so a workflow you wrote
yourself can be brought under the same schedule control as these twelve.

It is a fragment, not a workflow: four nodes (*Gate config → Check schedule →
Run now? → Stop if skipped*) meant to be copied and pasted onto an existing
canvas, then wired between that workflow's Schedule Trigger and its first
working node. It carries its own `Gate config` node holding `supabase_url`
rather than reading a `Config` node, because it cannot know what the host
workflow named its own; its Supabase call uses the same credential as
everything else.

`docs/DO_THIS_NOW.md` section C2 has the five steps.

### Capturing a workflow back into the repo

If you change one of these inside n8n and want the change kept, capture it
rather than editing the file by hand:

```bash
# n8n -> open the workflow -> ... menu -> Download
python scripts/sanitise_n8n_export.py ~/Downloads/My_Workflow.json n8n/
```

That blanks every Config value, strips bound credentials, removes
instance metadata, forces `active: false`, and redacts anything
secret-shaped anywhere in the file — including a key hardcoded inside a
Code node, which is the easiest one to miss. If a secret-shaped string
survives, it **refuses to write** and tells you which node it is in.

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

1. Pick the Supabase credential on any node that shows a red "credential
   required" flag, and fill in each workflow's `Config` node
   (`supabase_url`, plus `alert_email` on P3.1/P4.1).
2. Run once via the Manual Trigger to confirm the Supabase calls succeed.
   A `42501` at the *Run now?* node means either the credential holds the
   anon key or `sql/ad4_38_grants.sql` has not been run - the error names
   both and ad4_38's output says which.
3. Activate the workflow (top-right toggle) so its real trigger
   (Schedule/Webhook) takes over.

## Where these fit in the first run

`docs/GO_LIVE.md` step 5 walks the import, the Config fields, the test
execution and the expected result for each one, in order. Import them
after the SQL and the GitHub Actions workflows are working - they read
data those produce.
