# Everything, in one place

Every n8n workflow, every SQL file, every GitHub Action — what it is, whether
you need it, and the order to do it in. Nothing else on this page.

---

## Step 0 — the one that makes everything else work

**Put the SERVICE ROLE key in every n8n Config node.**

Supabase → **Project Settings** → **API** → `service_role` (or a key starting
`sb_secret_`). Paste it into the `service_key` field of the **Config** node in
every workflow you import.

You currently have the **anon** key there. `anon` is granted SELECT and nothing
else, so reads worked and every write was refused — that is the
`permission denied for function log_ingest` (42501), and the reason nothing
updated. The workflows now stop on node 2 and say so, instead of walking past
four refused writes and looking healthy.

> Never put that key in Vercel, or in anything starting `NEXT_PUBLIC_`. That
> would compile it into the browser bundle.

---

## Step 1 — SQL

Supabase → **SQL Editor** → paste the whole file → **Run**. In this order.
All four are safe to run again.

| # | File | Why you need it |
|---|---|---|
| 1 | `sql/ad4_21_weather_features.sql` | **Changed.** Its `obs` CTE is `not materialized` now, which is what lets a per-city read use the index instead of scanning the whole archive — 823 ms → 54 ms. Steps 2 and 3 depend on it. |
| 2 | `sql/ad4_28_feature_cache.sql` | **Changed.** `refresh_feature_cache()` takes a city, so the caller can split it. Fixes the HTTP 500 from Archive Observations. |
| 3 | `sql/ad4_29_retention.sql` | **Changed.** Same function, byte-for-byte, so run order cannot matter. Also `prune_observations()` now takes the exact instant that was exported. |
| 4 | `sql/ad4_30_open_meteo.sql` | **New.** `live_weather.source` / `source_kind`, the `v_forecast_coverage` view, and registers P1.5. |

Then, to see what the desk can actually trade tomorrow:

```sql
select * from v_forecast_coverage;
```

`NO FORWARD FORECAST` means that city has no price to disagree with.
`one model` means its spread is zero, so every band looks equally likely.

---

## Step 2 — n8n

n8n → **Workflows** → **Import from File**. Delete any older copy of the same
workflow first — two copies is two schedulers writing one table.

### Import these six

| File | What it does | Cadence |
|---|---|---|
| `P1.1_live_weather_alerts.template.json` | Webhook → email when a weather event fires | event-driven |
| `P1.2_nws_monitor.template.json` | api.weather.gov observations + alerts → the archive and `live_weather`. **US cities only.** | every 2 h |
| `P1.3_nws_forecast.template.json` | api.weather.gov hourly forecast → a daily max per city. **US only.** | every 6 h |
| `P1.4_nws_gridpoint.template.json` | api.weather.gov gridpoint → cloud, dewpoint, wind, rain. **US only.** | every 6 h |
| **`P1.5_open_meteo.template.json`** | **Every city, US or not.** Live reading + 7-day forecast + the drivers behind it, in one HTTP call. | every 3 h |
| `P3.1_email_digests.template.json` | Morning brief and end-of-day report | 2×/day |
| `P4.1_health_watchdog.template.json` | Is anything stale or failing | every 6 h |

That is seven files. P1.5 is the one that matters most right now: **it is the
only source that covers Warsaw, Ankara, Moscow and Jinan.**

### Do NOT import these four

`P0.2`, `P0.3`, `P0.4`, `P0.5` — you already have working versions of these in
n8n, and they are what put 841 markets and 122,000 trades in the database. The
files in this repo are **reconstructions**: the Supabase half is grounded in
the real schema, but the Polymarket endpoints were never verified against your
setup. Do not swap a working ingest for a reconstruction, and do not run both.

If you want the schedule gate on your existing four, paste
`schedule_gate.snippet.json` onto the canvas instead — instructions in
`docs/DO_THIS_NOW.md` section C2.

### After importing, in each one

Open **Config**. Fill in `supabase_url` and `service_key`. Everything else is
pre-filled and correct.

Two workflows want one more field: **P1.1** and **P4.1** each take an
`alert_email`.

Then press **Execute Workflow** and watch the last node, **Summary**. It prints
one line saying what happened. If a node goes red, the first line of the error
says what to fix — that is what the last two rounds of work were about.

Then toggle **Active**, top right.

---

## Step 3 — GitHub Actions

Nothing to install. After merging, use **Run workflow** for a fresh run —
*Re-run jobs* replays the old commit, which is what made the earlier fixes look
like they had not worked.

| Action | Run it now? |
|---|---|
| Data Bank | yes — it was failing on a 409 |
| Live Weather Monitor | yes — it was failing on PGRST102 |
| Signal Engine | yes — same PGRST102 |
| Archive Observations | yes, but leave **commit** unticked. It is a dry run then. |
| everything else | no — they are on their own schedules and were already green |

`docs/compute_budget.md` has one line on each of the sixteen Actions with run
counts, if you want to know why each exists.

---

## What is still not wired, and why

| | Status |
|---|---|
| **Settlement gate** (`settlement_verified`) | Still false, deliberately. It needs one live check of `weather.gov/wrh/timeseries?site=<icao>` against the archive, and this sandbox's network policy refuses that host, so I cannot do it. `docs/settlement_verification.md` has the query. |
| **Strategies** | All ship `enabled = false`. That is why Signals is empty — it is a safety default, not a fault. Turn one on at a time: `update strategies set enabled = true where strategy_id = '…';` and read `docs/strategies.md` first. |

---

## One thing about the weather sources, because it caused real damage

There are **two different weather.gov surfaces** and I had them confused:

- **`weather.gov/wrh/timeseries?site=<icao>`** — global, every city here
  resolves on it, and it is what the markets settle on.
- **`api.weather.gov`** — a different surface, and it covers the United States
  and its territories only.

`cities.nws_supported = false` means "the JSON API returned 404 for this point".
It does **not** mean weather.gov has no data for that city. Reading it the
second way is what left a third of the desk with no live reading and no forward
forecast at all — which is what P1.5 exists to fix.
