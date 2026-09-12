# n8n workflows (Task 15 / spec §7)

**All nine n8n workflows now have a file.** The four P0.x are
`.scaffold.json` reconstructions - they already run in Hassan's n8n and
were never captured here, so their Supabase half is grounded in
`sql/ad4_00_preflight.sql` while their Polymarket endpoint is a Config
field rather than an asserted URL. Capture the real ones with
`scripts/sanitise_n8n_export.py` and delete the scaffolds; see
`n8n/README.md`. The other five are checked templates.

n8n isn't git-managed the way the rest of this repo is - workflows live in
the n8n editor, not as files a build tool compiles. This doc is the spec
for what to build there, plus **rule 1 from §7.0 stated again because it's
the reason nothing here is a finished, importable-with-secrets file**:
never export or share a workflow JSON that has real config values in it -
it contains the service key. `n8n/*.template.json` are importable
skeletons with a blank `Config` node (`supabase_url`/`service_key`/
`alert_email` all empty strings) - fill them in after import, in n8n, not
in this repo.

## What's actually built where

Per `docs/architecture_deviations.md`, P2.1 (Probability + Edge),
P2.2 (Signal Engine), P2.3 (Settlement), and P2.4 (Derived Recompute) are
**GitHub Actions workflows**, not n8n workflows - the underlying compute
(ladder walking, signal rules, settlement verification) is Python that's
already built and unit-tested, and n8n's execution budget (§2.1: ~2,000/
month, ~300 already spoken for by the existing P0.x jobs) can't absorb
four more 4x/day polling loops on top of what §7.12's own accounting
shows is already tight. GitHub Actions has no per-run execution quota.

| Spec workflow | Where it actually runs | File |
|---|---|---|
| P0.2 Market Discovery | n8n (existing) | `n8n/P0.2_market_discovery.scaffold.json` |
| P0.3 Book + Volume Snapshot | n8n (existing) | `n8n/P0.3_book_volume_snapshot.scaffold.json` |
| P0.4 Trade History | n8n (existing) | `n8n/P0.4_trade_history.scaffold.json` |
| P0.5 Refresh Rules Text | n8n (existing) | `n8n/P0.5_refresh_rules_text.scaffold.json` |
| P1.1 Live Weather Monitor (poll) | GitHub Actions, every 15 min | `.github/workflows/live_weather.yml` -> `scripts/live_weather.py` |
| P1.1 Live Weather Monitor (notify) | **n8n**, webhook-triggered | `n8n/P1.1_live_weather_alerts.template.json` |
| P1.2 NWS Monitor (observations, alerts, solar transit) | **n8n**, every 2h + on demand | `n8n/P1.2_nws_monitor.template.json` |
| P1.3 NWS Forecast (second forecast model) | **n8n**, every 6h | `n8n/P1.3_nws_forecast.template.json` |
| P1.4 NWS Gridpoint (forecast conditions) | **n8n**, every 6h | `n8n/P1.4_nws_gridpoint.template.json` |
| P1.5 Open-Meteo Global (all 54 cities) | **n8n**, every 3h | `n8n/P1.5_open_meteo.template.json` |
| P2.1 Probability + Edge Pipeline | GitHub Actions, 4x/day | `.github/workflows/pipeline_intraday.yml` -> `probability_engine.py` + `edge_engine.py` |
| P2.2 Signal Engine | GitHub Actions, 4x/day | `.github/workflows/pipeline_intraday.yml` -> `scripts/signals.py` |
| P2.3 Settlement Sweep | GitHub Actions, daily | `.github/workflows/pipeline_daily.yml` -> `scripts/settlement.py` |
| P2.4 Derived Recompute | GitHub Actions, daily | `.github/workflows/pipeline_daily.yml` -> `scripts/capacity.py` (+ SQL RPCs) |
| P3.1 Email Digests | **n8n** | `n8n/P3.1_email_digests.template.json` |
| P4.1 Health Watchdog | **n8n** | `n8n/P4.1_health_watchdog.template.json` |
| Weather Model fit | GitHub Actions, weekly | `.github/workflows/weather_model.yml` -> `scripts/weather_model.py` |
| Model Forecast (AD4's own prediction) | GitHub Actions, 4x/day | `.github/workflows/pipeline_intraday.yml` -> `scripts/weather_model.py --predict-only` |
| Backtest runner | GitHub Actions, polls every 10 min | `.github/workflows/backtest.yml` -> `scripts/backtest/runner.py` |

> **Which scheduler, and why:** `docs/compute_budget.md` sets out the split -
> fetching is n8n's, thinking is Actions'. It also names the one place the two
> genuinely duplicated each other (live weather) and the two places they only
> look like they do (IEM vs NWS observations; Open-Meteo vs NWS forecasts).

Email digests and the health watchdog stay in n8n deliberately: they're
genuinely light (§7.12's own accounting: ~90/month + ~120/month), they're
mostly "call one RPC, format the JSON as HTML/text, send" with no
non-trivial logic worth unit-testing in Python, and n8n's Send Email node
is the simplest way to get a mailbox integration without adding an SMTP
client dependency to this repo. Live weather's *notification* half stays
in n8n for the same reason **and** because it's genuinely event-driven
(only fires on a spike), unlike the four polling loops above.

## §7.0 rules, applied to every workflow below

1. **No credential binding** - a `Config` Set node holds `supabase_url`
   and `service_key` (and `alert_email` where relevant), every HTTP node
   reads headers from it explicitly (`apikey`, `Authorization: Bearer
   ...`, `Content-Type`, `Accept`). Never bind a credential in the node's
   own auth dropdown - that failed repeatedly with "Credentials not
   found" earlier in this project.
2. Every workflow has both a **Manual Trigger** and its real trigger
   (Schedule or Webhook), wired to the same downstream nodes.
3. `saveDataSuccessExecution: "none"` on the two workflows that could run
   often (P1.1's notify webhook, since it's event-driven and could burst).
4. `alwaysOutputData: true` and `onError: continueRegularOutput` on every
   HTTP node, so one failed Supabase read/write doesn't kill the run.
5. Every workflow ends with a **Summary** node (a Code node emitting a
   plain-language line - counts, what happened) so a run's outcome is
   readable without opening node-level data.
6. Writes go through **RPC endpoints** (`/rest/v1/rpc/<fn>`), matching
   `sql/ad4_rpc.sql` - never a direct table insert from n8n either.

---

## P1.1 - Live Weather Monitor (notify half)

**Trigger:** Webhook (POST), called by `scripts/live_weather.py` when it
detects a `high`/`critical` event (see that script's `main()` - it POSTs
to `settings.weather_alert_webhook.url`, which should be set to this
workflow's n8n webhook URL once it's imported and activated).

**Nodes:**
1. Webhook Trigger
2. Manual Trigger (for testing with a synthetic payload)
3. Config (`supabase_url`, `service_key`, `alert_email`)
4. Code - normalise the incoming `{events: [...]}` payload into one
   readable block per event (city, kind, severity, temp, change)
5. IF - any event `severity` in (`high`, `critical`)? (belt-and-braces;
   `live_weather.py` already filters before calling the webhook, but a
   manual-trigger test run might send anything)
6. Send Email - subject `AD4 weather alert: <city> <kind>`, body from
   step 4's formatted block
7. HTTP PATCH `/rest/v1/weather_events?event_id=in.(<ids>)` -> `{"notified": true}`
8. Summary - "N events notified, M skipped (below threshold)"

**Executions:** only when a spike fires - spec estimate 5-20/day,
~450/month worst case (§7.5). Acceptable within the ~2,000/month budget.

---

## P3.1 - Email Digests

Three logical triggers in one workflow, per the spec's own table:

| Trigger | Cron UTC | Beirut | Content |
|---|---|---|---|
| Morning brief | `0 4 * * *` | 07:00 | Ranked opportunities, prices, volumes, regime per city, clock events, capital plan, predicted outcomes |
| End of day | `0 21 * * *` | 00:00 | Fills, net P&L by strategy, resolutions, calibration status, next-day candidates, previous-day report |
| Event alert | (handled by P1.1 above, not duplicated here) | - | High-severity signals as they fire |

**Nodes:**
1. Schedule Trigger (morning, `0 4 * * *`)
2. Schedule Trigger (EOD, `0 21 * * *`)
3. Manual Trigger
4. Config
5. Switch - which trigger fired?
6. HTTP POST `/rest/v1/rpc/build_morning_brief` (empty body) | HTTP POST
   `/rest/v1/rpc/build_eod_report` (empty body) - both defined in
   `sql/ad4_rpc.sql`, both return the assembled jsonb payload
7. Code - render the jsonb payload to an HTML email. Two templates (one
   per digest); keep both simple tables, dark-theme-agnostic (email
   clients don't respect `prefers-color-scheme` reliably) - light
   background, dark text, no external assets.
8. HTTP GET `/rest/v1/settings?key=eq.email_recipient&select=value` -
   recipient is a `settings` value Hassan fills (add via
   `update_setting` from a future Settings UI, or directly in the SQL
   editor: `select update_setting('email_recipient', '{"address":
   "hassan@..."}');`)
9. Send Email (To: from step 8, delivery time already handled by the
   cron being expressed in Beirut-equivalent UTC per the table above)
10. Summary - "morning brief sent to <address>" / "EOD report sent"

**Executions:** 2/day scheduled = ~60/month, per §7.12.

---

## P4.1 - Health Watchdog

**Trigger:** Schedule, `0 */6 * * *` (4x/day, per §7.11).

**Checks** (all read-only HTTP GETs against PostgREST, via Config):
- Stale book snapshots: `book_snapshots?select=observed_at&order=observed_at.desc&limit=1` - flag if `now() - observed_at > 60min` (matches `anomaly_rules.stale_book`).
- Stale forecasts: `weather_forecasts?select=run_at&order=run_at.desc&limit=1` - flag if `> 12h` (matches `anomaly_rules.stale_forecast`).
- Failed ingest jobs: `ingest_log?select=job,status,logged_at&order=logged_at.desc&limit=20` - flag any `status=eq.error` in the last 24h.
- Probabilities not summing to 1: reuses the Task 4 verify query, but as
  a live check - `select band_id, sum(calibrated_prob) from
  band_probabilities ... group by band_id having abs(sum-1) > 0.02` (a
  small SQL view, `v_prob_sum_check`, is worth adding to `sql/ad4_rpc.sql`
  if this check turns out to need it; the raw query works fine as a
  PostgREST `rpc` call in the meantime since it's just a read).
- Anomaly rows in last 24h: `anomalies?select=id&detected_at=gte.<24h ago>`.
- n8n execution count approaching the plan limit: n8n's own API
  (`GET {n8n_base_url}/api/v1/executions?limit=1` returns a total count
  in some n8n versions; otherwise use the n8n instance's built-in
  Insights/usage page) - the one check that can't be a Supabase read,
  since it's asking about n8n itself.

**Nodes:**
1. Schedule Trigger · 2. Manual Trigger · 3. Config
4. HTTP GET x5 (the Supabase checks above), each `alwaysOutputData: true`
5. Code - evaluate all five results against their thresholds, build a
   list of failures
6. IF - any failures?
7. Send Email (failures only - "Emails only on failure" per §7.11)
8. Summary - "N/5 checks failed" or "all clear"

**Executions:** 4x/day = ~120/month, per §7.12.

---

## §7.12 budget, updated for what's actually in n8n now

| Workflow | Cadence | Executions/month |
|---|---|---|
These are the defaults in `settings.workflow_schedules`, which
`sql/ad4_20_schedules.sql` seeds and `should_run()` enforces. **Read them from
`v_execution_budget`, not from here** - the table below is a snapshot and the
settings row is the authority.

| Workflow | Cadence | Executions/month |
|---|---|---|
| P0.2 Market Discovery | every 6h | 120 |
| P0.3 Book Snapshot | **hourly** | **720** |
| P0.4 Trade History | every 6h | 120 |
| P0.5 Refresh Rules | daily | 30 |
| P1.1 Weather Alerts (notify only) | event-driven | ~450 worst case |
| P1.2 NWS Monitor | every 2h | 360 |
| P1.3 NWS Forecast | every 6h | 120 |
| P1.4 NWS Gridpoint | every 6h | 120 |
| P3.1 Email Digests | 2x/day | 60 |
| P4.1 Watchdog | every 6h | 120 |
| **TOTAL** | | **1,770 / 2,000** |

P0.3 is 720 of that on its own, because a book snapshot is the one thing every
price on the desk depends on and an hour-old book prices nothing well. It is
also the first knob to turn if the cap gets tight.

An earlier version of this table said 1,530 and had P0.3 at 4x/day. That was
wrong - it did not match the seeded default, which is hourly - and the gap
mattered, because 1,530 leaves comfortable room and 1,770 does not.

Still inside budget, with ~230 spare for manual Run-button executions - and
the 4x/day polling loops of P2.1/P2.2/P2.3/P2.4 are on GitHub Actions rather
than stacked on top of this, which is what leaves the room.

**This is why P1.2 runs every 2 hours and not every 30 minutes.**
api.weather.gov imposes no rate limit of its own - the constraint is entirely
n8n's execution count. At 30-minute polling P1.2 alone would be 1,440/month
and the total ~2,850, i.e. over the cap; the first things to fail would be
the digests and the watchdog, silently, at the end of a month. Two hours is
360. The Workflows page's Run button covers "I want a reading now" for one
execution each time, which is the shape the freshness requirement actually
has. If continuous 30-minute NWS polling is ever genuinely wanted, its home
is a GitHub Action beside `live_weather.yml` - no per-run quota - not a
bigger n8n bill.

---

## Live workflow ids, as deployed

Written down because three of these were rebuilt on 12 Sep 2026 and the old
copies still exist in n8n under the same names. The ids below are the ones
that are actually published and running; anything else with a matching name
is the dead predecessor.

| Workflow | n8n id | State |
|---|---|---|
| AD4 P0.2 - Market Discovery | `speyDmtDN01cEI5N` | active, every 6h |
| AD4 P0.3 - Book + Volume Snapshot | `e0HiTavIzilGmBPo` | active, hourly |
| AD4 P0.4 - Trade History | `SA3nGidtSpXOrz9g` | active, every 6h |
| AD4 P0.5 - Refresh Rules Text | `F7UO2tcZQ6nThe0s` | active |
| AD4 P1.1 - Live Weather Alerts | `5Lz8nIOQmu8lEgl1` | INACTIVE - email, off by request |
| AD4 P1.2 - NWS Monitor | `tahTg5bLzFyeByqB` | active, every 2h — **rebuilt** |
| AD4 P1.3 - NWS Forecast | `8BBQpbp7gOx0UDFM` | active, every 6h — **rebuilt** |
| AD4 P1.4 - NWS Gridpoint | `FLNPloignHpFnOhp` | active, every 6h — **rebuilt** |
| AD4 P1.5 - Open-Meteo Global | `afcFuaCyn09xUJcV` | active, every 3h |
| AD4 P2.1 - Relearn | `vVxodQEVvw2nImGy` | active (needs a PAT in Config) |
| AD4 P2.2 - Paper Maintenance | `hKHr9qdVjmzHgyaP` | active |
| AD4 P3.1 - Email Digests | `9KKDvlBDYHxQMWzE` | INACTIVE - email, off by request |
| AD4 P4.1 - Health Watchdog | `uGZ2deLMqYWAk6IH` | active |

### Why P1.2, P1.3 and P1.4 were rebuilt rather than repaired

Their original copies were created with **MCP access turned off**, which makes
them invisible to every tool that could publish, edit or even deactivate them:

    Workflow is not available in MCP. Enable MCP access from the workflow
    card in the workflows list, or from the workflow settings.

That is a per-workflow toggle in the n8n UI and there is no API for it, so the
only way to get a working, publishable P1.2/P1.3/P1.4 was to create new ones
from the templates in `n8n/`. The old three are inactive and harmless, but they
hold the same webhook paths (`ad4-nws-monitor`, `ad4-nws-forecast`,
`ad4-nws-gridpoint`) — **activating one would collide with its replacement**.
Delete or rename them when convenient.

P1.5 was NOT rebuilt: it is equally invisible to MCP, but it is active and
working (7,992 forecast rows written 1.2h before this was written), and
replacing a healthy feed to gain editability is not worth the outage.

### Order matters on a cold start

P1.4 reads `cities.nws_grid_wfo/x/y` and refuses to run when no city has one.
P1.2 and P1.3 are what resolve `/points` and write those ids back. On a fresh
database, run P1.3 (or P1.2) once before P1.4, or P1.4 stops with

    No city has a cached NWS gridpoint. P1.2 or P1.3 must run first.

Of 54 cities, 12 are inside the NWS coverage area; the other 25 checked so far
are recorded as `nws_supported = false` and skipped on later runs. Those cities
are covered by P1.5 (Open-Meteo), which is global.
