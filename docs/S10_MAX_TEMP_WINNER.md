# S10 - the day's maximum-temperature winner: the contract (plan v2 P7.1)

**Version `s10-contract-v1`, frozen 26 Sep 2026, before any S10 evaluation.**
Every S10 result (P7.2 stages, the P7.3 replay, P7.6 shadow) states the
contract version it was scored under. Changing a definition below makes a new
version; results under the old one stay reported under it and are never
re-scored silently. `tests/test_s10_contract.py` holds this file to the code it
names.

S10 predicts which bucket of a city's daily-maximum ladder the venue will
settle on, and (later, P7.4-P7.7) trades when that prediction and the book
disagree by more than costs. This file defines only what is eligible, when a
prediction is scored, against what truth, and what "better" means.

## 1. An eligible city-day

A city-day is eligible at a checkpoint when all five hold **as of that
checkpoint's decision time** (nothing learned later may make a city-day
eligible or ineligible):

1. **Active city:** `cities.status = 'active'`.
2. **Station agreement:** `cities.observation_trust >= 0.95` with
   `observation_trust_n >= 10` (plan P2.3), from the value in force at the
   decision time (`observation_trust_at` at or before it). Measured 26 Sep
   22:58Z: 48 of 48 active cities qualify.
3. **Ladder complete:** the city-day's canonical ladder (`v_canonical_markets`
   and its bands) covers the whole number line at the decision time: an open
   lower tail, an open upper tail, and adjacent bands sharing their settle
   edges with no gap.
4. **Market open:** the market is not closed, and the decision time is before
   the end of the city's local day (plan P3.2).
5. **Settlement source unchanged:** `cities.source_changed_at` is null or
   before the start of the local day. A city whose rules text or settlement
   station changed on or after the day is not eligible that day.

An eligible checkpoint whose truth never arrives (section 4) is **unscored**,
listed with its reason, and never guessed.

## 2. Checkpoints are evaluation anchors, not trading times

The six checkpoints are the ones the tick writes (`scripts/tick.py`,
`CHECKPOINTS`, on the **city's** wall clock):

| checkpoint | decision time (local) |
|---|---|
| `d1_eve` | 18:00 the day before |
| `morning` | 09:00 |
| `noon` | 12:00 |
| `prepeak_2h` | the city-month's measured modal peak hour (`derived_weather_peak`) minus 2 h |
| `prepeak_1h` | that peak minus 1 h |
| `postpeak_1h` | that peak plus 1 h |

A city with no measured peak has no peak-relative checkpoints that day; 15:00
would be a guess.

- A city-day counts **once per checkpoint**. Each checkpoint is scored on its
  own and reported on its own; the six are never pooled into one headline
  number.
- Updates between checkpoints are not extra predictions and never extra wins.
- **Trading decisions happen on every tick** (P5.6 decides when to act). The
  checkpoints only fix the moments the prediction is graded.

## 3. What a prediction may know

- Observations with `valid_at` at or before the decision time.
- Forecasts with `issued_at` at or before the decision time (plan P2.6's
  `v_forecast_issued`). For Open-Meteo Previous Runs rows the true publication
  time is not recorded. The assumption used (a run started by 17:00 local the
  day before is out within about 7 hours) is stated with every result that
  relies on it, until P7.2 verifies it.
- A model fitted only on target dates **before** the target date (expanding
  window). Nothing learned is evaluated on the data it was learned from
  (Rule 11).

## 4. Truth

- **The venue winner only:** the winning token in
  `paper_resolution_evidence`, mapped to its band.
- The station maximum is used **only as a training label** where no venue
  answer exists. Such rows carry `label_source = 'station'` and never enter
  an S10 score.
- A checkpoint whose settled ladder does not hold the venue winner stays in
  the denominator as a miss, with its Brier and log-loss penalty (as
  `fact_checkpoint_outcome` already scores it).

## 5. Metrics, per checkpoint

1. **Full-coverage top-1 accuracy:** the most likely bucket equals the venue
   winner, over every eligible scored checkpoint. Wilson 95% interval.
2. **Selective accuracy and coverage** at top-probability thresholds 0.4, 0.5
   and 0.6.
3. **Reliability of `top_prob`:** predicted against realised, in deciles once
   n allows, with the count in each bin.
4. **Brier and log loss** on the whole ladder (log loss floored at
   probability 1e-6).
5. **Breakdowns** per city, per date, and per regime: terciles of the
   cross-model disagreement known at the decision time (the spread of the
   seven models' daily maxima, P2.8).

## 6. Comparators, at the same timestamp and on the same checkpoints

- **The market:** the band with the highest book mid (the checkpoint row's
  `market_top_band_id`). Its Brier and log loss count only where the whole
  ladder had a price (`market_complete`).
- **The engine as it priced** (Stage 0): `prediction_checkpoints`.
- **The public forecast:** the newest best_match daily maximum issued at or
  before the decision time, bucketed with the venue's rounding. Top-1 only.
- **Uniform** over the ladder, as the floor any model must clear.

## 7. What "better" means

A candidate beats a comparator at a checkpoint when all of these hold:

- the comparator's log loss minus the candidate's, averaged per scored day
  and taken in date order, has a **90% interval above 0** by moving-block
  bootstrap (`model_promotion.bootstrap_interval`, `BOOT_INTERVAL = 0.90`:
  the plan P3.4 rule);
- over **at least 20 scored days** at that checkpoint
  (`walk_forward.MIN_GATE_DAYS`);
- and its top-1 accuracy is not lower beyond its Wilson interval.

Anything short of that is reported as "not shown", never as a win. A result
on fewer than 20 days is reported with its n, and no conclusion is drawn.

## 8. Not decided here

Whether S10 ever trades the portfolio account is Hassan's decision (P7.7,
Rule 6). Shadow is free.
