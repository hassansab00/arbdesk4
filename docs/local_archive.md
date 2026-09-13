# The archive, and how to query it from your own machine

Supabase's free tier is 500 MB. This database measured **513 MB** — over it —
and nothing had ever been archived, because the archive job failed on 5
September and never ran again.

Nothing here deletes data. It **moves** the cold tail to a file you own, proves
the file is complete, and only then frees the space in Postgres.

## What moves, and what it buys

| Table | Size | Indexes | Older than 180 days |
|---|---|---|---|
| `weather_forecasts` | 124 MB | 78 MB | **269,744 rows (79%)** |
| `weather_observations` | 121 MB | 59 MB | **292,356 rows (56%)** |

Indexes shrink with the rows, which is why the saving is bigger than the row
count suggests: roughly **166 MB**, taking the database to about **330 MB**.

The recent window stays live in Supabase. 180 days is the default because the
weather model needs months to fit and the trend views need a fortnight; below
30 days the prune functions refuse outright.

## The four steps, in order, no exceptions

1. **Refresh the derived cache.** Every city-day about to lose its raw rows
   must already exist as a derived row.
2. **Export** the cold rows to gzipped CSV, paged on the primary key.
3. **Upload to a GitHub Release, then re-download it and count the rows back.**
4. **Only then prune**, and only as far back as what was actually archived.

Step 3 is the one that matters. An upload that returns `201` and stores a
truncated file would otherwise be discovered months later, by a model with a
hole in it. The prune functions independently refuse if step 1 did not cover
the range, so the guard exists on both sides.

Keyset paging in step 2 is also load-bearing. The cutoff columns are nowhere
near unique — 37 cities × 2 sources share a timestamp — and `OFFSET` paging
over a non-unique order silently skips rows, which the prune then deletes
anyway. Both tables page on their primary key, which is a total order.

## Running it

Actions → **Archive Observations** → Run workflow. Leave `commit` unticked the
first time: that does everything except upload and delete, and prints what it
would remove.

```
keep_days: 180
table:     both          (or observations / forecasts)
commit:    ☐             ← tick only after reading the dry run
```

Or locally, with `SUPABASE_URL` and `SUPABASE_SERVICE_KEY` set:

```bash
PYTHONPATH=scripts python scripts/archive_observations.py --keep-days 180 --dry-run
PYTHONPATH=scripts python scripts/archive_observations.py --keep-days 180 --commit
```

**Postgres does not return the space to the operating system on `DELETE`.** The
rows become reusable, the file stays the same size. After a prune, run this in
the Supabase SQL editor — it cannot be run from a migration or a pooled
connection because `VACUUM` refuses to run inside a transaction:

```sql
vacuum full public.weather_forecasts;
vacuum full public.weather_observations;
```

## Where the files live

A GitHub Release on this repository, one asset per run, named by the range it
covers:

```
forecasts-archive       forecasts-2024-01-01-to-2026-03-16.csv.gz
observations-archive    observations-2025-07-22-to-2026-03-16.csv.gz
```

Free, 2 GB per asset, unlimited assets, versioned, and already inside the
pipeline that produced the data. No new account, no new credential, no bill.
You own the repository, so you own the files.

## Loading an archive into local PostgreSQL

The CSV header is the exact column list, so it loads with `\copy` and no
transformation. Install PostgreSQL 16 or 17, then:

```bash
createdb arbdesk4_archive

# Take the schema from the live database - structure only, no rows.
pg_dump "$SUPABASE_DB_URL" --schema-only --no-owner --no-privileges \
        -t public.weather_forecasts -t public.weather_observations \
  | psql arbdesk4_archive
```

Download the assets from the Releases page, then:

```bash
gunzip -c forecasts-2024-01-01-to-2026-03-16.csv.gz > forecasts.csv

psql arbdesk4_archive -c "\copy weather_forecasts \
  (city_key,model,run_at,observed_at,for_date,lead_days,forecast_max_c,variables,source) \
  from 'forecasts.csv' with (format csv, header true)"

gunzip -c observations-2025-07-22-to-2026-03-16.csv.gz > observations.csv

psql arbdesk4_archive -c "\copy weather_observations \
  (city_key,station,valid_at,temp_c,temp_f,dewpoint_c,humidity,wind_speed, \
   wind_dir_deg,precip,cloud_cover,pressure_hpa,source) \
  from 'observations.csv' with (format csv, header true)"
```

The surrogate keys (`forecast_id`, `obs_id`) are deliberately **not** exported.
They identify a row inside one database and mean nothing outside it; the
natural key — city, source and time — is what makes a row unique, and it is all
present. Let the local table generate its own.

Check what you loaded before trusting it:

```sql
select count(*), min(for_date), max(for_date) from weather_forecasts;
select count(*), min(valid_at), max(valid_at) from weather_observations;
```

Those counts must equal what the archive run reported. If they do not, the
load is short — do not prune anything further until you know why.

## Pointing arbdesk4 at the archive

Backtests and research read through `scripts/common.py`, which builds every URL
from `SUPABASE_URL`. Running the same scripts against the local archive is
therefore a matter of pointing them at a PostgREST in front of it rather than
changing any code:

```bash
docker run --rm -p 3001:3000 \
  -e PGRST_DB_URI="postgres://localhost/arbdesk4_archive" \
  -e PGRST_DB_ANON_ROLE=web_anon \
  -e PGRST_DB_SCHEMAS=public \
  postgrest/postgrest

SUPABASE_URL=http://localhost:3001 SUPABASE_SERVICE_KEY=unused \
  PYTHONPATH=scripts python scripts/backtest/runner.py
```

`web_anon` needs creating once (`create role web_anon nologin; grant usage on
schema public to web_anon; grant select on all tables in schema public to
web_anon;`).

Keep the two apart in your head: **Supabase is the live desk**, the last six
months, what the board and the paper desk price against. **The local archive is
research** — backtests, skill measurement, anything asking what happened over
years. They hold the same rows in the same shape; only the window differs.

## What is deliberately not done

- **No Google Sheets.** 705,000 rows × 9 columns is past the 10 M cell ceiling,
  it cannot be queried without an API layer, and the numeric coercion would
  quietly change values.
- **No deleting to save space.** Every prune is preceded by an upload that is
  read back and counted. If verification fails, nothing is pruned.
- **No pruning `book_snapshots`.** Its oldest row is 22 August — there is no
  cold tail yet.
