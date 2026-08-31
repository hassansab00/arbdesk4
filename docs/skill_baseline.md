# Forecast skill baseline — TODO: unmeasured

**Status:** Not yet re-measured against complete data. This file is a
placeholder, not a result. Do not treat any number below as real — there
are none yet, deliberately.

## Why this file exists

Task 1 of `AD4-COMPLETE-BUILD-SPEC.md` requires re-running
`scripts/measure_skill.py` now that all 54 cities have 832+ days of
forecast coverage, because the `[MEASURED]` table in §0.4 of the spec was
computed when city coverage was wildly uneven (10 days for some cities,
900+ for others) and is explicitly flagged there as suspect.

This Claude Code session does not hold `SUPABASE_URL` /
`SUPABASE_SERVICE_KEY`, and the GitHub App token available to it returned
`403 Resource not accessible by integration` when it tried to dispatch
`.github/workflows/skill.yml` via the Actions API. So the actual
re-measurement has not happened yet from here — writing numbers into this
file without running the job would be exactly the invented-empirical-claim
mistake §0.3 rule 1 exists to prevent.

## What was fixed (this session)

`scripts/measure_skill.py` used a fragile PostgREST filter pattern —
mixing a `valid_at`/`for_date` key with a separate `and` key wrapping a
single condition. Replaced with PostgREST's documented range syntax:
repeat the same column key once per bound, passed as a list of tuples
(`requests` supports duplicate keys via a list, not a dict). This changes
*how* the range is expressed, not *which* rows match it — row counts
should be identical before and after. That could not be verified against
live data for the reason above; verify it as the first step below.

## How to actually produce this baseline

**One button plus one paste.** This never needed anything the build
sandbox lacked except credentials.

### 1. Run it

> GitHub -> **Actions -> Measure Forecast Skill -> Run workflow** ->
> `days = 400`

(or locally:
`SUPABASE_URL=... SUPABASE_SERVICE_KEY=... PYTHONPATH=scripts python scripts/measure_skill.py 400`)

### 2. Sanity-check the filter change first

`scripts/measure_skill.py` had a fragile PostgREST filter (a `valid_at` /
`for_date` key mixed with a separate `and` key wrapping one condition),
replaced with PostgREST's documented repeated-key range syntax. That
changes *how* the range is expressed, not *which* rows match, so the
per-city row counts in the log must be unchanged from a pre-fix run. If
they are not, stop - the filter is now selecting different data and every
number below it is wrong.

### 3. Paste the result

Copy the full printed table (`CITY / n / MAE C / bias / bands / within1`)
and the `VERIFY` line into a `## Measured <date>` heading below.

### 4. Confirm the sample size in SQL

```sql
select
  count(*)                          as rows,
  count(distinct city_key)          as cities,
  min(n_days)                       as worst_sample,
  round(avg(mae_c)::numeric, 2)     as avg_mae,
  round(avg(mae_bands)::numeric, 2) as avg_bands
from derived_forecast_skill
where computed_at = (select max(computed_at) from derived_forecast_skill);
```

`worst_sample` should be 200 or more. **List any city below that
explicitly** in the section you paste - the probability engine already
downgrades those cities' confidence automatically
(`scripts/probability_engine.py` reads `n_days` and applies the
`bias_c >= mae_c` rule), so this is about you knowing which cities are
thin, not about changing code.

`sql/ad4_99_verify.sql` also reports whether `derived_forecast_skill` has
rows at all, if you just want to know whether the job has ever run.

## Measured

_(nothing yet - paste the run's table here)_

## Downstream dependency

Every later task in this build (probability engine sigma, regime
thresholds, strategy gating) reads `derived_forecast_skill` live at
run time — nothing in this repo hardcodes the old `[MEASURED]` numbers
from the spec's §0.4. Once this file is filled in with a real run, no
code changes are needed elsewhere; the pipeline already reads whatever is
latest in the table.
