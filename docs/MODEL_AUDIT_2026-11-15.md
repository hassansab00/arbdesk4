# The prediction model, audited city by city — 15 Nov 2026

Hassan, 15 Nov: "fully audit and analyze the weather forecasting prediction model
city by city, and the learning and calibration mechanism in place, as well as the
accuracy and potential room for improvement. If something needs improvement ...
implement it."

**Where the numbers come from.** Everything here is rebuilt from committed files
by two tools, and their results are committed:

- `tools/model_audit.py` → `data/eval/model_audit_2026-11-15/served.json`: the
  served first calls on settled outcomes, from the nightly mirror (14,085
  calls, 24 Sep - 14 Nov).
- `tools/station_weights_study.py` →
  `data/eval/model_audit_2026-11-15/station_weights_study.json`: the two
  candidates in section 6.

The database was not reachable from the session that wrote this. What is served
was read from the code and from the mirror's copies of the tables. Settings
were read from the mirror's last snapshot (13 Oct) and are not re-verified.

## 1. The short version

- **The model is well calibrated but not sharp enough.** Its stated
  probabilities are honest at every checkpoint except the morning call. It
  loses to the market because its centre is less accurate, not because its
  probabilities are miscalibrated.
- **The morning call was the weakest and it had a defect.** It scored worse
  than the call made 15 hours earlier, because on the day itself the engine
  dropped the station correction it had used the evening before. **Fixed in
  this PR** (section 5). It takes effect when the migration is run.
- **The station correction is learned on older forecasts than it is applied
  to.** Learning it from the served forecasts' own history, with skill
  weights, is measurably better at every lead (section 6). **Measured, not
  built**: it changes the nightly learner and needs a schema change.
- **None of the newer learning loops has applied anything yet.** Every one is
  held for lack of dates (section 4).
- **The market is still ahead at every checkpoint.** After the fix the morning
  call's replayed top pick is right 41% of the time; the market's is 53%.

## 2. What is served

| Checkpoint (city's clock) | What prices it | Per-city learning in it |
|---|---|---|
| Day before, 09:00 and 18:00 | Seven models, each corrected for its station bias (P3.9), averaged equally, then averaged with the station model (MOS). Width: the station width, or the city's blend width where that passed its gate. | Bias per model, lead and city; MOS coefficients; width |
| Morning, 09:00 | **Before this PR:** the public forecast less a per-city bias with no shrinkage. **After:** the P3.9 combination and its width. | As above |
| Noon, 2 h and 1 h before the peak, 1 h after | `wxrd3` (five LightGBM models, fitted through 11 Oct, frozen). The forecast path is the fallback. | The city as a feature; past-only bias features |

Same-day calls put the probability below the station's running maximum on the
bucket it has reached, with the city's measured rounding shares.

## 3. Accuracy

First calls, top pick right and log loss, the market beside it on the rows
whose book was whole (`served.json`, `by_checkpoint`):

| Checkpoint | 24 Sep - 16 Oct: hit / market | log loss / market | 17 Oct - 14 Nov: hit / market | log loss / market |
|---|---|---|---|---|
| Evening before | 38.0% / 46.0% | 1.611 / 1.225 | 39.4% / 50.0% | 1.518 / 1.217 |
| Morning | 36.9% / 50.5% | 1.772 / 1.118 | 34.6% / 51.9% | 1.681 / 1.119 |
| Noon | 41.6% / 59.6% | 1.505 / 0.899 | 54.7% / 65.4% | 1.060 / 0.831 |
| 2 h before peak | 38.3% / 58.1% | 1.567 / 0.965 | 50.7% / 60.0% | 1.137 / 0.927 |
| 1 h before peak | 46.6% / 63.7% | 1.391 / 0.813 | 58.2% / 69.6% | 0.965 / 0.758 |
| 1 h after peak | 74.1% / 87.8% | 0.909 / 0.309 | 85.7% / 91.1% | 0.388 / 0.249 |

- From 17 Oct `wxrd3` serves noon and later: those four checkpoints improved
  by 11 to 13 points of hit rate.
- The evening and morning calls did not move, and the morning call is worse
  than the evening one.

**Calibration** (the top pick's stated probability against how often it won,
17 Oct - 14 Nov; `reliability_top_pick_since`):

| Stated | Evening before | Morning | Noon to 1 h before peak | 1 h after peak |
|---|---|---|---|---|
| 0.3-0.4 | 37.6% won (787 calls) | 30.0% (404) | 37.4% (382) | 33.3% (18) |
| 0.4-0.5 | 40.9% (377) | 34.9% (261) | 44.9% (1,250) | 40.6% (69) |
| 0.5-0.6 | 64.0% (50) | 35.7% (129) | 53.2% (1,131) | 56.0% (109) |
| 0.6-0.7 | - | 43.1% (72) | 63.2% (500) | 76.3% (152) |
| 0.7-0.8 | - | 39.3% (28) | 69.4% (252) | 85.9% (135) |
| 0.9-1.0 | - | 69.2% (26) | 94.5% (236) | 98.4% (641) |

The morning call is badly overconfident: a pick it called 54% won 36% of the
time. The others track.

## 4. Learning and calibration: what reaches the price

**Applied in serving:**

| Learner | What it learns | Prior, bounds, step | Reach |
|---|---|---|---|
| Station correction (P3.9) | Bias per model, lead and city | The model's pool, K 10; ±3 C; 0.25 C a night; 45 days | Day-ahead calls |
| Station model (MOS) | Ridge regression of the station's residual | Pool, then zero; ±4 C; 0.25 C | Day-ahead calls, as half the centre |
| Station width | Width per city from the centre's own errors | Pool, K 10; 0.5-3 C; 10% a night | Day-ahead calls at lead 1 |
| City blend width | Width of the blend, per city | Later-date gate | Where it passed |
| Forecast post-processing | Bias and width ratio per city and lead | City, then zero; forward gate | 28 of 399 cells |
| Sigma multiplier | Width multiplier per city | 0.75-2.5; no step limit, no version | 7 cities |
| Measurement layer | Rounding shares at the floor | Pool, k 20; 0-0.5; 0.10 a night | Same-day calls |
| Skill bias and MAE | Per city and lead | None | Morning (before this PR), fallback widths |

**Fitted but not applied — every cell held:**

| Learner | Held because |
|---|---|
| Global temperature T | Failed its held-out check on 29 Sep; the fitter has been stopped since 5 Oct |
| City × phase calibration of `wxrd3` | 8 of 20 training dates |
| City × horizon model choice | 8 of 20 training dates (noon and later); not authorised earlier |
| Early-city strength | 0-2 of 20 training dates |
| Seven-source ensemble | 2-3 learning dates; needs 20, then a 14-date trial |
| Market weights | Best weight 0 in every scope over 51 days |
| `wxrd3` refit challenger | Still inside its fixed forward window |
| Current-forecast challenger | 5 of 20 training dates |

The calibration learners key their history on the model's version string. The
string gained the contract and station, so their counters restarted on 24 Oct.

## 5. Fixed in this PR: the same-day forecast path

**The defect.** The evening before a day the engine prices from the
station-corrected combination. At lead 0 it went back to the public forecast
less a per-city bias. The morning centre's bias reaches 1 C or more in several
cities (section 7), where the evening centre's does not.

**The evidence.** `sd_corr:v1`'s forward rows priced every same-day checkpoint
from the combination live at that moment and its stored width, beside the
served call (`served.json`, `same_day_path`). Log loss, served minus
corrected, 90% by date, split by what served the call:

| Served by | Checkpoint | Calls | Dates | Gain | 90% | Top pick: served → corrected |
|---|---|---|---|---|---|---|
| Forecast path | Morning | 1,866 | 41 | +0.2413 | +0.2044 to +0.2814 | 35.7% → 40.1% |
| Forecast path | Noon | 608 | 33 | +0.1566 | +0.0906 to +0.2202 | 42.9% → 42.8% |
| Forecast path | 2 h before peak | 611 | 30 | +0.1484 | +0.0816 to +0.2104 | 39.0% → 39.3% |
| Forecast path | 1 h before peak | 608 | 27 | +0.1232 | +0.0723 to +0.1694 | 47.9% → 45.7% |
| Forecast path | 1 h after peak | 612 | 25 | +0.0809 | +0.0363 to +0.1276 | 75.3% → 73.7% |
| `wxrd3` | Noon | 1,258 | 29 | -0.2022 | -0.2348 to -0.1705 | 55.2% → 48.2% |
| `wxrd3` | 1 h after peak | 1,248 | 29 | -0.3442 | -0.3849 to -0.3003 | 86.5% → 77.4% |

- The corrected ladder beats the forecast path at every checkpoint and loses
  only to `wxrd3`.
- `sd_corr` as pre-registered was rejected today because it was tested at all
  five checkpoints against whatever served, and `wxrd3` serves four of them.
  That verdict stands. This change is narrower: what the forecast path prices
  on the day.
- **This reading was not pre-registered.** It was made after the first look.
  The morning gain on the first look's dates (5-25 Oct) is +0.2503 (+0.1851
  to +0.3159); on the 20 dates after it, +0.2320 (+0.1864 to +0.2758).

**The change.** `scripts/probability_engine.py`: while
`settings.station_same_day_pricing` is on, a same-day price on the forecast
path takes the combination's own centre and the width stored beside it. It
never takes the station-model blend, which that test did not include. The
advance-width guard does not widen it. A row without a width prices nothing.
`wxrd3` still replaces the ladder at noon and later.

**To take effect:** run
`supabase/migrations/20261115120000_same_day_station_pricing.sql`. Until then
the engine behaves as before. Setting `enabled` to false turns it off.

## 6. Measured, not built: learn the correction from the forecasts it is applied to

P3.9 learns each model's station bias on the Previous Runs record (runs 24 h
or more old) and applies it to each model's latest run. The seamless models
change their underlying model with lead, so the two are not the same forecast.

**On the rows the engine was served** (`forward.by_lead`; 12 Oct - 14 Nov, each
run night fitted on earlier target days only; mean absolute error against the
station maximum):

| Lead | Rows | Run nights | Bias in force | Own bias | Own bias + skill weights | Gain | 90% |
|---|---|---|---|---|---|---|---|
| 0 | 1,548 | 34 | 0.752 C | 0.739 | 0.709 | 0.043 | 0.035 to 0.052 |
| 1 | 2,196 | 33 | 0.892 C | 0.870 | 0.850 | 0.043 | 0.035 to 0.050 |
| 2 | 1,439 | 31 | 0.994 C | 0.980 | 0.963 | 0.032 | 0.021 to 0.042 |

**Replayed on the served first calls** (`forward.served_calls_replayed`; 14 Oct
- 14 Nov, 32 dates). Each call is re-priced from the runs made no later than
it, and both centres use those same runs, so the difference is the bias and
the weights alone. The replay reproduces the served evening ladders to within
0.016 log loss on every call.

| Call | Calls | Served | Combination as it is | Own bias + weights | Market |
|---|---|---|---|---|---|
| Morning log loss | 1,482 | 1.682 | 1.440 | 1.403 | 1.102 |
| Morning top pick | | 35.4% | 40.9% | 43.5% | 52.6% |
| Evening log loss | 1,476 | 1.514 | - | 1.498 | 1.210 |
| Evening top pick | | 39.6% | - | 39.4% | 50.2% |

- The morning column "combination as it is" is what this PR turns on
  (+0.2417, 90% +0.2014 to +0.2802).
- Own bias and weights add +0.037 to that at the morning call. At the
  evening call they gain +0.0160 log loss (+0.0115 to +0.0202) with no gain
  in hit rate, because P3.9 is half of that centre.
- Codex on #509: the first version of this replay took the newest run of the
  call's date, which for 739 evening calls was made after the call. That
  doubled the evening gain (+0.0317). It is corrected here.

**On the long record** (`record`; lead 1, 19,500 city-days, 1 Oct 2025 - 14 Nov
2026, each day fitted on the 45 days before it):

| Recipe | MAE | Log loss on 9,902 venue ladders | Top pick |
|---|---|---|---|
| P3.9 as coded | 0.906 C | 1.450 | 41.5% |
| Skill weights | 0.878 | 1.426 | 42.4% |
| Yesterday's miss added back | 0.877 | 1.428 | 42.4% |
| Both | 0.855 | 1.410 (+0.040, 90% +0.035 to +0.047) | 43.6% |
| The market at 00:00 | | 1.278 | 47.1% |

**Why it is not built here.** It changes the nightly learner that the
day-ahead price depends on. It needs a weight per cell stored with its step
limit (Rule 11), which is a schema change, and the forward rows' history read
through the archive. I could not run it against the database from this
session. The design is in the tool's docstring; the next step is to build it
in shadow beside the served combination.

## 7. City by city, 17 Oct - 14 Nov

Hit rate and log loss are over 28-29 dates a city. The centre's bias (station
maximum minus centre) and mean absolute error are over the 19-21 dates
`fact_forecast_outcome` has graded. Noon and later have no centre: `wxrd3`
prices the ladder directly.

| city | evening: hit / market | evening log loss / market | evening centre bias, MAE (C) | morning: hit / market | morning log loss / market | morning centre bias, MAE (C) | noon to 1 h before peak: hit / market | 1 h after peak: hit / market |
|---|---|---|---|---|---|---|---|---|
| amsterdam | 61% / 65% | 1.34 / 1.05 | -0.26, 0.51 | 36% / 52% | 1.51 / 1.05 | +0.34, 0.78 | 62% / 67% | 79% / 91% |
| ankara | 34% / 31% | 1.75 / 1.47 | +0.44, 0.82 | 41% / 36% | 1.80 / 1.54 | -0.05, 0.71 | 49% / 51% | 100% / 96% |
| atlanta | 43% / 48% | 1.62 / 1.18 | +0.43, 0.64 | 25% / 55% | 1.52 / 0.84 | +0.38, 0.79 | 55% / 73% | 93% / 95% |
| austin | 29% / 68% | 1.50 / 1.12 | +0.23, 0.95 | 25% / 64% | 2.01 / 1.09 | +0.34, 1.09 | 63% / 69% | 100% / 100% |
| beijing | 24% / 40% | 1.78 / 1.23 | +0.45, 1.09 | 10% / 38% | 2.40 / 1.36 | +1.17, 1.53 | 49% / 60% | 79% / 86% |
| buenos_aires | 18% / 32% | 1.87 / 1.70 | +0.28, 1.13 | 18% / 27% | 1.84 / 1.74 | +0.42, 1.10 | 43% / 52% | 57% / 86% |
| busan | 38% / 48% | 1.76 / 1.29 | +0.02, 1.09 | 45% / 25% | 2.08 / 1.23 | -1.02, 1.21 | 69% / 55% | 93% / 100% |
| cape_town | 41% / 39% | 1.45 / 1.37 | -0.22, 0.93 | 21% / 44% | 2.58 / 1.59 | -0.06, 1.56 | 44% / 55% | 72% / 81% |
| chengdu | 48% / 46% | 1.46 / 1.38 | -0.26, 0.84 | 34% / 44% | 1.75 / 1.26 | -0.79, 1.36 | 55% / 70% | 86% / 96% |
| chicago | 54% / 79% | 1.24 / 0.86 | +0.10, 0.70 | 50% / 67% | 1.23 / 0.81 | +0.38, 0.77 | 75% / 67% | 100% / 95% |
| chongqing | 38% / 52% | 1.34 / 1.19 | +0.20, 0.69 | 31% / 62% | 1.58 / 1.07 | -0.55, 0.76 | 57% / 73% | 79% / 78% |
| dallas | 54% / 35% | 1.58 / 1.40 | +0.42, 0.85 | 32% / 57% | 2.06 / 1.17 | +0.80, 1.03 | 45% / 57% | 96% / 100% |
| denver | 32% / 48% | 1.68 / 1.44 | -0.10, 1.10 | 29% / 41% | 1.50 / 1.32 | +0.05, 1.01 | 40% / 55% | 96% / 100% |
| guangzhou | 38% / 42% | 1.50 / 1.26 | -0.26, 0.92 | 31% / 28% | 1.61 / 1.35 | -0.56, 0.96 | 40% / 53% | 79% / 95% |
| helsinki | 55% / 65% | 0.92 / 0.78 | +0.12, 0.49 | 69% / 68% | 0.73 / 0.61 | +0.33, 0.37 | 83% / 85% | 93% / 92% |
| houston | 18% / 50% | 1.95 / 1.34 | +0.66, 0.89 | 36% / 39% | 2.57 / 1.16 | +0.30, 0.61 | 52% / 55% | 89% / 83% |
| istanbul | 34% / 63% | 2.11 / 1.37 | +0.20, 0.97 | 52% / 54% | 1.58 / 0.81 | +0.12, 0.59 | 54% / 64% | 79% / 68% |
| jeddah | 38% / 36% | 1.55 / 1.33 | +0.37, 0.74 | 24% / 32% | 1.90 / 1.34 | +0.54, 0.83 | 60% / 60% | 93% / 96% |
| karachi | 38% / 38% | 1.26 / 1.14 | +0.42, 0.67 | 59% / 57% | 1.25 / 1.05 | -0.01, 0.63 | 43% / 58% | 83% / 100% |
| kuala_lumpur | 34% / 60% | 1.41 / 1.16 | +0.42, 0.68 | 24% / 58% | 2.28 / 1.18 | +1.28, 1.49 | 47% / 63% | 83% / 96% |
| london | 50% / 64% | 1.25 / 0.94 | -0.18, 0.69 | 54% / 71% | 1.23 / 0.83 | +0.23, 0.72 | 71% / 73% | 86% / 87% |
| los_angeles | 50% / 60% | 1.30 / 0.93 | +0.49, 0.76 | 32% / 52% | 1.79 / 1.04 | +0.36, 0.94 | 52% / 71% | 89% / 100% |
| lucknow | 48% / 52% | 1.27 / 1.01 | +0.43, 0.64 | 34% / 58% | 1.74 / 1.02 | +0.32, 0.87 | 71% / 63% | 97% / 100% |
| madrid | 28% / 25% | 1.60 / 1.32 | +0.12, 0.90 | 55% / 55% | 1.25 / 1.13 | +0.13, 0.54 | 61% / 58% | 93% / 100% |
| manila | 38% / 44% | 1.37 / 1.26 | -0.00, 0.83 | 34% / 46% | 1.61 / 1.20 | -0.52, 0.89 | 43% / 58% | 79% / 81% |
| mexico_city | 32% / 33% | 1.35 / 1.25 | +0.02, 0.91 | 18% / 43% | 1.84 / 1.07 | -1.12, 1.46 | 52% / 53% | 86% / 90% |
| miami | 68% / 69% | 1.07 / 0.91 | +0.15, 0.52 | 32% / 74% | 1.52 / 0.68 | +0.48, 0.93 | 48% / 78% | 86% / 91% |
| milan | 29% / 44% | 1.52 / 1.23 | -0.24, 0.88 | 50% / 50% | 1.42 / 1.24 | +0.16, 0.66 | 45% / 70% | 93% / 100% |
| moscow | 24% / 48% | 1.77 / 1.28 | +1.16, 1.37 | 34% / 57% | 1.64 / 1.14 | +0.97, 1.07 | 54% / 73% | 83% / 88% |
| munich | 32% / 52% | 2.27 / 1.31 | -0.97, 1.18 | 25% / 46% | 2.37 / 1.31 | -0.54, 1.12 | 40% / 53% | 82% / 80% |
| nyc | 50% / 71% | 1.12 / 0.76 | +0.62, 0.65 | 46% / 81% | 0.98 / 0.54 | +0.55, 0.74 | 76% / 86% | 96% / 100% |
| panama_city | 36% / 46% | 1.56 / 1.43 | -0.14, 0.92 | 25% / 45% | 1.64 / 1.24 | +0.18, 0.87 | 45% / 53% | 71% / 91% |
| paris | 55% / 61% | 1.82 / 1.27 | -0.45, 0.69 | 38% / 50% | 1.59 / 1.21 | -0.13, 0.64 | 61% / 69% | 86% / 92% |
| qingdao | 38% / 46% | 1.56 / 1.43 | +0.04, 0.79 | 38% / 48% | 1.36 / 1.11 | -0.06, 0.78 | 57% / 71% | 83% / 100% |
| san_francisco | 50% / 42% | 1.44 / 1.26 | -0.21, 0.83 | 21% / 54% | 1.85 / 1.20 | +0.96, 1.48 | 41% / 64% | 86% / 83% |
| sao_paulo | 36% / 43% | 2.09 / 1.49 | +0.17, 1.30 | 21% / 41% | 1.69 / 1.36 | -0.23, 1.18 | 39% / 58% | 82% / 89% |
| seattle | 25% / 40% | 1.69 / 1.34 | +0.42, 0.94 | 25% / 42% | 2.14 / 1.34 | +0.70, 1.01 | 41% / 47% | 82% / 87% |
| seoul | 52% / 73% | 1.37 / 1.02 | -0.31, 0.80 | 24% / 72% | 1.98 / 0.92 | -1.35, 1.49 | 60% / 75% | 93% / 87% |
| shanghai | 55% / 60% | 1.17 / 1.00 | +0.09, 0.67 | 34% / 65% | 1.35 / 0.79 | +0.57, 0.92 | 69% / 72% | 83% / 92% |
| shenzhen | 52% / 62% | 1.40 / 1.33 | -0.05, 0.65 | 34% / 65% | 1.44 / 1.07 | +0.13, 0.76 | 52% / 73% | 83% / 90% |
| singapore | 31% / 42% | 1.34 / 1.25 | -0.07, 0.76 | 21% / 52% | 1.56 / 1.09 | +0.32, 0.92 | 52% / 69% | 76% / 91% |
| tel_aviv | 45% / 42% | 1.58 / 1.31 | +0.52, 0.88 | 45% / 39% | 1.76 / 1.41 | +0.42, 0.87 | 59% / 63% | 90% / 92% |
| tokyo | 21% / 57% | 1.50 / 1.31 | +0.26, 0.88 | 45% / 63% | 1.56 / 0.86 | +0.21, 1.20 | 60% / 73% | 83% / 85% |
| toronto | 39% / 28% | 1.49 / 1.17 | +0.73, 0.88 | 43% / 58% | 1.30 / 1.12 | +0.28, 0.90 | 69% / 69% | 85% / 88% |
| warsaw | 24% / 29% | 1.48 / 1.17 | +0.15, 0.88 | 17% / 43% | 1.67 / 1.02 | +0.52, 0.89 | 51% / 70% | 83% / 90% |
| wellington | 45% / 60% | 1.35 / 0.90 | +0.34, 0.75 | 44% / 68% | 1.15 / 0.83 | +0.26, 0.59 | 49% / 73% | 79% / 85% |
| wuhan | 31% / 69% | 1.61 / 1.21 | +0.33, 1.04 | 41% / 56% | 1.78 / 1.13 | -0.82, 1.47 | 66% / 81% | 90% / 100% |

**What stands out:**

- **Morning centre biases of 1 C or more:** Seoul -1.35, Kuala Lumpur +1.29,
  Beijing +1.17, Mexico City -1.12, Busan -1.02; Moscow +0.97 and San
  Francisco +0.96 are close. The evening centre for the same cities is within
  0.5 C, except Moscow.
- **Evening centre biases that the correction has not removed:** Moscow +1.16,
  Munich -0.97, Toronto +0.73, Houston +0.66, NYC +0.62. The correction moves
  at most 0.25 C a night over a 45-day window, so it lags a change of season.
- **Hardest cities the evening before** (hit rate): Buenos Aires and Houston
  18%, Tokyo 21%, Beijing, Moscow and Warsaw 24%. Best: Miami 68%, Amsterdam
  61%.
- **Cities where the model matches or beats the market at noon to 1 h before
  the peak:** Busan, Chicago, Lucknow, Madrid, Toronto, Jeddah.

## 8. Checked and not supported

- **Stale inputs at the evening call.** The served evening calls' forecast was
  a median 1.7 h old (`forecast_issued_at`, the newest mirrored night's 47 evening calls). A four-hourly
  refresh already rebuilds the combination. My first reading of 13 h was the
  mirror's nightly snapshot, not what was served.
- **Refitting `wxrd3`.** The refit challenger (fitted through 29 Oct) and the
  frozen model's shadow copy score the same against the served calls on 2-14
  Nov (13 dates). No case for a refit yet.
- **More forecast models.** Not measured: Open-Meteo's archive refused this
  session's address (daily limit) after one request.

## 9. Noticed, not changed

- The sigma multiplier applies for z between 1.10 and 1.15 while its reason
  text says no correction is applied. It has no step limit or version.
- `station_width_score` skips every market the width priced, so the width has
  had no forward score since 22 Oct.
- The belief map is fitted every night and nothing reads it.
- `wx_city_calibration.usable` does not check the decision, so a held state's
  old parameters could serve for up to 3 days.
- Eight cities with a lead-0 post-processing cell skipped the same-day width
  guard (Dallas sigma about 0.58 C). The same-day station price replaces this
  path once the migration is run.
