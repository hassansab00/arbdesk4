# The station correction learned from its own forecasts, with skill weights (`own_w:v1`): pre-registration

**Written 15 Nov 2026, before any forward call of this candidate existed.**
Hassan, 15 Nov, on the model audit's proposal to build this in shadow: "Yes do
whats recommended and necessary".

The question: does the station-corrected combination (P3.9) price the venue's
ladders better when each model's bias is learned from the forecasts the engine
is actually served, and each model is weighted by its skill at the city?

It is scored in shadow. Nothing here prices, trades or writes to the database.
The learner and the evaluator are `tools/own_w_forward.py`;
`tests/test_own_w_forward.py` holds both to this file. Every number below is
from `data/eval/own_w/design_2026-11-15.json`, which
`python tools/own_w_forward.py --design data/eval/own_w` rebuilds from the
mirror.

## Where the candidate comes from (development data, not evidence)

`docs/MODEL_AUDIT_2026-11-15.md` section 6 and
`tools/station_weights_study.py`. P3.9 learns each model's station bias on the
Previous Runs record (runs 24 h or more old), applies it to each model's
latest run, and averages the corrected models equally. The seamless models
change their underlying model with lead, so the two are not the same forecast.
The study measured a recipe that learns from the served forecasts instead.
The dates below chose it, so they flatter it.

`own_w:v1` is that recipe under Rule 11. It differs from the study's in three
ways: a cell moves at most one step a night, a model's skill is bounded, and a
cell's skill stays at the pool's until it has 5 city-days. On the study's
dates that cost almost nothing (the last column).

**The served first calls, replayed, 14 Oct - 14 Nov, 32 dates:**

| Call | Calls | Base | `own_w:v1` | Gain | 90% | The study's recipe | Market |
|---|---|---|---|---|---|---|---|
| Evening before, log loss | 1,476 | 1.5135 | 1.4979 | +0.0156 | +0.0112 to +0.0196 | +0.0160 | 1.2103 |
| Evening before, top pick | | 39.6% | 39.4% | | | 39.4% | 50.2% |
| Morning, log loss | 1,482 | 1.4399 | 1.4044 | +0.0355 | +0.0277 to +0.0434 | +0.0371 | 1.1023 |
| Morning, top pick | | 40.9% | 43.2% | | | 43.5% | 52.6% |

- **The base** is the served ladder at the evening call, and the combination
  as it is at the morning call.
- **It moves the centre by 0.166 C** on average at the evening call's runs and
  0.165 C at the morning's.
- **It gained in 29 of 47 cities** at the evening call and 33 of 47 at the
  morning call.
- **It does not reach the market** at either call.
- The replay reproduces the served evening ladders to within 0.0164 log loss
  on every call.

**Against the station maximum, on the forward rows** (run nights 12 Oct - 14
Nov, mean absolute error):

| Lead | Rows | Run nights | The combination | `own_w:v1` | Gain | 90% |
|---|---|---|---|---|---|---|
| 0 | 1,548 | 34 | 0.7518 C | 0.7114 C | 0.0405 | 0.0325 to 0.0490 |
| 1 | 2,196 | 33 | 0.8921 C | 0.8500 C | 0.0421 | 0.0347 to 0.0492 |
| 2 | 1,439 | 31 | 0.9940 C | 0.9630 C | 0.0309 | 0.0204 to 0.0412 |

**How often Rule 11 held a cell** (32,256 cell-nights): a skill sat at a bound
on 3.4%, a skill was held by its step on 2.6%, a bias by its step on 0.04%.

## Hypothesis

On dates after it was fixed, `own_w:v1` prices the venue's ladders better
than the combination as it is, at both the evening-before call and the
morning call.

## The candidate, `own_w:v1` (nothing else changes)

For each night (a UTC date) and each lead 0, 1 and 2:

- **Errors.** Truth minus forecast for every forward row of that lead
  (`weather_forecast_models`, source `open-meteo-models-current`, the newest
  run of each run date) whose target day is **2 to 45 days** before the night.
  The truth is the station's whole-day maximum (20 reports or more, no gap
  over 3 h), as in the study.
- **Target.** The study's fit (`station_weights_study.shrunk`, which is
  `station_correction.fit`'s shrinkage, K 10): a bias per city and model
  shrunk to the model's pool, and its mean squared error shrunk the same way.
  A model's skill at a city is its precision (1 over that error) divided by
  the mean precision of the seven models there.
- **Centre.** The sum of skill x (model maximum + bias) over the sum of
  skill, from 4 models or more.

**Rule 11.**

| | |
|---|---|
| Prior | The model's pool for the bias; the pool's skill for the weight |
| Minimum sample | 5 city-days before a cell's bias or skill leaves its pool |
| Bounds | Bias within 3.0 C either way; skill between 0.5 and 2.0, so no model counts more than four times another |
| Maximum step a night | 0.25 C for a bias, 0.10 for a skill, from the cell the night before. A cell with no night before it takes its target, as `station_correction.fit` does |
| Version | `own_w:v1`, the night and a hash of every cell, on each look |
| Out of sample | A night reads only target days two or more days before it. The nights run in order from 26 Sep, so every step is one a nightly job would have taken |

A change to any of these is `own_w:v2`, scored only on dates after it is
committed. It is never an edit to this file.

## How a call is scored

Each served first call (the earliest `(decided_at, checkpoint_id)` of a city,
target date and checkpoint) is re-priced from each model's newest run made
**no later than the call**. Both centres use those same runs, so their
difference is the bias and the weights alone.

- **Morning.** The combination as it is (the bias in force that night,
  `derived_station_correction`, equal weights) against `own_w:v1`, each at the
  stored station width (`derived_corrected_forecast.width_c`), with the served
  floor and the city's measurement q.
- **Evening before.** The served ladder against the served centre moved by
  **half** of (`own_w:v1` minus the combination), at the served width. P3.9 is
  half of that centre; the station model is the other half.
- **The score** is the log loss on the venue's winner, floored at 1e-6. The
  gain is the base's log loss minus the candidate's, per call.

## Dates

- **Forward dates:** target dates from **17 Nov 2026**
  (`own_w_forward.FIRST_FORWARD_DATE`). Their evening-before calls are made on
  16 Nov, after this file was written.
- **15 and 16 Nov are neither development nor forward.**
- **A valid date:** a target date with at least **20** scored calls at each of
  the two checkpoints, at least 3 days before the record's last date.
- **Before a look, only counts are read**
  (`python tools/own_w_forward.py --count`).

## Scores (fixed now)

**Primary: the mean gain in log loss at each of the two calls,** with a 90%
interval from resampling whole dates (seed 11, 2,000 resamples).

**Beside it, never used to choose a recipe:**
- the top-pick rate of each, and the market's log loss on the same calls;
- the mean absolute error against the station maximum by lead;
- how many cities gained;
- the study's unbounded recipe on the same calls;
- how often a bound or a step held a cell;
- every call left out, by reason.

## When it is looked at

- **First look:** the first **20** valid dates. If every date from 17 Nov is
  valid, that is about 9 Dec.
- **If it reads "insufficient evidence"**, one more look at **40** valid
  dates, and then the test ends. Nothing is looked at in between.

## Acceptance rule

- **Accepted as a proposal:** the gain's 90% interval is wholly above 0 at
  **both** calls.
- **Rejected:** the interval is wholly below 0 at either call.
- **Insufficient evidence:** anything else.

**What a verdict does.** Nothing changes by itself.
- An acceptance is a proposal to Hassan to build `own_w` into the nightly
  station correction. That build stores a bias and a skill per cell with their
  steps (a schema change), reads the forward rows' history through the
  archive, and is checked against the live database before it prices anything.
- A rejection is recorded here and in `docs/PLAN_PROGRESS.md`, and the
  candidate is closed.

## Known limits, stated before the data

- **It is a replay of the record, not a live capture.** The forecasts are read
  from the mirror with their run times. On the study's dates the replay
  reproduces the served evening ladders to within 0.0164 log loss on every
  call. No new schedule and no database write is needed (Rule 7); I could not
  reach the database from the session that wrote this.
- **A night's state is defined by what it reads, not by when a job would
  run.** It reads target days that ended everywhere before the night's 00:00Z,
  and their station maxima as the record holds them now. A nightly job would
  read them as they stood that night; a day whose reports arrived late would
  differ.
- **The evening call's half is the blend as it is today.** If the station
  blend's weights change, the evening comparison no longer mirrors what would
  be served.
- **The width is not refitted.** Each centre is priced at the width fitted to
  the combination's errors. A centre that is nearer the station would earn a
  narrower width; that is not tested here.
- **The learner's truth is the station's whole-day maximum** from the
  repository's record. The nightly P3.9 fit learns on the venue's truth. The
  score is on the venue's winner either way.
- **Once `settings.station_same_day_pricing` is on,** the morning base is what
  the engine serves on the forecast path. Until then the served morning ladder
  is the older path, and it is reported beside both.
- **Even if it holds, the gap to the market stays.** On the study's dates the
  market's log loss was 1.1023 at the morning call against 1.4044.

## Result

(appended below after each look; nothing above changes)
