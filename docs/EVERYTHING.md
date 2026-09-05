# Everything, in one place

Every SQL file, every n8n workflow, every GitHub Action — what it is, whether
you need it, and the order. Nothing else on this page.

**Total time: about 25 minutes.** Steps 0–2 are the ones that make data move.

---

## Step 0 · The key (2 min, and nothing works without it)

**Put the SERVICE ROLE key in every n8n Config node.**

Supabase → **Project Settings** → **API** → `service_role` (or a key beginning
`sb_secret_`). Paste it into `service_key` in the **Config** node of every
workflow you import.

You currently have the **anon** key there. `anon` holds SELECT on every table
and nothing else, so reads worked and every write was silently refused — that
is the `permission denied for function log_ingest` (42501), and the reason
nothing updated for days.

> **Never** put that key in Vercel, or in anything starting `NEXT_PUBLIC_`.
> That compiles it into the browser bundle for every visitor.

**Check it before you spend a run:** run `sql/ad4_diagnose.sql` (read-only) and
read **section 6 — WRITE ACCESS**. It reports which key can write which table.

> One trap it names explicitly: `should_run` is granted to `anon` **on purpose**
> so the Workflows page can preview a schedule change. An anon key therefore
> sails through the schedule gate and only fails at the first *write*. The gate
> is not what catches a bad key; section 6 and the write guard are.

---

## Step 1 · SQL (10 min)

Supabase → **SQL Editor** → paste the whole file → **Run**, in this order.
All are safe to run again, in any order, any number of times.

| # | File | Why |
|---|---|---|
| 1 | `ad4_21_weather_features.sql` | **Changed.** Its `obs` CTE is `not materialized`, which lets a per-city read use the index instead of scanning the archive: **823 ms → 54 ms**. Steps 2 and 3 depend on it. |
| 2 | `ad4_28_feature_cache.sql` | **Changed.** `refresh_feature_cache()` takes a city so the caller can split it. Fixes the HTTP 500 from Archive Observations. |
| 3 | `ad4_29_retention.sql` | **Changed.** Same function byte-for-byte as step 2, so run order cannot matter. `prune_observations()` now takes the exact instant that was exported. |
| 4 | `ad4_30_open_meteo.sql` | **New.** `live_weather.source` / `source_kind`, `v_forecast_coverage`, registers P1.5. |
| 5 | `ad4_31_predictive.sql` | **New.** The five views behind the Predictive page. |
| 6 | `ad4_32_run_scope.sql` | **New.** Lets a job cover selected cities instead of all 37. |
| 7 | `ad4_33_control.sql` | **New.** `set_strategy_enabled()` — the switch the app never had. Plus `v_strategy_board` (every strategy with its own record) and `v_trade_timing` (when the day is decided, from each city's own measured peak hour). |
| 8 | `ad4_34_trade_plan.sql` | **New.** `v_trade_plan` — every edge with **when** and **who** attached: which strategies would fire on it right now, what the entry costs at the ask, and where the day is heading relative to that band. Plus `v_city_day_plan` for the two-bucket cover. |
| 9 | `ad4_35_databank_inventory.sql` | **New.** What the archive holds and what has been built from it, per dataset and per city. Behind the Data Bank page. |
| — | `ad4_diagnose.sql` | **Read-only.** Run any time. Section 6 is the write-access check above. |

Then four questions worth asking straight away:

```sql
select * from v_forecast_coverage;   -- which cities can be traded tomorrow
select * from v_run_scope;           -- which cities each job covers
select * from v_archive_inventory;   -- what has actually been collected
select strategy_id, enabled, verdict from v_strategy_board;   -- what is switched on
```

`NO FORWARD FORECAST` means that city has no price to disagree with.
`one model` means its spread is zero, so every band looks equally likely.

---

## Step 2 · n8n (10 min)

n8n → **Workflows → Import from File**. Delete any older copy of the same
workflow first — two copies is two schedulers writing one table.

### Import these seven

| File | What it does | Cadence |
|---|---|---|
| `P1.1_live_weather_alerts` | Webhook → email when a weather event fires | event-driven |
| `P1.2_nws_monitor` | api.weather.gov observations + alerts → archive and `live_weather`. **US only.** | 2 h |
| `P1.3_nws_forecast` | api.weather.gov hourly forecast → daily max. **US only.** | 6 h |
| `P1.4_nws_gridpoint` | api.weather.gov gridpoint → cloud, dewpoint, wind, rain. **US only.** | 6 h |
| **`P1.5_open_meteo`** | **Every city, US or not.** Live reading + 7-day forecast + the drivers behind it, in one request. | 3 h |
| `P3.1_email_digests` | Morning brief and end-of-day report | 2×/day |
| `P4.1_health_watchdog` | Is anything stale or failing | 6 h |

**P1.5 matters most.** `api.weather.gov` covers the US and its territories
only, so Warsaw, Ankara, Moscow and Jinan had no live reading and no forward
forecast from any job at all.

In each one: open **Config**, fill in `supabase_url` and `service_key`.
Everything else is pre-filled and correct. **P1.1** and **P4.1** also want an
`alert_email`. Press **Execute Workflow**, watch the **Summary** node, then
toggle **Active**.

### Do NOT import P0.2–P0.5

Those four in this repo are **reconstructions**. The Supabase half is grounded
in the real schema; the **Polymarket half is a guess**, and it has to stay one —
this build environment's network policy refuses `gamma-api.polymarket.com`,
`clob.polymarket.com` and `data-api.polymarket.com`, exactly as it refuses
`api.weather.gov`.

They used to ship with a plausible URL already filled in. That was the mistake:
a wrong pre-filled value reads as authoritative, so nobody changes it, and the
run dies four nodes later talking about response shapes. Those fields now ship
**empty** and the workflow stops at node 2 saying to copy the URL from your own
working P0.x.

**Your originals are the authority** — they put 841 markets and 122,000 trades
in the database. Use these for reference only:

| Take | How |
|---|---|
| The **schedule gate** | Paste `schedule_gate.snippet.json` onto your original's canvas. `docs/DO_THIS_NOW.md` § C2. |
| The **write check** | Turn on *Options → Response → Never Error* on their write nodes, and copy the guard from the `Summary` node of any P1.x file. Without it a refused write is reported as a successful run. |

If your own P0.2–P0.5 are failing, run `ad4_diagnose.sql` section 6 first — the
most likely cause is the same anon key.

---

## Step 3 · GitHub Actions (3 min)

Nothing to install; no Action file changed. Use **Run workflow** for a fresh
run — *Re-run jobs* replays the old commit, which is what made the earlier
fixes look like they had not worked.

| Action | Run now? |
|---|---|
| Data Bank | yes — was failing on a 409 |
| Live Weather Monitor | yes — was failing on PGRST102 |
| Signal Engine | yes — same PGRST102 |
| Archive Observations | yes, **leave `commit` unticked** (dry run) |
| everything else | no — already green, on their own schedules |

`docs/compute_budget.md` has one line per Action with run counts.

---

## What changed in the app

| Page | |
|---|---|
| **Predictive** *(new)* | Forward: what the desk expects, which bucket, what the market charges. Backward: was it right, and separately did being right pay. Includes the 3D convergence funnel — depth is lead days, height is temperature, width is the resolution day. |
| **Signals** | No longer a permanent 320px column. A drawer: closed by default, opens over the page, and the trigger with its live count is present at **every** screen width. It used to vanish entirely below 1024px. Pin it if you want the old docked column. |
| **Live Weather** | One verdict at the top: newest reading anywhere, and if that is stale, what is not running. Cards now mark a `model` reading — Open-Meteo interpolates to a coordinate, a station measures at the ICAO the market settles on, and only one is evidence. |
| **Analytics** | Four groups in dependency order: is the forecast good → is the pricing good → did it make money → can it take size. Line charts have a real crosshair tooltip; they previously had none. |
| **Workflows** | Pick which cities each job covers. |
| **Strategies** *(new)* | The main switch. Every strategy with its own record beside it — fired, filled, win rate, net P&L — and a verdict that separates "has never fired" from "loses money". |
| **Globe** *(new)* | All 37 cities under real daylight, with the 12:00–17:00 local-solar band lit warm: the only strip on the planet where today's maxima are being made. Opens on that meridian, not on Greenwich. |
| **Data Bank** *(new)* | What has been collected and what was built from it, per dataset and per city, then the frozen record asked whether a 30% settles 30% of the time. |
| **Opportunities** | Now says **when** and **who**: which strategies would take each row (struck through when they are switched off), what the entry costs at the ask rather than the mid, where the day is heading relative to that band, and how old the book underneath it is. |
| **Goals** | A "what the day says" panel. The spread was priced entirely off the model and the book; neither had looked out of the window. It now flags a covered band the day cannot physically reach. |
| **Calculator** | Removed. Its one inbound link now opens the Board. |

### About scoping a run

n8n bills per **execution**, so narrowing a job does not reduce your execution
count. It reduces runtime and upstream load, and that matters only where a job
makes one request per item:

| Job | Effect |
|---|---|
| P0.3 book | ~800 requests a run → 66 for three cities. The difference between finishing and timing out. |
| P0.4 trades | one per market — cuts proportionally |
| P1.2 NWS | three per city, 111 → 9 |
| **P1.5 Open-Meteo** | **one request carries every city — scoping changes the length of a URL and nothing else** |

A scope that matches no active city falls back to covering everything. Running
zero cities is indistinguishable from a broken job, so it never happens.

---

## Still not wired, and why

| | |
|---|---|
| **Settlement gate** (`settlement_verified`) | Deliberately false. Needs one live check of `weather.gov/wrh/timeseries?site=<icao>` against the archive; this environment's network policy refuses that host. `docs/settlement_verification.md` has the query. |
| **Strategies** | All ship `enabled = false` — a safety default, not a fault, and why Signals is empty. **This is now a toggle on the Strategies page**, not an UPDATE typed into the SQL editor. Each row shows how many bands on the board would pass its entry test right now, so the switch is a decision rather than a guess. Read `docs/strategies.md` first. |

---

## The weather sources, because confusing them cost real time

- **`weather.gov/wrh/timeseries?site=<icao>`** — global, every city here
  resolves on it, and it is what the markets settle on.
- **`api.weather.gov`** — a different surface, US and territories only.

`cities.nws_supported = false` means "the JSON API returned 404 for this point".
It does **not** mean weather.gov has no data for that city. Reading it the
second way is what left a third of the desk with no forecast, and is what P1.5
exists to fix.
