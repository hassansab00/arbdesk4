# One morning reading, chosen the same way every time (4 Oct 2026)

## What was wrong
`v_city_day_features` takes one reading nearest 08:00 local for the morning
features: `morning_temp_c`, `morning_dewpoint_c`, `dewpoint_depression_c`,
`morning_humidity` and `morning_pressure_hpa`, and from them
`morning_to_max_c` and `pressure_change_24h_hpa`. The weekly `weather_model`
reads `morning_temp_c`, `dewpoint_depression_c`, `morning_humidity` and the
pressure terms.

The reading was chosen with `distinct on` ordered by the whole local hour
alone, so every reading of 08:xx tied and Postgres took any of them. On the
readings held on 4 Oct:
- 253 of 402 US city-days had tied readings with different temperatures, 251
  of them across the IEM and NWS feeds;
- 431 of 1,220 city-days elsewhere did.

The cached value could therefore change from one nightly refresh to the next
with no new reading.

`supabase/migrations/20261004160000_one_morning_reading.sql` fixes the
choice. It now takes the settlement feed's reading first (source `IEM`), then
the one nearest 08:00 by the minute, then the earlier one. IEM comes first
because every US reading before 5 Sep was IEM, and every other city's still
is, so the feature keeps one meaning across time and cities.

## How the repair was made
All steps were run through the Supabase SQL tool on 4 Oct.

1. **Proof first.** In one REPEATABLE READ snapshot, `EXCEPT ALL` both ways,
   the new view was compared with the live one over the 1,658 city-days from
   2 Sep:
   - the 17 columns that do not depend on the morning reading were identical;
   - the morning reading differed from the live view's current pick on 752
     rows;
   - the same 36 days had no morning reading in both;
   - two reads of the new view agreed.
2. **Before, 09:26:38Z** (`cache_before.txt`). The cached rows from 2 Sep for
   the 48 active cities: 1,580 rows in the format of
   `tools/repair_day_features.py`. The md5 of the lines,
   `1178f33786e8ffd48418bfb68e878e6d`, matched in the database and in the
   file.
3. **The view**, applied with `apply_migration` as `one_morning_reading`.
4. **The refresh.** One `refresh_feature_cache(33, city)` per active city, in
   two statements of 24 cities. All 48 returned ok, touching 1,552 city-days,
   at most 848 ms a city.
5. **After, 09:28:04Z** (`cache_after.txt`). 1,584 rows, md5
   `52559aab910f458c87625d034597a0f5`, matching in the database and in the
   file.

## What changed
Measured from the two files. `tests/test_one_morning_reading.py` re-derives
the count.

| Rows | What happened |
|---|---|
| Days whose reading count did not change | Only the morning columns moved, on **722 rows**: 344 in the US and 378 elsewhere. `morning_temp_c` changed on 644 of them: in the US on 328, by 1.23 °C on average (median 1.11, at most 4.67); elsewhere on 316, by 1.61 °C on average (median 1.0, at most 7.0). |
| 3 – 4 Oct | These days also took the readings that arrived after the nightly refresh (04:55Z); their non-morning columns moved with `n_obs`. Today's partial day was added for 4 cities. |

## Limits
- Cached days before 2 Sep keep the morning value chosen when they were last
  computed. The raw readings behind them are no longer in the database, so
  `refresh_feature_cache` cannot recompute them. The repository's archive
  (`data/archive/observations`) holds them, and a repair from it, like the
  one of 29 Sep, would need its own plan.
- Retired cities (dc and others) were not refreshed; the nightly refresh
  skips them too.
- The effect on the weekly weather fit of 5 Oct is not measured yet. That fit
  is the first on these features and on the US label of
  `data/repairs/2026-10-04-settlement-max`; check C compares it with the last
  one.
