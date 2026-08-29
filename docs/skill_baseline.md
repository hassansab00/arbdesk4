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

1. Run the workflow (either is fine — the fix is now on this branch and
   will be on `main` once merged):
   - GitHub UI → Actions → "Measure Forecast Skill" → Run workflow →
     `days = 400` (or via `gh workflow run skill.yml -f days=400`), **or**
   - locally: `SUPABASE_URL=... SUPABASE_SERVICE_KEY=... PYTHONPATH=scripts
     python scripts/measure_skill.py 400`
2. Before trusting the new numbers, sanity-check the filter change: compare
   total `weather_observations`/`weather_forecasts` row counts pulled per
   city against a run from before this fix (e.g. re-run against a couple
   of cities on the previous commit and diff the counts). They must match.
3. Copy the full printed table (`CITY / n / MAE C / bias / bands /
   within1`) and the `VERIFY` line the script now prints into this file,
   under a `## Measured <date>` heading.
4. Run the SQL verification query from the spec against
   `derived_forecast_skill` and paste the result:

   ```sql
   select
     count(*) as rows,
     count(distinct city_key) as cities,
     min(n_days) as worst_sample,
     round(avg(mae_c)::numeric,2) as avg_mae,
     round(avg(mae_bands)::numeric,2) as avg_bands
   from derived_forecast_skill
   where computed_at = (select max(computed_at) from derived_forecast_skill);
   ```

   `worst_sample` should be 200+. List any city below that explicitly —
   its confidence gets downgraded in the probability engine (Task 4;
   see `scripts/probability_engine.py`, which already reads `n_days` and
   `bias_c >= mae_c` per the spec's "handling untrusted cities" rule and
   will downgrade automatically once this table has real, complete rows).

## Downstream dependency

Every later task in this build (probability engine sigma, regime
thresholds, strategy gating) reads `derived_forecast_skill` live at
run time — nothing in this repo hardcodes the old `[MEASURED]` numbers
from the spec's §0.4. Once this file is filled in with a real run, no
code changes are needed elsewhere; the pipeline already reads whatever is
latest in the table.
