# P1.1: what the intraday update does to the engine (4 Oct 2026)

**Question.** The external plan of 4 Oct (P1.1) asks why the engine's same-day calls trail its own
day-ahead call and the market. This answers it component by component: on identical city-days,
at the same checkpoints, from recorded inputs only, with fec-v1's date-clustered intervals.

**Status: research only.** Nothing served has changed. The candidate at the end needs a
pre-registered forward test before it can price anything.

**Reproduce:**

```bash
python3 tools/p11_intraday_ablation.py score data/eval/p11/rows_*.json.gz --out data/eval/p11/report.json
```

The rows are `tools/p11_intraday_ablation.py sql --date D` for each date, run through the
Supabase SQL tool on 4 Oct.

## The rows
**Population.** Every same-day checkpoint of 27 Sep – 3 Oct (7 dates; the day S10 began
recording to the last settled date) whose ladder has a settled winner, taking the engine's first
capture (`v_checkpoint_calls.first_call`). Each is matched on the city-day to:
- the engine's day-ahead call (`v_city_hit_history`: the last pricing before the local day began);
- S10 rd1's ladder at the same checkpoint.

**Size.** 1,649 checkpoint rows; 288 – 298 per checkpoint with all three present.

**The replay is the engine.** Each served ladder was recomputed with
`probability_engine.compute_band_probabilities` from the row's centre, width and floor:
- q (the measurement layer) is the value the engine read at that hour, from the nightly
  `data/mirror/cities` snapshot, switching at the 05:07Z refit;
- all 1,649 served ladders are reproduced: the largest difference is 0.000046 in a bucket, and none
  is off by more than 0.001;
- the remaining difference is the 4-decimal rounding of the stored centre and width.

## What the engine does on the target day (code and data, 27 Sep – 3 Oct)
- **The centre.**
  - The day before, the engine prices the station-corrected forecast: P3.9 with the MOS blend,
    on 286 of 335 evening-before checkpoints.
  - On the day itself (lead 0), P3.9 does not apply: `_station_corrected_for` needs lead 1 or
    more (`scripts/probability_engine.py`).
  - So every same-day checkpoint priced `open_meteo_forecast` minus a per-city bias: 1,795 of
    1,795 lead-0 ladders in `band_probabilities`, and every same-day checkpoint row.
- **The width.** At lead 0 it is the lead-0 skill width: mean 1.158 °C across the lead-0 ladders,
  against 1.532 at lead 1.
- **The trajectory (P3.3)** never applied in this window: every same-day checkpoint row has model
  path `forecast`. Its fit gate (P3.4) is not met, so it cannot be ablated here.
- **The floor** is applied at every same-day checkpoint. In this window it is the floor from
  before #297, which still took NWS readings.

## Log loss on the winner (lower is better)

| Checkpoint | City-days (dates) | Served | Day-ahead carried | Day-ahead + floor | Same-day centre, day-ahead width | Day-ahead centre, same-day width | S10 rd1 | Market (city-days) |
|---|---|---|---|---|---|---|---|---|
| Morning (09:00) | 298 (7) | 1.847 | 1.599 | **1.550** | 1.663 | 1.652 | 1.608 | 1.150 (247) |
| Noon | 296 (7) | 1.478 | 1.591 | **1.339** | 1.376 | 1.454 | 1.163 | 0.877 (249) |
| 2 h before peak | 297 (7) | 1.569 | 1.596 | **1.432** | 1.466 | 1.568 | 1.249 | 0.929 (246) |
| 1 h before peak | 292 (7) | 1.364 | 1.606 | **1.254** | 1.271 | 1.399 | 1.102 | 0.784 (245) |
| 1 h after peak | 288 (7) | 1.051 | 1.599 | **1.018** | 1.013 | 1.097 | 0.559 | 0.255 (240) |

## Top pick right

| Checkpoint | Served | Day-ahead carried | Day-ahead + floor | Same-day centre, day-ahead width | Day-ahead centre, same-day width | S10 rd1 | Market |
|---|---|---|---|---|---|---|---|
| Morning (09:00) | 0.332 | 0.403 | 0.372 | 0.319 | 0.399 | 0.346 | 0.502 |
| Noon | 0.429 | 0.405 | 0.415 | 0.405 | 0.422 | 0.524 | 0.626 |
| 2 h before peak | 0.384 | 0.404 | 0.384 | 0.367 | 0.391 | 0.495 | 0.589 |
| 1 h before peak | 0.469 | 0.394 | 0.476 | 0.452 | 0.456 | 0.531 | 0.649 |
| 1 h after peak | 0.736 | 0.399 | 0.760 | 0.767 | 0.733 | 0.799 | 0.925 |

## Which component does what

| Change in log loss (positive = worse; 90% date-clustered interval; * excludes 0) | Morning (09:00) | Noon | 2 h before peak | 1 h before peak | 1 h after peak |
|---|---|---|---|---|---|
| Adding the floor to the day-ahead ladder | -0.049 [-0.064, -0.034] * | -0.252 [-0.301, -0.195] * | -0.164 [-0.193, -0.136] * | -0.352 [-0.391, -0.303] * | -0.582 [-0.645, -0.529] * |
| Switching to the same-day centre and width, floor held | +0.297 [+0.168, +0.451] * | +0.139 [+0.050, +0.225] * | +0.136 [+0.056, +0.222] * | +0.110 [+0.040, +0.184] * | +0.033 [-0.016, +0.095] |
| the centre alone, at the day-ahead width | +0.114 [+0.049, +0.165] * | +0.037 [-0.026, +0.095] | +0.034 [-0.018, +0.074] | +0.018 [-0.029, +0.065] | -0.005 [-0.064, +0.058] |
| the width alone, at the same-day centre | +0.183 [+0.081, +0.316] * | +0.102 [+0.051, +0.167] * | +0.103 [+0.039, +0.189] * | +0.093 [+0.041, +0.168] * | +0.038 [+0.014, +0.069] * |
| the width alone, at the day-ahead centre | +0.102 [+0.011, +0.253] * | +0.115 [+0.014, +0.274] * | +0.136 [+0.044, +0.291] * | +0.145 [+0.055, +0.299] * | +0.079 [+0.042, +0.125] * |
| the centre alone, at the same-day width | +0.194 [+0.049, +0.317] * | +0.025 [-0.142, +0.157] | +0.000 [-0.139, +0.109] | -0.035 [-0.162, +0.069] | -0.045 [-0.126, +0.033] |
| The whole update (served minus day-ahead carried) | +0.248 [+0.126, +0.393] * | -0.113 [-0.230, +0.019] | -0.028 [-0.105, +0.065] | -0.242 [-0.319, -0.148] * | -0.548 [-0.606, -0.505] * |
| Served minus S10 rd1 | +0.239 [+0.139, +0.365] * | +0.315 [+0.188, +0.469] * | +0.320 [+0.180, +0.471] * | +0.262 [+0.137, +0.406] * | +0.493 [+0.478, +0.507] * |

## Findings
1. **The floor helps at every checkpoint.** Adding it to the day-ahead ladder lowers log loss
   from 0.049 at the morning checkpoint to 0.582 an hour after the peak, and every interval
   excludes 0.
2. **Switching at local midnight to the same-day centre and width hurts every checkpoint before
   the peak**, by 0.110 to 0.297 with every interval excluding 0. An hour after the peak the
   difference, +0.033, is inside its interval.
3. **The width is the larger and more consistent part.** The same-day width is about 26 % narrower
   while the same-day centre is no better. It costs at every checkpoint, whichever centre it is
   paired with, and every interval excludes 0. The centre switch costs significantly only at the
   morning checkpoint (+0.114 at the day-ahead width; +0.194 at the same-day width).
4. **Among the engine's own pieces, the day-ahead centre and width with the checkpoint floor is
   best at every checkpoint.**
   - Morning: 1.550 against 1.847 served.
   - Noon: 1.339 against 1.478.
   - An hour after the peak: 1.018 against 1.051.

   S10 rd1 is better still from noon on (1.163 at noon, 0.559 after the peak), but not at the
   morning checkpoint (1.608).
5. **The market is ahead of every variant at every checkpoint.** Nothing here is an edge.
6. **Near-zero calls in the US (the "39 unexplained" of earlier).**

| Checkpoint | Floor above the winning bucket | Centre 2.5 widths or more from the settled maximum |
|---|---|---|
| Morning (09:00) | 0 | 12 |
| Noon | 1 | 4 |
| 2 h before peak | 1 | 6 |
| 1 h before peak | 2 | 6 |
| 1 h after peak | 13 | 0 |

   - Every near-zero call is one of two kinds:
     - a floor above the winning bucket (17, which #297 addresses: a floor from the routine
       reports cannot sit above the settled maximum);
     - a centre 2.5 widths or more from the settled maximum (28).
   - The second kind is what an overconfident width on a centre that is no better produces.
7. **Top pick at the morning checkpoint** is best with the day-ahead ladder carried unchanged
   (0.403). Adding the floor lowers log loss but takes the top pick to 0.372: the floor's atom
   can make the floor's bucket the favourite on a morning far below the peak. Not investigated
   further here.

## What this suggests (not done)
- **A candidate with no fitted parameters.** On the target day, keep pricing from the day-ahead
  centre and width, and condition them on the checkpoint's floor ("day-ahead + floor"), instead
  of switching at local midnight. On these 7 dates it beats served by:

  | Checkpoint | Gain in log loss |
  |---|---|
  | Morning | 0.297 |
  | Noon | 0.139 |
  | 2 h before peak | 0.136 |
  | 1 h before peak | 0.110 |
  | 1 h after peak | 0.033 (inside its interval) |

  **It was chosen after looking at these dates.** fec-v1 requires it to be pre-registered and
  then scored only on dates after the registration, beside the served ladder, in shadow (rule 6:
  shadow is free).
- **The lead-0 width is too narrow for the centre it serves.** The squared standardised error of
  the served centre is 2.20 at the morning checkpoint and 1.49 – 1.89 later (about 1 if
  calibrated). The station-width challenger (P3.9 part 3) covers lead 1 only.
- **S10 is the stronger same-day forecaster from noon on.** Its path to serving is P7.6 / P7.7,
  which are Hassan's gates.

## Limits
- 7 dates, and in sample. The intervals resample dates, but the candidate was named after seeing
  them.
- The floor in this window took NWS readings (fixed in #297 on 4 Oct). Finding 6's first column
  counts that bug.
- The trajectory never applied, so its contribution cannot be measured from these rows.
- The market comparison covers only the rows whose book was whole (240 – 249 per checkpoint).
