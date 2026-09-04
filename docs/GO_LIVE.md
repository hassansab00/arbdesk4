# AD4 — GO LIVE

One ordered pass, start to finish. Copy-paste each block, check the stated
result, move on. Nothing here says "open the file and check" — every step
has an exact command and an exact expected output.

Total time if nothing goes wrong: about 40 minutes, most of it waiting for
GitHub Actions.

**Before you start you need:**

| Thing | Where it comes from |
|---|---|
| Supabase project URL | Supabase → Project Settings → Data API → Project URL |
| Supabase **publishable** key | same page. Newer projects: `sb_publishable_…`. Older: the `anon public` JWT. **This is the browser one.** |
| Supabase **secret** key | same page. Newer: `sb_secret_…`. Older: `service_role`. **Never** goes in Vercel — see the warning in step 4. |
| GitHub repo admin | to set Actions secrets |
| Vercel account | to deploy `web/` |
| n8n instance | optional; only for email digests and alerts |

---

## STEP 1 — Run the SQL, in this exact order

Supabase → **SQL Editor** → New query. Paste the **entire contents** of each
file, one file at a time, in this order. Wait for each to finish before
starting the next.

| # | File | What it does |
|---|---|---|
| 1 | `sql/ad4_00_preflight.sql` | **Run this first.** Guarantees every table, column and unique key the other 22 files need. |
| 2 | `ad4_phase1_tables.sql` | forecast-skill table |
| 3 | `sql/ad4_phase2.sql` | cost params, edges, anomaly rules, correlation, capacity, views |
| 4 | `sql/ad4_phase2_ranking.sql` | `v_opportunities` with the ranking score |
| 5 | `sql/ad4_capacity_correlation.sql` | `recompute_capacity()`, `recompute_correlation()` |
| 6 | `sql/ad4_strategies_seed.sql` | the eight strategies, all disabled |
| 7 | `sql/ad4_paper_engine_columns.sql` | paper-trade / signal / ledger columns |
| 8 | `sql/ad4_settlement.sql` | `settle_markets()` |
| 9 | `sql/ad4_backtest.sql` | `queue_backtest()` |
| 10 | `sql/ad4_rpc.sql` | `calc_recommendation()` and the rest of the RPC layer |
| 11 | `sql/ad4_live_weather.sql` | `live_weather`, `weather_events` |
| 12 | `sql/ad4_rls.sql` | RLS policies, and the revoke-then-grant that closes the write boundary. |
| 13 | `sql/ad4_13_reconcile.sql` | Reconciles everything above with the real Phase 0 column shapes. |
| 14 | `sql/ad4_14_workflows.sql` | Lets the UI run the n8n workflows and read their run history. |
| 15 | `sql/ad4_15_pipeline_fixes.sql` | Adds the `system` strategy the Signal Engine writes its alerts against. |
| 16 | `sql/ad4_16_nws.sql` | Makes room for api.weather.gov: per-city NWS ids, today's solar transit, and `v_forecast_divergence`. |
| 17 | `sql/ad4_17_city_stats.sql` | Each city's climatological normal and volatility, so City Clusters can say how hot today is *for that city*. |
| 18 | `sql/ad4_18_databank.sql` | The immutable record: what was predicted, what the market charged, what happened. Feeds calibration. |
| 19 | `sql/ad4_19_stats_cache.sql` | Caches the climatology and bounds the divergence join — without it City Clusters and Analytics time out on a real archive. |
| 20 | `sql/ad4_20_schedules.sql` | Moves each workflow's cadence out of its n8n JSON and into a setting the Workflows page owns, with a runs/month budget. |
| 21 | `sql/ad4_21_weather_features.sql` | Morning conditions per city-day — dewpoint depression, cloud, wind, rain — plus the persistence benchmark every forecast must beat. |
| 22 | `sql/ad4_22_opportunity_context.sql` | Price and forecast movement per band, and whether the market has repriced since the forecast moved. |
| 23 | `sql/ad4_23_reasoning.sql` | The desk's whole argument for one city in one row — forecast, persistence, this morning, where the day is, measured error, model agreement, and therefore which bucket. Safe to run before 21 and 22: it builds from whatever exists and names what is missing. |
| 24 | `sql/ad4_24_nws_gridpoint.sql` | The forecast side of file 21: `weather_forecast_features`, whose columns carry the same names `v_city_day_features` uses for observed conditions, so a model fitted on what happened reads a forecast day untranslated. Filled by n8n **P1.4**. |
| 25 | `sql/ad4_25_model_forecast.sql` | AD4's own forward prediction — the fitted coefficients applied to those forecast conditions — with its arithmetic stored beside it. Filled by the **Model Forecast** action. |
| 26 | `sql/ad4_26_temp_trend.sql` | **Run this last.** Which way today is pointing and how fast (least squares over the real timestamps), plus how much this city has historically still climbed from this local hour. Feeds the **City Monitor** page and strategy **S7**. Also adds `v_band_price_history` — per-bucket price movement, bounded to 48h. |

Every file is idempotent — re-running any of them is safe and changes
nothing that is already correct.

**File 13 is not optional, and it genuinely has to be last.** Files 1–12
were written before anyone could see the real Phase 0 schema, and three of
their assumptions were wrong on the live database:

* `book_snapshots.bid_levels` / `ask_levels` are **integer level counts**,
  not jsonb ladders. The real book is in `raw_book`, with pre-aggregated
  depth in `ask_usd_1c … ask_total_usd`. Anything that walked a ladder was
  reading an integer.
* `trades_observed` timestamps live in **`traded_at`**. File 1 adds an
  `observed_at` column that is NULL on every existing row, and the volume
  views filtered on it — so every band reported **$0 traded volume** with
  122,373 trades sitting in the table.
* `anon` still held **INSERT/UPDATE/DELETE/TRUNCATE** on about 40 tables
  from an old `grant all`. File 12 only ever added SELECT; a grant nobody
  revokes never goes away, and a `for select` RLS policy does not stop an
  INSERT from a role that holds the INSERT privilege.

File 13 fixes all three, plus the type mismatches underneath them
(`paper_trades.trade_id` is `uuid`, not `bigint`; the close columns are
`close_price` / `close_reason`). It does it by **inspecting the schema at
run time** rather than hard-coding names, so it is correct on your database
and on a fresh one, and it stays correct if the ingest changes shape again.

If you re-run file 12 for any reason, run file 13 again after it.

### What step 1 should look like

**File 1 (`ad4_00_preflight.sql`)** prints a report in the SQL editor's
**Messages** tab. On a database that has already had some AD4 schema
applied you will see something like:

```
NOTICE:  preflight: 5 missing column(s) added
NOTICE:  =====================================================
NOTICE:  AD4 PREFLIGHT COMPLETE
NOTICE:  =====================================================
NOTICE:  5 object(s) had to be added:
NOTICE:    + column         strategies.capital_cap_pct
NOTICE:    + column         strategies.extra
NOTICE:    + column         strategies.max_concurrent
NOTICE:    + column         strategies.regime_filter
NOTICE:    + column         strategies.universe
```

`Nothing was missing` is also a valid result — it means your database
already had everything.

**Files 2–13** finish with `Success. No rows returned`. Some print
`NOTICE: relation "x" already exists, skipping` — that is the idempotency
working, not an error.

**File 13** prints what it found and what it decided, which is worth
reading:

```
NOTICE:  v_band_book built: raw_book=t  jsonb_levels=f  usd_tiers=t
NOTICE:  reconcile: trades_observed timestamp = coalesce(t.traded_at, t.observed_at, t.ingested_at), city_key column = t
NOTICE:  reconcile: v_band_volume / v_city_volume rebuilt
NOTICE:  reconcile: v_latest_book + v_opportunities rebuilt
NOTICE:  reconcile: close_position(uuid, numeric, text) rebuilt
NOTICE:  reconcile: log_paper_trade rebuilt (trade_id returned as text)
NOTICE:  reconcile: approve_signal / dismiss_signal rebuilt
NOTICE:  reconcile: SECURITY OK - anon/authenticated hold zero write grants in public
```

That last line is the one to check. If it says `WARNING: n write grants
SURVIVED` instead, something else is re-granting them — send the output of
`select * from ad4_reconcile_report();`.

You can re-run that report on its own at any time:

```sql
select * from ad4_reconcile_report();
```

| check | what PASS means |
|---|---|
| `trades timestamp column` | the expression the volume layer is actually filtering on |
| `band volume is non-zero` | at least one band reports traded volume |
| `book ladders resolve` | how many bands got a real book vs. a reconstructed one |
| `raw_book coverage` | how many snapshots carry a real ladder |
| `bands with no usable book` | bands with neither a ladder nor depth totals — unfillable by design |
| `anon/authenticated write grants` | zero — the browser key cannot write |
| `anon EXECUTE surface` | should be exactly 15: the 9 UI RPCs plus 6 pure view helpers |
| `close_position signature` | its argument type matches `paper_trades.trade_id` |

### If `raw_book coverage` reads 0

Then every ladder in AD4 is being **reconstructed** from the cumulative
depth columns (`ask_usd_1c` … `ask_total_usd`) rather than read from a real
order book. That is not broken — but it is worth knowing how good the
approximation is, because it prices every fill in Goals and the Calculator.
Measured against a known six-level ladder:

| | real ladder | reconstructed |
|---|---|---|
| total depth | $2,946.75 | $2,946.75 (exact) |
| $500 order, 25c cap | 1464.2 sh @ 0.3415 | 1466.5 sh @ 0.3409 |
| $1,500 order, 25c cap | stops at $1,146.75, 3050.0 sh @ 0.3760 | stops at $1,146.75, 3052.3 sh @ 0.3757 |
| whole side, uncapped | $2,946.75 @ 0.4871 | $2,946.75 @ 0.4787 |

Total depth is exact, and a slippage-capped walk is within ~0.2%. The error
lives in the far tail, where the depth columns genuinely stop telling you
anything — everything past 25c is one bucket, so it is priced *outside* 25c
so a capped walk correctly refuses it rather than promising fills that are
not there.

**The cure is to populate `raw_book`.**
`n8n/P0.3_book_volume_snapshot.scaffold.json` now writes it, along with the
level counts and all twelve depth columns, in the real production shape. Its
earlier version wrote the ladders into `bid_levels`/`ask_levels`, which are
`integer` — so the ladder never landed anywhere. Re-import that workflow and
run it, and `book ladders resolve` flips to `raw_book=…` on the next
snapshot cycle.

### Verify step 1

**The fast way:** paste the whole of **`sql/ad4_99_verify.sql`** into the SQL
editor and run it. It is read-only, safe to run any time, and returns one
grid: every check with PASS / FAIL / ATTENTION and, where something failed,
which file to re-run. It also reports the **real column shape** of the six
tables this repo previously had to guess at — that grid is the thing to send
back if anything looks wrong.

Expect every row in sections 1–5 to read PASS. Sections 6 (DATA) and 7
(ACTUAL SHAPE) are informational: at this point almost everything in section
6 will read EMPTY, which is correct — steps 2 and 3 below fill it.

**The manual way**, if you would rather see each number on its own — run
this whole block as one query:

```sql
-- 1a. every table exists
select count(*) as tables_present from information_schema.tables
where table_schema = 'public' and table_name in (
  'cities','markets','bands','book_snapshots','trades_observed',
  'band_probabilities','model_versions','strategies','deployments',
  'signals','paper_trades','ledger','backtest_runs','backtest_results',
  'backtest_trades','settings','anomalies','ingest_log',
  'weather_observations','weather_forecasts','derived_weather_peak',
  'derived_market_peak','derived_city_day_volume','derived_band_day_volume',
  'derived_forecast_skill','edges','cost_params','anomaly_rules',
  'strategy_conflicts','derived_capacity','derived_city_correlation',
  'live_weather','weather_events');
```
**Expect: `tables_present = 33`.**

```sql
-- 1b. every view exists
select count(*) as views_present from information_schema.views
where table_schema = 'public' and table_name in (
  'v_latest_book','v_latest_prob','v_latest_edge','v_opportunities',
  'v_band_volume','v_city_volume');
```
**Expect: `views_present = 6`.**

```sql
-- 1c. the five columns that used to be missing
select count(*) as strategy_cols from information_schema.columns
where table_schema='public' and table_name='strategies'
  and column_name in ('universe','regime_filter','capital_cap_pct','max_concurrent','extra');
```
**Expect: `strategy_cols = 5`.**

```sql
-- 1d. every RPC exists
select count(*) as rpcs_present from pg_proc p
join pg_namespace n on n.oid = p.pronamespace
where n.nspname='public' and p.proname in (
  'calc_recommendation','log_paper_trade','approve_signal','dismiss_signal',
  'close_position','queue_backtest','update_setting','upsert_deployment',
  'set_deployment_status','settle_markets','recompute_capacity',
  'recompute_correlation','refresh_derived','build_morning_brief',
  'build_eod_report','walk_ladder_jsonb','depth_usd','capacity_side',
  'compute_edges','evaluate_signals','recompute_regime_thresholds',
  'recompute_behavioural_clusters','log_ingest');
```
**Expect: `rpcs_present = 23`.**

```sql
-- 1e. all eight strategies seeded and DISABLED
select strategy_id, enabled from strategies order by strategy_id;
```
**Expect: exactly 6 rows, `enabled = false` on every one.**
```
s1_buy_low_sell_signal   false
s2_combination_arb       false
s3_concentration         false
s4_tail_fade             false
s5_running_max_lock      false
s6_anchor_insurance      false
```

```sql
-- 1f. anon can read but not write
select count(*) as anon_read_policies from pg_policies
where schemaname='public' and policyname='anon_read';
```
**Expect: 30 or more.** (One per table in `sql/ad4_rls.sql`'s list, plus
`settings`, `anomalies` and `trades_observed`.)

**If any of these numbers is lower than stated**, re-run
`sql/ad4_00_preflight.sql` and read its NOTICE output — it names exactly
what it could not create and why.

---

After step 3 and step 4, run `sql/ad4_99_verify.sql` again. By then
section 6 should read HAS DATA for everything the workflows you ran are
responsible for, and `MARKET VOLUME (rolling 24h)` tells you whether the
volume layer has anything to work with.

---

## STEP 2 — GitHub Actions secrets

GitHub → your repo → **Settings → Secrets and variables → Actions → New
repository secret**. Add exactly two:

| Name | Value |
|---|---|
| `SUPABASE_URL` | `https://YOURPROJECT.supabase.co` (no trailing slash) |
| `SUPABASE_SERVICE_KEY` | the `service_role` key |

### Verify step 2

GitHub → **Actions → Tests → Run workflow**.

**Expect: green tick, `112 passed` in the log.** If it fails on an import
or a missing secret, fix that before going further — every other workflow
uses the same two secrets.

---

## STEP 3 — Trigger the workflows, in this order

GitHub → **Actions** → pick the workflow → **Run workflow** → `Run
workflow`. Wait for each to go green before starting the next. Run them in
exactly this order — each one needs what the previous wrote.

### 3.1 Observations (IEM METAR)

Actions → **Observations (IEM METAR)** → Run workflow. Input `days`: leave
default.

Verify:
```sql
select count(*) as observations, max(valid_at) as newest from weather_observations;
```
**Expect: `observations` in the tens of thousands, `newest` within the last
few hours.**

### 3.2 Forecasts (Open-Meteo Previous Runs)

Actions → **Forecasts (Open-Meteo Previous Runs)** → Run workflow.

This is the slow one — it back-fills history and has a soft deadline, so it
may stop early and tell you to re-run. Re-run it until it stops saying so;
it always resumes with the least-covered cities first, so no work is lost.

Verify:
```sql
select count(*) as forecasts, count(distinct city_key) as cities,
       min(for_date) as earliest, max(for_date) as latest
from weather_forecasts;
```
**Expect: `cities = 54`** (or however many are `active` in `cities`), and a
multi-year span between `earliest` and `latest`.

### 3.3 Measure Forecast Skill

Actions → **Measure Forecast Skill** → Run workflow.

Verify:
```sql
select city_key, lead_days, n_days, round(mae_c,2) as mae_c, round(bias_c,2) as bias_c
from derived_forecast_skill
where computed_at = (select max(computed_at) from derived_forecast_skill)
order by mae_c limit 10;
```
**Expect: one row per city per lead day, `n_days` in the hundreds.**
A city with `n_days` under 200 is flagged in the UI as too thin to trust —
that is deliberate, not a bug.

### 3.4 Probability + Edge Pipeline

Actions → **Probability + Edge Pipeline** → Run workflow.

Verify:
```sql
select count(*) as probabilities from band_probabilities
where computed_at > now() - interval '1 hour';
select count(*) as edges, count(*) filter (where tradeable) as tradeable
from edges where computed_at > now() - interval '1 hour';
```
**Expect: `probabilities` > 0 and `edges` > 0.** `tradeable` may legitimately
be 0 — it means nothing currently clears the tradeability gates.

```sql
select count(*) as opportunities from v_opportunities;
```
**Expect: > 0.** This is the number the whole frontend hangs off. If it is 0
here but `edges` was not, check `markets.resolution_date >= current_date` —
`v_opportunities` deliberately hides past markets.

### 3.5 Signal Engine

Actions → **Signal Engine** → Run workflow.

Verify:
```sql
select count(*) as signals_24h from signals where fired_at > now() - interval '24 hours';
```
**Expect: 0.** All eight strategies ship disabled, so nothing fires yet. A
non-zero count here means a strategy is already enabled — check
`select strategy_id, enabled from strategies where enabled`.

### 3.6 Live Weather Monitor

Actions → **Live Weather Monitor** → Run workflow.

Verify:
```sql
select city_key, round(temp_c,1) as temp_c, round(running_max_c,1) as running_max_c,
       trend, peak_window_state, day_decided, observed_at
from live_weather order by city_key limit 10;
```
**Expect: one row per active city, `observed_at` within the last hour or so.**

### 3.7 Derived Recompute (capacity + correlation)

Actions → **Derived Recompute (capacity + correlation)** → Run workflow.

Verify:
```sql
select count(*) as capacity_rows from derived_capacity
where computed_at > now() - interval '1 hour';
```
**Expect: > 0** (one row per city per snapshot hour).

Correlation needs at least 20 overlapping forecast-error days per city
pair, so this can legitimately be 0 on day one:
```sql
select count(*) as correlation_pairs from derived_city_correlation;
```
**Expect: 0 is fine now; > 0 once ~20 days of overlap exist.**

### 3.8 Market volume

Volume is a first-class input across the whole platform (ranking, thin-market
warnings, the calculator, the goals feasibility read), and it comes from
`trades_observed`.

```sql
select count(*) as trades, max(observed_at) as newest from trades_observed;
```

- **If `trades` > 0**: recompute the derived rollups —
  Supabase SQL editor: `select refresh_derived();`
  **Expect: `{"city_day_volume_rows": N, "band_day_volume_rows": M}` with N, M > 0.**

- **If `trades` = 0**: `trades_observed` is Phase 0 market data that this
  repo does not ingest. Every volume figure in the UI will read `$0` and
  every band will be flagged thin, which is honest — the platform is
  telling you it has no evidence anyone trades these markets. Nothing
  else breaks. Load `trades_observed` from your Phase 0 pipeline when you
  can, then run `select refresh_derived();`

Verify either way:
```sql
select count(*) as bands_with_volume from v_band_volume;
select coalesce(sum(volume_usd),0) as total_24h_volume from v_city_volume;
```

### 3.9 Verify Resolution Source  (do this before trusting settlement)

Actions → **Verify Resolution Source** → Run workflow. Leave both inputs
blank — it picks a city with a resolved past market and checks yesterday.

This is the check that gates settlement, and it could not be done from the
build sandbox (no egress to weather.gov). A runner has normal internet
access, so it happens here instead.

**Expect one of two outcomes**, both useful:

- **Green** — it prints a block with the max temperature from all three
  surfaces (our IEM archive, `api.weather.gov`, and
  `weather.gov/wrh/timeseries`, the one that actually settles) and the
  spread between them. Paste that block into
  `docs/settlement_verification.md`.
- **Red, on the parser** — `fetch_resolution_source_reading()` in
  `scripts/settlement.py` has never been checked against a live page, so
  this is the likely first result. The log then dumps the real page
  structure. Fix the parser against it and re-run. **Do not hand-write a
  temperature into the doc.**

Only once it is green, and you have read the numbers yourself, open the gate:
```sql
update settings set value = jsonb_set(value, '{value}', 'true')
where key = 'settlement_verified';
```

### 3.10 Settlement Sweep

Only useful once a market has actually resolved. Run it, expect it to do
nothing on day one:

```sql
select count(*) as settled from markets where resolution_verified_at is not null;
```
**Expect: 0 today.**

**Before trusting automatic settlement**, read
`docs/settlement_verification.md` and run its spot-check. Settlement stays
gated until you flip it:
```sql
select value from settings where key = 'settlement_verified';
```
**Expect: `{"value": false, ...}`.** Leave it false until the spot-check passes.

---

## STEP 4 — Vercel

1. Vercel → **Add New… → Project** → import this repo.
2. **Root Directory**: click *Edit* and set it to `web`. This is the one
   setting that matters — `web/vercel.json` is written for it, and the
   build fails without it.
3. Framework Preset: Vercel will detect **Next.js**. Leave Build Command,
   Output Directory and Install Command on their defaults — `web/vercel.json`
   already sets them.
4. **Environment Variables** — add exactly these two, for *Production*,
   *Preview* and *Development*:

| Name | Value |
|---|---|
| `NEXT_PUBLIC_SUPABASE_URL` | `https://YOURPROJECT.supabase.co` |
| `NEXT_PUBLIC_SUPABASE_ANON_KEY` | the **publishable** key — `sb_publishable_…`, or the legacy `anon` JWT |

> ### ⚠ Get this one right
>
> **Never put a secret key in any `NEXT_PUBLIC_` variable.** They are
> compiled into the JavaScript every visitor downloads, so a secret key put
> here is published the moment the page is served — and then has to be
> **rotated**, not just replaced.
>
> Supabase renamed these keys, which is the usual cause of the mistake:
>
> | | Browser (Vercel) | Server only (GitHub Actions, n8n) |
> |---|---|---|
> | Newer projects | `sb_publishable_…` | `sb_secret_…` |
> | Older projects | `anon` JWT | `service_role` JWT |
>
> If you get it wrong the site tells you: a red **"SECURITY: a secret
> Supabase key is exposed in this build"** banner appears on every page, and
> AD4 refuses to open a connection at all, so no request is ever sent with
> it. Rotate the key in Supabase → Project Settings → API Keys, set the
> publishable one, and redeploy.

5. **Deploy.**

### Verify step 4

The build log should end with:
```
✓ Compiled successfully
✓ Generating static pages (13/13)
Route (app)                              Size     First Load JS
┌ ○ /
├ ○ /analytics
...
```

Then open the deployed URL. **If you see an orange banner reading "Supabase
is not configured"**, the env vars did not reach the build — they are
inlined at build time, so adding them afterwards needs a **redeploy**, not
a restart. Vercel → Deployments → ⋯ → Redeploy.

To confirm from your own machine that the build works with no environment
at all (the exact condition that used to fail):
```bash
cd web && rm -rf node_modules .next && npm install && npm run build
```
**Expect: exit code 0.**

---

## STEP 5 — n8n

**Full instructions: `docs/n8n_setup.md`.** What follows is the short version.

The desk works without n8n — but P0.2–P0.5 are what keep `markets`, `bands`,
`book_snapshots` and `trades_observed` current, so "optional" only applies to
the last three (digests, alerting, the watchdog).

> **If P0.2–P0.5 already run in your n8n** — they do; they are what put the
> data there — do **not** import and activate these files alongside them. Two
> copies writing the same tables is worse than one. `docs/n8n_setup.md` has
> both safe paths: patch your originals, or cut over one at a time.

All ten now carry a **Webhook Trigger** next to their Manual and Schedule
ones, and a **Log run** node that writes `ingest_log` at the end of every
execution. That gives you the **Workflows** page in the UI: run any job on
demand, and see when each last ran and how it went — including the runs that
started from a schedule or from inside n8n.

To turn the Run buttons on: run `sql/ad4_14_workflows.sql`,
`sql/ad4_16_nws.sql` and `sql/ad4_24_nws_gridpoint.sql`, then paste each
workflow's **Production webhook URL** into AD4 → **Workflows** → *set URL*.
Leave them empty and the page stays a read-only status board.

Import each file: n8n → **Workflows → Import from File**.

### 5.1 `n8n/P3.1_email_digests.template.json` — morning brief + EOD report

Open the **Config** node and fill in:

| Field | Value |
|---|---|
| `supabase_url` | `https://YOURPROJECT.supabase.co` |
| `service_key` | the `service_role` key |
| `alert_email` | where the digests go |

Then: **Manual Trigger → Execute Workflow**.
**Expect:** an email titled *AD4 Morning Brief* with a table of ranked
opportunities including **Depth 5c** and **Vol 24h** columns. Rows on
thin-volume bands are shaded.
Activate the workflow (toggle, top right) so its 04:00 / 21:00 UTC
schedules take over.

### 5.2 `n8n/P4.1_health_watchdog.template.json` — every 6 hours

Fill in the same three Config fields. Then **Execute Workflow**.
**Expect:** no email if everything is healthy, and the Summary node reading
`AD4 P4.1: all clear.` On a failure the email lists the failing checks plus
a context line like `market volume 24h: $12,400 across 41 cities, 380
trades`. Activate it.

### 5.3 `n8n/P1.1_live_weather_alerts.template.json` — webhook

Fill in Config, **activate it**, then copy its **Production webhook URL**
from the Webhook Trigger node and register it:

```sql
select update_setting('weather_alert_webhook',
  '{"url": "PASTE_THE_WEBHOOK_URL_HERE"}'::jsonb);
```

> `update_setting` only accepts a whitelist of keys. If it returns
> `{"ok": false, "error": "key not editable from the UI: weather_alert_webhook"}`,
> set it directly instead:
> ```sql
> insert into settings (key, value)
> values ('weather_alert_webhook', '{"url": "PASTE_THE_WEBHOOK_URL_HERE"}'::jsonb)
> on conflict (key) do update set value = excluded.value;
> ```

**Expect:** `scripts/live_weather.py` now POSTs to it whenever it detects a
high or critical weather event.

### 5.4 `n8n/P1.2_nws_monitor.template.json` — every 2 hours

Fill in `supabase_url` and `service_key` only. There is no api.weather.gov
key: it is free, public, and imposes no rate limit. Then **Execute Workflow**.

**Expect:** a Summary like
`AD4 P1.2: 246 NWS observations from 54 cities, 13 not US stations. no new
alerts.` Non-US cities 404 once, are recorded as `nws_supported = false`, and
are never asked again.

The observation count is larger than the city count because this fetches the
**last 26 hours of readings**, not just the newest one. These markets settle
on a daily *maximum*; running every 2 hours, a single `latest` reading would
miss the peak on most days and leave nothing to go back to. The newest
reading drives `live_weather`; all of them are archived under the
`(city_key, valid_at, source)` key, so re-running costs nothing.

Then check the thing this workflow exists to make answerable — whether the
two feeds agree on the same instant:

```sql
select o.city_key, o.valid_at,
       max(o.temp_c) filter (where o.source = 'NWS') as nws_c,
       max(o.temp_c) filter (where o.source = 'IEM') as iem_c
  from weather_observations o
 where o.valid_at > now() - interval '6 hours'
 group by o.city_key, o.valid_at
having count(distinct o.source) > 1
 order by o.valid_at desc limit 20;
```

Both columns populated on the same row is the evidence
`docs/settlement_verification.md` has been waiting for. Activate it.

### 5.5 `n8n/P1.3_nws_forecast.template.json` — every 6 hours

Same two Config fields. **Execute Workflow**.

**Expect:** `AD4 P1.3: 287 NWS forecast day(s) for 41 cities …, 41 partial
day(s) skipped.` Skipped days are correct behaviour, not a fault: the hourly
series starts at the current hour and stops mid-day at the far end, so those
days' maxima would read low — and a low maximum would invent disagreement
with Open-Meteo that is not there.

This is the second forecast model, and it is what switches sigma widening on:

```sql
select city_key, for_date, models, spread_c, sigma_multiplier
  from v_forecast_divergence
 where n_models > 1
 order by spread_c desc limit 10;
```

`sigma_multiplier` above 1.0 means the next Probability Engine run will price
that day less confidently, and its reason string will say
`models_disagree:<spread>C_over_<n>`. It can never drop below 1.0 — two
models agreeing is not evidence that a day is easy. Activate it.

### 5.6 `n8n/P1.4_nws_gridpoint.template.json` — every 6 hours

Same two Config fields. Requires `sql/ad4_24_nws_gridpoint.sql`.
**Execute Workflow**.

**Expect:** `AD4 P1.4: 246 forecast-condition day(s) for 41 cities, 41
partial day(s) skipped.` Same peak-window rule as P1.3, for the same reason.

P1.3 forecasts the temperature. This forecasts **what moves it** — cloud
cover, dewpoint depression, wind, rain, and the morning temperature — off
the raw `/gridpoints/{wfo}/{x},{y}` endpoint. Those are exactly the
quantities `sql/ad4_21_weather_features.sql` measured on days that already
happened, and the columns here carry the **same names**, so the model
`scripts/weather_model.py` fits on observed conditions applies to a forecast
day with no translation step:

```sql
select f.city_key, f.for_date, f.lead_days,
       f.forecast_max_c, f.morning_temp_c, f.dewpoint_depression_c,
       f.cloud_mean, f.wind_mean, f.precip_total
  from weather_forecast_features f
 where f.for_date >= current_date
 order by f.city_key, f.for_date limit 20;
```

Then confirm the two sides really do line up — this is the assumption the
whole design rests on, and a rename on either side breaks it silently:

```sql
select column_name
  from information_schema.columns
 where table_name = 'weather_forecast_features'
intersect
select column_name
  from information_schema.columns
 where table_name = 'v_city_day_features';
```

`dewpoint_depression_c`, `cloud_mean`, `wind_mean`, `precip_total` and
`morning_temp_c` must all appear. Activate it.

### 5.7 Actions → **Weather Model**, then **Model Forecast**

Two GitHub Actions, listed here rather than in step 3 because they only do
anything once 5.6 has run. Requires `sql/ad4_25_model_forecast.sql`.

**Weather Model** (weekly) fits each city's daily maximum against its own
morning conditions and scores it against persistence — yesterday's maximum,
unchanged — on held-out days, in time order. Run it once by hand now.

**Expect** a line per city, and this is the honest outcome either way:

```
nyc            n=284   model MAE 1.42°C  persistence 2.10°C  BEATS persistence by 32.4%
               -1.07°C per okta of daytime cloud; +0.34°C per °C of dryness at 08:00; 0.31 carry-over from yesterday
```

A city that reads `loses to persistence` is a result, not a failure: on that
city's data the morning adds nothing over yesterday, and `beats_persistence`
is the only column that decides whether anything downstream may use it.

**Model Forecast** (every 6h, 15 minutes before the probability pipeline)
applies those stored coefficients to the forecast conditions from 5.6. It does
not refit — the relationship is seasonal and a day of new observations cannot
move it.

**Expect:**
```
118 forward prediction(s) across 34 city/cities from 41 stored fit(s). 9 differ from NWS by more than the model's own error.
  austin         2026-09-05  34.2°C  NWS 32.8  (+1.4)  [observed]
```

Then read where AD4 actually disagrees, and only where its own measured skill
says the disagreement is worth something:

```sql
select city_key, for_date, lead_days, predicted_max_c, nws_max_c,
       disagreement_c, model_mae_c, tradeable_view, prev_source
  from v_model_disagreement
 where tradeable_view
 order by abs(disagreement_c) desc limit 20;
```

`tradeable_view` is false whenever the gap is smaller than the model's own
average error, or the model loses to persistence. Both cases are still
written and still shown — marked, so nothing downstream trusts them blind.

`prev_source` matters as much as the number. `observed` means the prediction
was anchored on a real archived maximum; `chained` means it rests on AD4's own
prediction of the day before, and error compounds with each day out. A day-5
row is not the same object as a day-0 row and the column says so.

This is also the fourth step of the **Why this city** panel on the Board.

---

## STEP 6 — Smoke test: what each page should show

Open the deployed site. There should be **no orange banner** at the top.

The **global bar** (every page) should read something like:
```
Bankroll set bankroll   Open exposure $0.00   Day P&L (net) $0.00
UTC 14:32:07   Next model cycle 5h 12m   Vol 24h $12.4k   Next peak window 38m
```
Click `set bankroll`, type a number, press Enter — it should persist across
a reload. If it does not, `update_setting` is not granted to `anon`: re-run
`sql/ad4_rls.sql`.

| Page | What working looks like |
|---|---|
| **Overview** `/` | Five stat tiles (Cities live, Open positions, Signals 24h, Open P&L, Market volume 24h). A "Top opportunities" table with City / Band / Side / Price / Model P / Net edge / **Vol 24h** / Regime. "Open positions" shows the empty state explaining all eight strategies ship disabled. |
| **Board** `/board` | One row per band per side, sortable. Columns include **Depth (5c)** and **Vol 24h** side by side, plus **Rank** with a `×0.xx` volume factor next to it. The "hide thin-volume markets" checkbox filters. Footer reads `N rows · total 24h volume $X`. Below it, **Why this city** — eight steps, each either a fact or a named gap. Step 4 is AD4's own forward call with its per-driver arithmetic; it says "no forward prediction" until 5.6 and 5.7 have both run. |
| **Opportunities** `/opportunities` | Ranked cards, best first. Two sliders: min confidence and **min 24h volume**. Cards on thin bands carry an amber "Thin market" note. Clicking a card opens the calculator with that band loaded. |
| **Calculator** `/calculator` | Search a city, add legs. Budget and target-profit modes. Each leg shows Model P, **Vol 24h**, and a "Your P" box that stays blank unless you tick *auto-fill from model*. The recommendation panel updates as you type and shows a "Thin volume on N legs" badge when relevant. |
| **City Clusters** `/clusters` | A world map with one node per city, sized by 24h volume; a 24h UTC timeline showing each city's peak window against a "now" line; and a volume-by-city bar chart. |
| **City Monitor** `/monitor` | One city, watched properly. Now / max so far / forecast / implied-by-observation as four figures; the 3-reading and 6-reading slope in °/h and a **rolling over** banner when the short one turns negative while the long one is still positive; today's trace against the forecast and implied maximum; every bucket's mid price over 48h; and the ladder with the **cover pair** flagged. Each panel names the job that fills it when it is empty. |
| **Live Weather** `/live` | One card per city. Pulsing border = inside the peak window; dimmed = day decided; red border = a band was crossed in the last hour; a flash when the running max moves. Click a card for the detail view: a 24h temperature chart with **band overlay** (dashed lines, labelled) and **peak-window shading**, plus the running-max line. Right column is the event feed, labelled `live` when the Realtime subscription is connected. |
| **Analytics** `/analytics` | Forecast skill per city (MAE, bias, MAE in bands, n days — with ⚠ under 200 days). Strategy attribution in **net** P&L. Signal frequency. A liquidity table showing **Depth 5c vs Volume 24h** per city and which shape each city is in (`both`, `quoted, not traded`, `traded, thin book`, `neither`). |
| **Backtest** `/backtest` | The book-depth-history warning banner. A queue form. Queuing writes a `queued` row and says so; GitHub Actions picks it up within ~10 minutes. Selecting a queued run says "Queued — not picked up yet", not a blank panel. |
| **Campaigns** `/campaigns` | A create form listing the eight strategies (each marked `(disabled)`), with a note that a deployment created now will not trade until its strategy is enabled. |
| **Goals** `/goals` | Pick a city, a **risk mode** (Very safe / Safe / Mid / Risky / Custom), and a **Solve** direction: *I set profit* or *I set budget*. The board table lists every band with Yes ¢, No ¢, Model %, Your %, **Vol 24h** and ladder depth; covered rows are highlighted. The spread pane gives budget, profit if a covered band wins, coverage %, tail risk, EV, total leg volume, and cent-exact legs. Four tier cards price the same target across all four risk modes. |

### The one thing to check on Goals

Set **Solve** to *I set profit*, type `100`. Note the "Budget needed" figure.
Now switch to *I set budget* and type that same budget. **"Profit if a
covered band wins" should come back to ≈ $100.** That round-trip is the
whole section working.

### If a page shows a red "Query failed" box

Read the message — it is the raw Postgres error, not a summary.

| Message contains | Fix |
|---|---|
| `relation "x" does not exist` | A SQL file did not run. Go back to step 1 and run `sql/ad4_00_preflight.sql`, then the rest in order. |
| `permission denied for table …` | Expected for a write. The browser key is read-only by design — writes go through the RPCs. If it happens on a **read**, re-run `sql/ad4_13_reconcile.sql`: it re-grants SELECT after rebuilding the views. |
| `permission denied for function …` | `sql/ad4_rls.sql` did not run, or ran before the file that defines the function. Re-run `sql/ad4_rls.sql` then `sql/ad4_13_reconcile.sql` — 13 is last for exactly this reason. |
| Every band shows `$0` volume / `THIN` | `sql/ad4_13_reconcile.sql` has not run, or `trades_observed` is genuinely empty for the lookback window. Check with `select * from ad4_reconcile_report();` — the `trades timestamp column` row shows what is being filtered on. |
| `function jsonb_typeof(integer) does not exist` | You are on the real schema without file 13. `book_snapshots.ask_levels` is an integer there. Run `sql/ad4_13_reconcile.sql`. |
| `operator does not exist: uuid = bigint` | `close_position` still has its old signature. Run `sql/ad4_13_reconcile.sql`. |
| `Supabase is not configured` | Env vars missing from the Vercel build. Step 4, then **redeploy**. |
| `column ... does not exist` | Re-run `sql/ad4_00_preflight.sql` and read its NOTICE output. |

### If a page shows a dashed "empty state" box

That is not a failure — it says which workflow fills that table. Run it.

---

## STEP 7 — Enabling a strategy (when you are ready)

Nothing trades until you do this, and you should not do it on day one.

```sql
-- read the six first
select strategy_id, name, side, capital_cap_pct, max_concurrent, enabled
from strategies order by strategy_id;

-- enable exactly one
update strategies set enabled = true where strategy_id = 's2_combination_arb';
```

Signals still require approval unless you turn that off too:
```sql
select value from settings where key = 'auto_approve';   -- {"value": false, ...}
```
Leave it false. Approve signals by hand in the right-hand Signals panel
until you have enough evidence to trust the strategy.

---

## What is still provisional

These are placeholders with **no evidential basis**. They are all
UI-settable and all labelled as such in the schema. Replace them with
measured values once there is enough history to measure:

```sql
select key, value from settings
where key in ('max_slippage_cents','risk_limits','correlation_warn_threshold',
              'weather_alerts','volume_thresholds')
order by key;
```

- `max_slippage_cents` — 5c
- `risk_limits` — per-band / per-city / exposure / daily-loss caps
- `correlation_warn_threshold` — 0.6
- `weather_alerts` — spike/drop thresholds, day-decided rules
- `volume_thresholds` — thin-market cutoffs and the ranking's liquidity
  half-saturation constant

Also unproven until you check them yourself:
`docs/skill_baseline.md` (forecast skill vs. a real baseline) and
`docs/settlement_verification.md` (the resolution-source parser).
