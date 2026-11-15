# The archive, and how to query it from your own machine

Cold history moves through **export → verify → push → retire the live copy**.
The repository copy remains readable through `/api/archive`. Copying alone
is a backup; it does not reduce the live database's storage.

## Archive and mirror

- `data/archive/` contains the verified exports used by guarded retirement.
  `web/public/archive/index.json` identifies the committed files.
- `data/mirror/` backs up ended days and snapshots from the other supported
  tables. Its manifest records rows, key ranges and SHA-256. **Mirroring does
  not retire database rows**, nor prove that every table has a cold reader.
- Private side-aware financial/research evidence must use a verified private
  destination. Do not add those packets to a public repository.

## Running a recovery

Actions → **Archive Observations** → Run workflow on `main`:

| Input | Recovery setting | Meaning |
|---|---|---|
| `table` | `all` | Attempt each supported dataset independently |
| `keep_days` | blank | Existing per-table defaults and safe pressure floors |
| `archive_only` | `true` | Archive, commit, headroom check and mirror; skip enrichment/scoring |
| `commit` | `false` | Export and push verified copies; retain live rows |
| `commit` | `true` | Also retire eligible live copies after the confirmed push |

`commit=false` is **not** a workflow dry run: files still upload and commit.
The scheduled clock omits `archive_only`, so its existing enrichment and
scoring steps continue. Recovery adds no schedule and changes no retention
floor. It leaves the future-dated station-report guard intact.

Each dataset must export completely and pass its independent database/cache
checks. Before retirement, the script re-counts its file and compares its
blob with a fresh remote fetch. Failed exports or failed pushes cannot
retire that dataset's live rows. A dataset refusal stays visible while the
others continue. Verify the archive-index commit and `/api/archive` readback
as well as the export step; a green upload alone is insufficient.

## Capacity is a separate acceptance check

`storage_acceptance.py` records measured database size and the target from
`storage_pressure()`. Archive success does not make an over-capacity database
healthy. The nightly mirror also covers tables that remain fully live;
these need reader-by-reader cold-tier work before retirement is safe.

`request_reclaim` acknowledges routine autovacuum with `completed=false`.
Autovacuum makes pages reusable; it does not promise to shrink the allocated
files. Do not restore blanket `VACUUM FULL` schedules: full rewrites can block
collection and readers and exhaust disk IO. Any physical compaction needs a
measured relation-level plan, adequate temporary space and an available
maintenance connection. Check size again after completion.

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
