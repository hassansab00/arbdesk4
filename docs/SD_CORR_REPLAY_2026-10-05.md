# The engine's station-corrected path on the day itself (`sd_corr`): design replay (5 Oct 2026)

**Question.** P1.1 (`docs/P11_INTRADAY_DIAGNOSIS_2026-10-04.md`) showed that at local midnight
the engine drops the station-corrected forecast it priced the day before. It moves to the public
forecast with a narrower width, and that costs every checkpoint before the peak. `da_floor:v1`
(`docs/P11_DA_FLOOR_PREREG.md`) keeps the evening-before call. This asks the other question:
**what if the engine kept its station-corrected path on the day itself, with what that path has
learned?**

**Status: research on design data.** Nothing served has changed. These are the dates P1.1 looked
at, so this chooses a candidate; it does not prove it. The proof is a forward test
(`docs/SD_CORR_PREREG.md`).

**Reproduce:**

```bash
python3 tools/sd_corrected_replay.py score data/eval/p11/rows_*.json.gz \
    --corrected data/eval/sd_corr/corrected_rows.json.gz --out data/eval/sd_corr/report.json
```

## The candidate, `sd_corr`
At each same-day checkpoint:
- **The centre** is the station-corrected combination live at the decision
  (`derived_corrected_forecast.combined_c`, P3.9). It comes from each of the 7 models' newest run,
  each corrected for its error at the settlement station. At lead 0 it takes lead 1's
  corrections, as `station_correction.correction` already does.
- **The width** is the station width stored beside that centre (`width_c`, P3.9 part 3). It is
  fitted nightly under Rule 11 on this combination's own errors, per city, shrunk to the pool.
  Lead 0 takes lead 1's width.
- **The floor and q** are the ones the served ladder used.
- **The ladder** comes from the engine's own integrator.

Nothing new is fitted. The MOS blend is not part of it: `derived_mos_forecast` has no lead-0
rows.

**What was live.** `data/mirror/derived_corrected_forecast/<D>.csv.gz` is the table at about
02:40Z on D. The nightly fit rewrites every open day's row at about 05:00Z, so each night's
rows survive in the next snapshot. A checkpoint reads the row with the latest `computed_at` at
or before its decision, if that row is younger than the engine's `max_age_hours` (36). That is
exactly what the tick would have read. The rows these dates need are frozen in
`data/eval/sd_corr/corrected_rows.json.gz` (1,131 computations for 331 city-days).

## The rows
P1.1's rows: every same-day checkpoint of 27 Sep – 3 Oct whose ladder has a settled winner, the
engine's first capture, with its day-ahead call. The replay reproduces every served ladder: the
largest difference is 0.000046, none above 0.001.

| Checkpoint | Rows | Scored (dates, cities) | Left out: no live row before the decision | Left out: live row without a width | Left out: no day-ahead call | Live row at lead 0 / lead 1 |
|---|---|---|---|---|---|---|
| Morning (09:00) | 331 | 264 (6, 48) | 18 | 48 | 1 | 119 / 145 |
| Noon | 329 | 264 (6, 48) | 16 | 48 | 1 | 131 / 133 |
| 2 h before peak | 331 | 267 (6, 48) | 14 | 49 | 1 | 139 / 128 |
| 1 h before peak | 328 | 269 (6, 48) | 10 | 48 | 1 | 157 / 112 |
| 1 h after peak | 330 | 279 (6, 48) | 2 | 48 | 1 | 216 / 63 |

- **27 Sep is left out.** The station width was first stored at 05:02Z on 28 Sep, and the rows
  before it have no width. Its early checkpoints also had no live row.
- **Lead 1 at a same-day checkpoint** means the newest night's runs were fetched before the
  city's day began (the Americas, and Asia before 05:00Z). That is still the newest corrected
  forecast the engine holds.

## Log loss on the winner (lower is better; identical rows)
Differences are paired and have 90% date-clustered intervals; positive means `sd_corr` is
better.

| Checkpoint | Served | `da_floor` | `sd_corr` | Served minus `sd_corr` | `da_floor` minus `sd_corr` |
|---|---|---|---|---|---|
| Morning (09:00) | 1.804 | 1.526 | **1.323** | +0.481 [+0.352, +0.615] | +0.203 [+0.158, +0.244] |
| Noon | 1.451 | 1.312 | **1.162** | +0.289 [+0.193, +0.378] | +0.150 [+0.121, +0.177] |
| 2 h before peak | 1.522 | 1.404 | **1.257** | +0.265 [+0.203, +0.337] | +0.147 [+0.103, +0.199] |
| 1 h before peak | 1.318 | 1.214 | **1.123** | +0.195 [+0.118, +0.273] | +0.090 [+0.048, +0.131] |
| 1 h after peak | 1.030 | 1.000 | **0.986** | +0.044 [+0.007, +0.084] | +0.014 [-0.027, +0.062] |
| Pooled (1343 rows) | 1.420 | 1.288 | **1.168** | +0.252 [+0.179, +0.324] | +0.119 [+0.083, +0.156] |

## Which part does it

| Change in log loss (negative = better) | Morning (09:00) | Noon | 2 h before peak | 1 h before peak | 1 h after peak |
|---|---|---|---|---|---|
| The width alone: station width instead of the day-ahead width, at the day-ahead centre | -0.165 [-0.194, -0.132] | -0.092 [-0.120, -0.063] | -0.115 [-0.142, -0.087] | -0.064 [-0.089, -0.042] | +0.010 [-0.025, +0.047] |
| The centre alone: the live corrected centre instead of the day-ahead one, at the day-ahead width | -0.012 [-0.042, +0.019] | -0.022 [-0.045, -0.002] | -0.005 [-0.031, +0.019] | -0.000 [-0.024, +0.024] | +0.001 [-0.035, +0.036] |
| The width at the live centre: station width instead of the day-ahead width | -0.191 [-0.211, -0.174] | -0.128 [-0.142, -0.115] | -0.141 [-0.166, -0.118] | -0.090 [-0.108, -0.072] | -0.016 [-0.040, +0.009] |

**Average miss of each centre (°C) against the observed maximum, on the same rows:**

| Checkpoint | Served centre | Day-ahead centre | Live corrected centre |
|---|---|---|---|
| Morning (09:00) | 0.989 | 0.769 | 0.741 |
| Noon | 0.915 | 0.765 | 0.718 |
| 2 h before peak | 0.908 | 0.771 | 0.733 |
| 1 h before peak | 0.900 | 0.786 | 0.748 |
| 1 h after peak | 0.834 | 0.786 | 0.720 |

**The widths, over the 1,343 scored rows:**
- the station width averages 0.987 °C (0.616 – 1.646);
- the day-ahead width averages 1.545;
- the served same-day width averages 1.14.

The station width is the narrowest of the three and scores best, because it is fitted to this
centre's own errors, city by city. Against the observed maximum, the centre is within one width
on 69.8% of the rows and beyond two on 3.6%. A normal distribution gives 68.3% and 4.6%.

## Top pick right (rate and Wilson 95% interval)

| Checkpoint | Served | `da_floor` | `sd_corr` |
|---|---|---|---|
| Morning (09:00) | 0.326 [0.272, 0.384] | 0.383 [0.326, 0.443] | 0.439 [0.381, 0.500] |
| Noon | 0.432 [0.373, 0.492] | 0.436 [0.377, 0.496] | 0.489 [0.429, 0.549] |
| 2 h before peak | 0.382 [0.326, 0.442] | 0.401 [0.344, 0.461] | 0.457 [0.398, 0.517] |
| 1 h before peak | 0.476 [0.417, 0.535] | 0.502 [0.443, 0.561] | 0.520 [0.461, 0.579] |
| 1 h after peak | 0.742 [0.688, 0.790] | 0.778 [0.725, 0.823] | 0.710 [0.654, 0.760] |

## Against the market, on identical rows
Only the rows where the market's book was whole are used. Negative means the market is better.

| Checkpoint (market rows) | Market | Market minus served | Market minus `da_floor` | Market minus `sd_corr` |
|---|---|---|---|---|
| Morning (09:00) (242) | 1.143 | -0.710 [-0.908, -0.559] | -0.395 [-0.449, -0.331] | -0.194 [-0.246, -0.147] |
| Noon (243) | 0.871 | -0.605 [-0.765, -0.450] | -0.446 [-0.552, -0.331] | -0.296 [-0.378, -0.211] |
| 2 h before peak (244) | 0.926 | -0.617 [-0.792, -0.471] | -0.483 [-0.585, -0.352] | -0.343 [-0.447, -0.239] |
| 1 h before peak (245) | 0.779 | -0.566 [-0.718, -0.460] | -0.434 [-0.534, -0.336] | -0.354 [-0.478, -0.268] |
| 1 h after peak (247) | 0.248 | -0.837 [-0.982, -0.740] | -0.779 [-0.933, -0.637] | -0.783 [-0.929, -0.672] |

## Findings
1. **`sd_corr` scores better than the served ladder at every checkpoint.** The gain runs from
   +0.481 in log loss at the morning checkpoint to +0.044 an hour after the peak, and every 90%
   interval is above 0.
2. **It also scores better than `da_floor` before the peak:** +0.090 to +0.203, every interval
   above 0. An hour after the peak the +0.014 is inside its interval. Pooled over the five
   checkpoints: +0.119 [+0.083, +0.156].
3. **The width does most of it.** The station width instead of the day-ahead width gains 0.064 –
   0.191 before the peak, at either centre. The live corrected centre on its own changes little:
   its miss is 0.72 – 0.75 °C against the evening-before call's 0.77 – 0.79, and in log loss
   only noon's -0.022 has an interval excluding 0.
4. **The top pick is better before the peak and worse an hour after it** (0.710 against 0.742
   served and 0.778 for `da_floor`). This was not investigated further.
5. **The market is still ahead at every checkpoint**, by 0.194 at the morning checkpoint to
   0.783 an hour after the peak. Nothing here is an edge.

## Limits
- **Six dates, in sample.** These are P1.1's dates, which also chose `da_floor`. The intervals
  resample dates, but the candidate was named after seeing them.
- **The station width has its own forward test still pending.** It is shadow at lead 1
  (`settings.station_width_pricing` off). Its nightly score (`P3.9_width_score`) had 5 of the 7
  dates it needs on 4 Oct. This replay uses it at lead 0, which nothing has tested forward.
- **The floor** in this window still took NWS readings for US cities (fixed in #297 on 4 Oct),
  as in P1.1.
- **One computation per night.** The mirror holds one snapshot a night, so a same-night re-run
  of the fit would hide the earlier computation. The snapshots show one `computed_at` a night on
  every date here.
