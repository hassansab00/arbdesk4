# The honest training record (plan v2.2 P2.9)

What each public forecast said about a day **before** that day, for every
active city, 14 Jul 2025 to 25 Sep 2026. The desk's own weather model used to
train on afternoon weather that had already been observed (`derived_city_day_features`);
nothing in these files was known later than the call it would inform.

## Source

Open-Meteo's Previous Runs API (`previous-runs-api.open-meteo.com/v1/forecast`),
hourly, at each city's coordinates in `public.cities`. Fetched on 26 Sep 2026
through the database's `pg_net` (the sandbox's shared address was over
Open-Meteo's daily limit).

**Days are the city's wall-clock days** (its IANA zone in `public.cities`, with
daylight saving). The first build used `timezone=auto`, and Open-Meteo returns
the whole range at ONE fixed offset - the city's offset at the moment of the
request (measured: every series exactly 439 x 24 hours, no daylight-saving step
in any of 48 cities; Wellington +13 for July). That put a daylight-saving
city's winter days an hour off, and would have made the same day come out
differently when re-fetched in winter. The record was rebuilt the same evening
from the same answers, each hour turned back into UTC with its fixed offset and
grouped by `scripts/honest_record.py` (`local_stamps`, `previous_rows`) - the
code the nightly append runs, which now asks for `timezone=GMT`. 6,336 of the
42,144 best_match rows changed, all in daylight-saving cities, almost all
November-March (and Wellington's own winter); `tmax_00_17_c` moved by 0.042 C
on average, 5.3 C at most.

`lead_days` N means the `_previous_dayN` series: for an hour H, the value from
a run started at least N x 24 h before H.

## Which hours

Every window ends by **17:00 local** (wall clock). A run started by 17:00 the day before and
published within about 7 hours is out before the local midnight the day-ahead
call is frozen at; a later hour could come from a run published after it. The
publication delay is assumed from the providers' schedules, not verified run by
run (plan P2.6, P7.2). `tmax_c` is the whole day's maximum and is kept only as
the comparison the engine used to read; it is not a training input.

## Files

`best_match_daily.csv.gz` - Open-Meteo best_match, one row per (city, lead, day):

| column | hours (local) |
|---|---|
| `tmax_c` | 00-23, comparison only |
| `tmax_00_17_c` | 00-17 |
| `tmin_00_08_c` | 00-08 |
| `t_08_c`, `td_08_c`, `pressure_msl_08_hpa` | 08 |
| `td_09_17_c`, `cloud_09_17_pct`, `cloud_max_09_17_pct`, `wind_09_17_kmh` | 09-17 |
| `shortwave_06_17_wh_m2` | 06-17 (sum of hourly W/m2) |
| `precip_00_17_mm` | 00-17 |
| `n_hours` | hours with a temperature (days with fewer than 20 are left out) |

`best_match_hourly_day1_utc.csv.gz` - Open-Meteo best_match `_previous_day1`,
one row per (city, UTC hour): `t`, `td` (C), `cloud` (%), `sw` (W/m2), `wind`
(km/h), `precip` (mm), `pmsl` (hPa). Built once on 26 Sep through pg_net
(`timezone=auto`, each local hour turned back into UTC with the answer's fixed
offset, so Lucknow's stamps sit at :30), local days 14 Jul 2025 - 25 Sep 2026,
505,728 rows. The nightly append does not extend it. **Extended 5 Oct** by
`tools/wxpredict/fetch_forecasts.py` the same way, local days 24 Sep - 4 Oct:
10,368 new hours (48 cities x 216). The 2,304 hours that overlap the file
were identical value for value. No existing row was rewritten: all 505,728
are unchanged, and the file now holds 516,096 rows. An hour H's value comes from a run started at least 24 h
before H (published, by the assumption above, by H - 17 h).

`models_daily.csv.gz` - the seven models the desk combines (ECMWF IFS 0.25,
GFS, ICON, UKMO, JMA, GEM, Meteo-France), one row per (city, lead, model, day):
`tmax_c` (00-23) and `tmax_00_17_c`.

## Counts on 26 Sep

- best_match: 42,144 rows = 48 cities x 2 leads x 439 days.
- models: 292,655 rows. UKMO has 1,851 fewer lead-1 days than the others, GEM 223, Meteo-France 146, ICON 133.
