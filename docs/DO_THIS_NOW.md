# Do this now

The short version. `docs/GO_LIVE.md` is the long one — this is only the steps
that are outstanding, in order, with nothing else in the way.

Four parts. **A** takes 10 minutes, **B** takes 20, **C** takes 10, **D** is the
check that it worked.

---

## A — Run 6 SQL files

Supabase → **SQL Editor** → paste the whole file → **Run**. One at a time,
**in this order**. Each one is safe to run again if you are unsure.

| # | File | Why |
|---|---|---|
| 1 | `sql/ad4_diagnose.sql` | **Read-only. Changes nothing.** Tells you what is broken before you fix anything. Read the grid it prints. |
| 2 | `sql/ad4_17_city_stats.sql` | Fixes the wrong temperature (Chicago 98 °F). |
| 3 | `sql/ad4_19_stats_cache.sql` | Same fix, in the version that actually wins. **Must come after 17.** |
| 4 | `sql/ad4_24_nws_gridpoint.sql` | The one that failed for you. It no longer stops on a missing view. |
| 5 | `sql/ad4_25_model_forecast.sql` | New. AD4's own forecast. |
| 6 | `sql/ad4_26_temp_trend.sql` | New. Temperature direction + speed. Feeds the City Monitor page. |
| 7 | `sql/ad4_strategies_seed.sql` | Adds the three new strategies. Safe: it never overwrites an existing row. |
| 8 | **`sql/ad4_21_weather_features.sql`** | **It changed** — gained `wind_max` and `pressure_change_24h_hpa`, which step 9 needs. Re-run it even if you ran it before. |
| 9 | **`sql/ad4_28_feature_cache.sql`** | **Fixes the `statement timeout` you were seeing everywhere.** Adds the missing index and caches the four views that scanned the whole archive on every page load. Must come after 26. |

| 10 | **`sql/ad4_29_retention.sql`** | Lets you archive cold observations out of Supabase so the free tier lasts. See **Section F**. |

### If step 9 refuses

It checks two things and says which failed:

- *"v_city_day_features does not exist"* — step 8 has not run. Run it.
- *"exists but is the OLD version"* — you ran `ad4_21` before it changed. Run
  step 8 again; it is safe to re-run.

Step 8's own last act is to check it really created all four of its objects and
raise if not, so "Success. No rows returned." from it now means something.
That file now refuses to say "success" unless it really created everything, so
you will know either way.

---

## B — Import 6 n8n workflows

### Do NOT import these four

`P0.2`, `P0.3`, `P0.4`, `P0.5` already run in your n8n, and they are what put
841 markets and 122,000 trades in the database. The files in this repo are
**reconstructions** — the Supabase half is grounded in the real schema, but the
Polymarket endpoints in them were never verified against your working setup.

So: **do not swap a working ingest for a reconstruction.** Importing them
alongside is worse — two workflows writing the same tables.

They do miss one thing the six below have: the schedule gate. If you want that
on your existing four, add it to them rather than replacing them — see
**Section C2** below.

### Import these six

n8n → **Workflows → Import from File**. One at a time.

| File | New or re-import |
|---|---|
| `n8n/P1.1_live_weather_alerts.template.json` | re-import (the schedule bug is fixed) |
| `n8n/P1.2_nws_monitor.template.json` | **re-import** — it now reads the whole day's readings, not one |
| `n8n/P1.3_nws_forecast.template.json` | new |
| `n8n/P1.4_nws_gridpoint.template.json` | new |
| `n8n/P3.1_email_digests.template.json` | new |
| `n8n/P4.1_health_watchdog.template.json` | new |

If you already imported an older copy of one of these, delete the old one first.
Two copies is the same problem as above.

### Then, in each one

Open the **Config** node. Every workflow has `supabase_url` and `service_key`;
this is everything else, and what it means.

| Workflow | Also fill in | Comes pre-filled — leave alone |
|---|---|---|
| P1.1 | `alert_email` | — |
| P1.2 | *nothing* | `user_agent`, `alert_events`, `observation_hours` 30, `observation_limit` 60, `max_cities_per_run` 0 |
| P1.3 | *nothing* | `user_agent`, `model_label` nws, `max_horizon_days` 7, `peak_window` 12-18, `max_cities_per_run` 0 |
| P1.4 | *nothing* | `user_agent`, `max_horizon_days` 7, `peak_window` 12-18, `morning_hour` 8, `max_cities_per_run` 0 |
| P3.1 | *nothing* | `digest_kind` — an expression that reads the webhook body |
| P4.1 | `alert_email` | — |

What the pre-filled ones do, in case you ever want to change one:

- `max_cities_per_run` / `max_bands_per_run` — **0 means no limit.** Set a number
  only if a run is timing out and you want it to work through the list in
  batches.
- `observation_hours` 30 / `observation_limit` 60 — how far back P1.2 asks
  weather.gov for readings. 30 hours covers a full local day in every timezone
  with margin; 60 readings is enough for hourly METAR plus SPECIs.
- `peak_window` 12-18 — the local hours a day's maximum must be covered by. A
  day whose forecast misses this window has an understated maximum and is
  skipped rather than written wrong.
- `morning_hour` 8 — which local hour P1.4 calls "morning" for dewpoint
  depression. 8 is after sunrise, so the air mass has declared itself, but
  before the afternoon it is trying to predict.
- `user_agent` — weather.gov asks apps to identify themselves. No key, no
  sign-up, no rate limit.
- `alert_events` — which NWS event names raise a weather event. Widen it if you
  want cold-weather alerts too.

> **The secret key goes in n8n and GitHub Actions only.** Never in Vercel, never
> in anything starting `NEXT_PUBLIC_`. That would put it in the browser.

### Then test each one

Press **Execute Workflow** (bottom of the screen). Watch the last node,
**Summary**. It prints one line saying what happened. Red means read the red
node — it says what to fix.

### Then turn them on

Toggle **Active**, top right.

---

## C — Your n8n executions are protected

Every one of these files asks the database whether it should run **before it
does any work**. If the answer is no it stops at the second node, which costs
almost nothing. So being Active does not mean it runs every time its schedule
fires.

You control that from Supabase, not from n8n:

```sql
-- see every workflow's cadence and the monthly total
select * from v_execution_budget;
```

```sql
-- turn one off completely
update settings
   set value = jsonb_set(value, '{P1.4_nws_gridpoint,mode}', '"off"')
 where key = 'workflow_schedules';

-- or: only run it when you press Run in the UI
update settings
   set value = jsonb_set(value, '{P1.4_nws_gridpoint,mode}', '"manual"')
 where key = 'workflow_schedules';

-- or: change how often
update settings
   set value = jsonb_set(value, '{P1.2_nws_monitor,every_minutes}', '240')
 where key = 'workflow_schedules';
```

`mode` is one of `auto`, `manual`, `off`.

### C2 — Putting the gate on your existing P0.2–P0.5

Those four were built before `should_run()` existed, so nothing above controls
them — they run on whatever schedule is set inside n8n. To bring them under the
same control without replacing them:

1. Open `n8n/schedule_gate.snippet.json`, select all, copy.
2. In n8n, open your existing P0.3 (say) and **paste onto the canvas**. Four
   nodes appear: *Gate config → Check schedule → Run now? → Stop if skipped*.
3. Fill in `supabase_url` and `service_key` in **Gate config**, and set `job` to
   the matching key: `P0.2_market_discovery`, `P0.3_book_volume_snapshot`,
   `P0.4_trade_history` or `P0.5_refresh_rules_text`.
4. Rewire: your **Schedule Trigger → Gate config**, and **Stop if skipped →**
   whatever your trigger used to connect to.
5. Save.

The snippet carries its own two credential boxes rather than reading a `Config`
node, because it cannot know what you named yours.

**The gate fails open.** If the settings row is missing or the RPC is
unreachable, the workflow runs anyway and logs why. A gate that failed closed
would silently disable a job for a reason nobody could see, which is worse than
one that occasionally runs too often.

**The six you are importing cost 780 runs a month** at their default cadences
(P1.2 is 360 of those; P1.1 is event-driven and not counted). Your existing
P0.2–P0.5 are on top of that and are not governed by this table — they were
built before it existed, so they run on whatever schedule is set inside n8n
itself. Against a ~2,000/month cap that leaves room, but check
`v_execution_budget` rather than trusting a number in a document.

---

## D — Run the GitHub Actions, in order

GitHub → **Actions** → pick the workflow → **Run workflow**.

Order matters — each one needs what the one above it wrote.

| # | Action | What it needs first |
|---|---|---|
| 1 | Observations (IEM METAR) | nothing |
| 2 | Forecasts (Open-Meteo) | nothing |
| 3 | Measure Forecast Skill | 1 and 2 |
| 4 | Probability + Edge Pipeline | 3, and a book from n8n P0.3 |
| 5 | Signal Engine | 4 |
| 6 | Derived Recompute | trades from n8n P0.4 |
| 7 | Weather Model | 1 (needs ~120 days per city) |
| 8 | Model Forecast | 7, and n8n **P1.4** must have run |

7 and 8 are the new pair. **Weather Model** fits the model; **Model Forecast**
applies it to tomorrow. Fitting is weekly, predicting is every 6 hours — that is
why they are two separate actions.

---

## E — Check it worked

Run `sql/ad4_diagnose.sql` again and read the five sections:

| Section | What good looks like |
|---|---|
| 1 OBJECTS | every row says `ok` |
| 2 FRESHNESS | observations minutes old, book minutes old, forecast hours old |
| 3 TEMPERATURE | no row says `SUSPECT`, and the lead is 1 or 2, not 7 |
| 4 BUCKETS | every market matches the widest ladder that day |
| 5 TRADING | see below |

### Section 5 will say `strategies enabled: 0 of 8`

That is why **Signals is empty**. It is not a bug and it is not n8n — all eight
strategies ship switched off on purpose. Turn them on one at a time:

```sql
update strategies set enabled = true where strategy_id = 's8_two_bucket_cover';
```

Read `docs/strategies.md` first. One at a time, so that when two strategies want
the same band you can tell which one changed.

### Then look at the pages

| Page | What is new |
|---|---|
| **City Monitor** `/monitor` | New page. One city: which way the temperature is moving and how fast, today's trace, every bucket's price over 48h, and the cover pair. |
| **City Clusters** `/clusters` | Regions are correct now. Every temperature shows its lead and run age underneath. |
| **Board** `/board` | The "Why this city" panel has a 4th step: AD4's own forecast and the reasoning behind it. |

---

## If a bucket count looks short

Section 4 of the diagnostic compares each market against the widest ladder on
the same day. If your markets show 6 buckets and the widest is 11, five were not
captured and the fix is on the n8n **P0.2 Market Discovery** side — nothing in
this repo can invent a bucket that was never ingested.

This matters for the new **two-bucket cover** strategy: it picks the two most
likely *adjacent* buckets, and on a truncated ladder those two may not be
adjacent, so it will correctly refuse to fire.


---

## F — Staying inside the Supabase free plan

`weather_observations` is normally **98% of the database** — 272 bytes a row in
Postgres, where the same data as gzipped CSV is about **50× smaller**. On a
710k-row archive that is 184 MB against 3.6 MB.

See where yours is going:

```sql
select table_name, total, approx_rows, bytes_per_row from v_storage_report;
```

### The fix: keep a working window, archive the rest

GitHub → Actions → **Archive Observations** → Run workflow.

Leave **commit** unticked the first time. It prints what it *would* do and
changes nothing:

```
feature cache: {"ok": true, "city_days_total": 39420, ...}
591,948 rows -> observations-2024-09-06-to-2026-05-07.csv.gz  (3.6 MB gzipped, ~161 MB in Postgres)
--dry-run: nothing uploaded, nothing deleted.
```

Then run it again with **commit** ticked. It uploads the file as a **GitHub
Release asset** — free, 2 GB per asset, on this repo — reads it back to confirm
the row count, and only then prunes. Monthly from there.

Measured end to end on a 710k-row archive with a 120-day window kept:

| | before | after |
|---|---|---|
| database total | 200 MB | **33 MB** |
| `weather_observations` | 184 MB | 24 MB |
| history the model can still see | 2 years | **2 years** |

That last row is the point. Raw observations only reach back 120 days
afterwards, but `derived_city_day_features` — one row per city-day, which is
what the model and every analytics view actually read — keeps the full two
years. Persistence, the climb profile and the weather-effects tables were all
verified intact after the prune.

### Two guards, and both have to pass

1. The script refreshes the feature cache **first**, uploads, then
   **re-downloads and counts the rows back**. An upload that returns 201 and
   stored a truncated file would otherwise surface months later as a model with
   a hole in it.
2. `prune_observations()` independently refuses if any city-day about to lose
   its raw rows is not already in the cache — so even run by hand, in the wrong
   order, it will not destroy history.

After a big prune, reclaim the space for real:

```sql
vacuum full weather_observations;
```

### About storing it in YouTube

That does work and people do it — but it is against their terms, the data comes
back unqueryable, and it takes minutes per megabyte. The instinct is right:
cold storage should be free and should not be your database. A GitHub Release
is the version of that idea that holds up, and it needs no new account, no new
credential and no new bill.
