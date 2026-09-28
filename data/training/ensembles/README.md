# The ensemble record (plan v2.4 P2.10 part 1)

What each weather ensemble said about the next days, kept from 28 Sep 2026 on.

Open-Meteo keeps ensemble members for only about four days. Measured 28 Sep: 96 of 168 hours of `past_days=7` had members, and 1 Sep had none. So this record cannot be rebuilt afterwards.

Written once a night by `scripts/ensemble_record.py`, a step of `archive_observations.yml`. It is committed with the nightly mirror. A key already stored is kept, not rewritten.

## Source

Open-Meteo's Ensemble API: hourly `temperature_2m`, every member, at each active city's coordinates. Members per model:

| model | members |
|---|---|
| `ecmwf_ifs025` | 50 + control |
| `gfs025` | 30 + control |

Each model's run initialisation and publication times come from Open-Meteo's `static/meta.json`:
- `ecmwf_ifs025_ensemble` (`last_run_initialisation_time`, `last_run_availability_time`);
- `ncep_gefs025` (the same fields).

On 28 Sep, ECMWF's 00Z run was available at about 12:07Z.

## `ensemble_daily.csv.gz`

One row per (city, model, run, local day, window). The columns:
- `city_key`, `model`
- `run_init`, `run_available` (ISO UTC)
- `local_date` (the city's wall-clock day)
- `window`:
  - `00_23` is the whole day;
  - `00_17` is the honest record's window.
- `n_members`, `mean_c`, `sd_c`
- `p10_c` to `p90_c`: the members' daily maxima
- `fetched_at`

A local day is kept only when every member has at least 20 hourly values for `00_23`, or at least 14 for `00_17`.
