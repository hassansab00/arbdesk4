# The US station label from the settlement feed, 4 Oct 2026

## What was wrong
`derived_city_day_features.max_c` is the station label that station_mos (nightly),
station_correction, the weekly weather fit and the S10 files train on. It is
cached from `v_city_day_features`, whose `max_c` was the maximum over **every**
reading of the local day.

From 5 Sep the US cities also carry NWS five-minute readings (source `NWS`,
whole °C; first 5 Sep 06:46Z). They sit beside the routine reports the venue
settles on (source `IEM`, `obs_primary_source()`). The maximum of the
five-minute readings runs warm. Measured on the live database on 4 Oct:

- **297 US city-days, 5 Sep – 2 Oct.** The IEM daily maximum was in the venue's
  winning bucket on 286. The maximum over every source was in it on 213.
- **462 US city-days, 1 Aug – 2 Oct.** The cached label averaged 0.187 °C above
  the settlement's observed maximum. 54 days differed by 0.6 °C or more.
- **Before 5 Sep** every US reading was IEM (`data/archive/observations`,
  `data/mirror/weather_observations`), so no earlier label changes.

`supabase/migrations/20261004130000_the_label_is_what_the_venue_reads.sql`
fixes the rule going forward. `max_c` is now the IEM maximum when the day has
any IEM reading, else the maximum over what was measured. `max_c_all_sources`
(the old value) and `max_c_source` are appended.

This directory records the cached rows the fix recomputed. They had to be
recomputed while the raw readings were still held: the oldest held reading was
2 Sep 02:47Z, and `refresh_feature_cache` never recomputes a day the
observation prune has cut into.

## How the repair was made
All steps were run through the Supabase SQL tool on 4 Oct.

1. **Proof first, view-to-view, in one REPEATABLE READ snapshot**, `EXCEPT ALL`
   both ways, over 1,670 city-days:
   - the 17 columns the change does not touch were identical;
   - `max_c` changed on 180 rows, all in US cities, all from 5 Sep onward, and
     was never raised;
   - the day-over-day terms changed only where the day or the day before
     changed.
2. **Before, 08:05:37Z** (`cache_before.txt`). The cached rows from 2 Sep
   onward for the 13 cities that hold NWS readings:
   - the 11 active US cities, `dc` (retired) and `mexico_city` (47 NWS
     readings, none above its IEM maximum);
   - 405 rows, one `|`-joined line each, NULL as `~` (the format of
     `tools/repair_day_features.py`);
   - md5 of the lines `01978dca08398260906b5c4ced1fa584`, equal in the database
     and in the file.
3. **The view**, applied with `apply_migration` as version `20261004080628`.
   Anon then read it: houston returned 34 rows, all `settlement_feed`, 19
   lowered, none raised.
4. **The refresh, 08:06–08:07Z.** One `refresh_feature_cache(33, city)` per
   city, which is what the nightly refresh does with `p_days` null:
   - each call took 80–617 ms;
   - each reported `cut_days_left_as_cached = 1`, which is 1 Sep, left as
     cached.
5. **After, 08:07:17Z** (`cache_after.txt`). 412 rows, md5
   `651cca902672b4e71dd29ceb7df72b17`, equal in the database and in the file.

## What changed
Measured from `cache_before.txt` and `cache_after.txt`.
`tests/test_the_label_is_what_the_venue_reads.py` re-derives these numbers from
the two files.

| Rows | What happened |
|---|---|
| **Settled days 2 Sep – 1 Oct, 11 active US cities and mexico_city** | Only the label columns changed: `max_c`, and the four computed from it (`diurnal_range_c`, `morning_to_max_c`, and the next day's `prev_max_c`, `delta_max_c`). `n_obs` was identical on every row. `max_c` was **lowered on 152 rows and raised on none**: by 0.559 °C on average, by 2.11 °C at most, 60 by 0.6 °C or more, 21 by 1 °C or more. The earliest is 11 Sep. Nothing before 5 Sep moved. mexico_city did not change. |
| **2 – 4 Oct** | Each row also took the readings that arrived after the nightly refresh (04:55Z). For example, chicago 2 Oct went from 339 to 340 readings and los_angeles 3 Oct from 299 to 329. Today's partial day was added for 6 cities (7 new rows with dc 20 Sep). |
| **dc, 2 – 19 Sep** (retired) | The nightly refresh skips retired cities (`common.refresh_feature_cache`), and dc's rows were last computed 19 Sep 09:06Z. This refresh therefore also brought in readings it had never seen: 19 Sep went from 61 to 313 readings (max 24 → 27 °C), 20 Sep was added, and all 18 of its wind vectors, NULL before, were filled (why they were NULL on 19 Sep was not measured). Four dc labels (14, 15, 17, 18 Sep) moved by the settlement-feed rule. |

After the refresh, the cache equals the view on `max_c` and `diurnal_range_c`
for all 412 rows. `prev_max_c` and `delta_max_c` differ from the view only on
2 Sep, by design: the first whole day takes them from the cached 1 Sep, not
from the part of 1 Sep the raw table still holds.

The cached label now matches the settlement. On 30 Sep – 2 Oct, the label
differed from `fact_band_outcome.observed_max_c` by more than 0.05 °C on these
city-days (mean absolute difference in brackets):

| Cities | City-days | Old label | New label |
|---|---|---|---|
| US | 36 | 22 (0.377 °C) | 0 (0.001 °C) |
| Others | 107 | 0 | 0 |

For example, houston on 1 Oct: the old label was 33 °C, the new label is
31.67 °C, and the settlement is 31.67 °C.

## What reads the label, and what this means for it
| Reader | Effect |
|---|---|
| station_mos (nightly), station_correction | They read the cache, so they train on the repaired label from their next run. |
| weather_model (weekly; next fit 5 Oct) | The first fit on the repaired label. Check C on 5 Oct compares it with the last fit. |
| S10 (`data/replay/inputs_2026-09-26/labels_whole_repaired.json.gz`, which rd1 and the rd3 forward shadow are fitted on) | 111 of its 22,484 rows carry the old all-sources label: 12 cities, 11 – 25 Sep, 0.591 °C too warm on average, 2.11 °C at most. **Not refitted.** rd3's forward test runs on frozen versions, so the file stays as measured until that test is decided. The forward rule's coverage label is the repaired cache, the same for rd1 and rd3; see `docs/CHALLENGER_C_PREREG.md`. |

## Seen, not changed here
- **The morning reading can come from either feed.** `v_city_day_features`
  picks the reading nearest 08:00 with `distinct on ... order by
  abs(local_hour - 8)`, which ties on every reading in the same hour. Postgres
  then picks one arbitrarily, so `morning_temp_c` and the terms computed from
  it can differ between two reads of the view. Over the held readings:
  - US cities: 253 of 402 city-days have tied readings with different
    temperatures, 251 of them across the two feeds;
  - the other cities: 431 of 1,220.

  Not caused by this change, and not fixed by it.
- **`v_city_climb_profile_live`** takes its hourly maxima over every source.
  That view was not changed.
