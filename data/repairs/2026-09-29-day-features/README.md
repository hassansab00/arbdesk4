# Repair of `derived_city_day_features`, 29 Sep 2026 (plan v2 P1.6)

## What was wrong
`prune_observations` cuts every city at one instant, part-way through most
cities' local day. The next `refresh_feature_cache` recomputed that day from
the readings the cut left, and overwrote the value it had cached while the day
was whole. The first whole day after each cut also took its `prev_max_c`,
`delta_max_c` and pressure change from the part-day, or from nothing at all.
The fix going forward is
`supabase/migrations/20260929010000_a_cut_day_keeps_its_cached_values.sql`
(#259, applied 29 Sep 06:59:54Z). This directory repairs the rows the prunes
had already damaged.

## How the repair was made
1. **Candidates.** For each of the 14 prune cut instants (`cuts.json`, from
   the `archive_observations` logs) and each of the 54 cities
   (`timezones.json`), the local day of the cut plus the day before and after
   it: 1,098 city-days (`pairs.json`).
2. **Recompute.** `tools/repair_day_features.py` recomputed those days from the
   repository's raw readings (`data/archive/observations` and
   `data/mirror/weather_observations`) exactly as `v_city_day_features` does
   (`recomputed.json`). 1,063 had readings, 35 had none. Validated first on
   104 whole city-days still in the live table: 0 differences from the view.
3. **Before.** The 1,063 cached rows were dumped from the live table in two
   halves, each md5-checked against the database's own md5
   (`cache_before_a-k.txt`, `cache_before_l-z.txt`).
4. **Plan** (`tools/repair_day_features.py plan`, `audit.jsonl`):

   | Kind | Rows | What happened |
   |---|---|---|
   | repair | 561 | Cached from fewer readings than the repository holds. Rewritten. |
   | equal | 437 | Same values. Untouched. |
   | cache_has_more | 3 | dc, jakarta, taipei on 23 Jun: the repository is missing readings. Untouched. |
   | same_count_differs | 62 | dc, jakarta, lagos, taipei: the cache has no wind vector (NULL), and these cities' old rows were never refreshed. Not prune damage. Untouched. |

5. **Apply**, 29 Sep 07:40–07:47Z, via the Supabase SQL tool:
   - Ran `sql/repair_01.sql` … `repair_10.sql`, 60 rows each (21 in the last).
     Each block is all or nothing. It refuses unless its values hash to the
     audit's and its rows still hold the audit's "before" md5. Blocks 2–10
     ran with a shorter header comment and a more compact `SET` list;
     statements, values and guards were identical.
   - Ran `sql/repair_last_day_over_day.sql`: 561 repaired rows, 812 affected
     (each plus the cached day after it), and 641 day-over-day terms
     rewritten from `lag()`.
   - A check of the whole table against `lag()` then found 14 more rows with
     NULL day-over-day terms although an earlier cached day exists. Each was
     computed just after a prune had taken the day before it out of the view.
     `sql/repair_last_2_prev_left_null.sql` fixed exactly those 14 (audit
     kind `day_over_day_left_null`).
6. **After** (`cache_after.txt`, md5-checked):
   - all 561 repaired rows equal the recompute, and no other row's values changed;
   - the whole table, 22,674 rows, has 0 rows whose `prev_max_c`,
     `delta_max_c` or pressure change differs from `lag()`.

## What changed
| Change | Result |
|---|---|
| `n_obs` | raised on all 561 repaired rows: 6,646 readings back in the features |
| Daily maximum | raised on 184, by 3.52 °C on average and 12.0 °C at most; never lowered |
| NULL `prev_max_c` among the 1,063 candidates | 575 before, 0 after |

For example, nyc on 29 Jul was cached from 2 readings at 23.33 °C. It now has
24 readings and 27.22 °C.

`tests/test_repair_day_features.py` re-derives the plan and the blocks from
these files. `tests/database/day-feature-repair.cjs` replays the blocks on
the "before" rows.
