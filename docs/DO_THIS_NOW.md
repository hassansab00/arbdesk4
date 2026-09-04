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
| 7 | `sql/ad4_strategies_seed.sql` | Adds the two new strategies. Safe: it never overwrites an existing row. |

### If step 1 says `v_city_day_features MISSING`

Run `sql/ad4_21_weather_features.sql` first, then do steps 4, 5, 6 again.
That file now refuses to say "success" unless it really created everything, so
you will know either way.

---

## B — Import 6 n8n workflows

### Do NOT import these four

`P0.2`, `P0.3`, `P0.4`, `P0.5` already run in your n8n. The files in this repo
are reconstructions. Importing and turning them on means **two workflows writing
the same tables**. Leave them alone.

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

1. Open the **Config** node.
2. Fill in `supabase_url` — `https://YOURPROJECT.supabase.co`
3. Fill in `service_key` — your Supabase **secret** (`service_role`) key.
4. On **P1.1** and **P4.1** only, fill in `alert_email`.
5. Leave every other box as it is.

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
