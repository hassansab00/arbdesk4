# Challenger A: S10 with the readings received by the decision time (pre-registration)

**Written 30 Sep 2026 ~10:30Z, before any Challenger A score existed.** Scored
under `fec-v1` (`docs/FORECAST_EVALUATION_CONTRACT.md`). The result is appended
to the end of this file, and nothing above that point changes after it.

## The confirmed limitation

`remaining_day.features(readings, forecast, hour, ...)` reads readings at or
before the whole local hour `H`, and `s10_shadow.shadow_rows` sets
`hour = local.hour`. The tick decides at `H:36` local (`H:06` in a half-hour zone).

On the 673 live S10 decisions of 27 Sep 12:36Z - 30 Sep 09:36Z (48 cities;
`s10_shadow_checkpoints` against `weather_observations` source IEM):

- The mean decision came 46.0 min after `H:00`.
- 202 decisions (30%) had received at least one reading after `H:00` that the model threw
  away, a mean of 0.40 readings per decision.
- The newest reading received was a median 36.6 min old at decision (p90 45.6 min).
- In 216 decisions a later reading existed but had not been received yet, so valid time alone
  overstates what was known.

Receipt by reading age at decision, the same data:

| age at decision | readings | received |
|---|---|---|
| 2-10 min | 168 | 7.7% |
| 10-20 min | 99 | 87.9% |
| 22-30 min | 7 | 71.4% |
| 31-60 min | 611 | 92-94% |
| 61-86 min | 271 | 92-100% |

## Hypothesis

Using the readings actually received by the decision time predicts the final maximum's
bucket better than the whole-hour cut: lower log loss, not lower top-1. The effect is
expected to be largest where a reading after `H:00` exists and the maximum is still being
set (the late morning to early afternoon hours).

## What changes (and nothing else)

- **The feature contract.** `rd2` (`remaining_day.features_at`) replaces `rd1`. At decision
  time `t` (fractional local hour), over the readings available at `t`:
  - the running maximum `R`, the latest reading and its age;
  - the 1 h and 3 h changes anchored at the latest reading, not at `H`;
  - the forecast interpolated at the latest reading's time, and its error then and over the
    2 h before;
  - the forecast's maximum, cloud and radiation over the hours after `t`;
  - season and the models' day-ahead spread, as before;
  - two new inputs: `obs_age_h`, and `min_frac` (where in the hour `t` falls).
- **Stale input.** A latest reading older than 1.5 h gives no prediction; the incumbent has
  the same 1.5 h rule around `H`. Rows either model cannot predict are counted.
- **The form and the fit.** These are identical to `rd1`: the equal mix of A and B, pooled
  with city shrinkage, and the width calibrated on the training window's last fifth
  (`remaining_day.fit_hour`). It is fitted per decision hour `H = floor(t)`.
- **The forecast.** Unchanged: the day-before best_match hourly run all day. Freshness is
  Challenger B's question, kept apart so each contribution can be identified.
- **Cost.** No new API call or table. Live, the same readings are read with their
  `observed_at`, and the fit is the same pure-Python fit on the same rows.

## Evaluation (fixed now)

- **Rows.** City-days of the venue record (`data/training/market_history`) with:
  - a resolved winner;
  - the event's station equal to the observed station (`data/eval/fec_v1/stations.json`);
  - a whole-day station label;
  - the day-before hourly forecast;
  - and **both** models predicting at `t = H:MM`, for `H` in 7..17.
- **Availability.** Valid time `<= t - 10 min` for both models, since the incumbent can only
  see what was received too. This is an approximate as-of backtest. Sensitivity runs use
  0 and 20 min.
- **Fits.** Both models are refitted at each monthly cutoff on the same training city-days
  (those where both have a row).
- **Development.** Test months January to July 2026, for reading and debugging only.
- **Untouched window.** 1 Aug to 25 Sep 2026, fitted on every day before 1 Aug. Run once.

## Acceptance rule (`fec-v1` §7, unchanged)

On the untouched window, **accepted as a better forecast** only if all hold:

1. The pooled mean log-loss gain (rd1 minus rd2) over hours 7..17 has a 90%
   date-clustered bootstrap interval above 0, over at least 20 dates.
2. No hour's 90% interval lies wholly below 0.
3. rd2's top-1 is not lower than rd1's beyond the Wilson 95% interval.
4. rd2's 80% coverage is within 0.75-0.88.

A significant regression means **rejected**. An interval spanning 0 means
**insufficient evidence**. An accepted candidate then runs in shadow beside `rd1` on
live receipt times (forward, fully as-of) before it replaces anything. It earns no
trading weight by this (`fec-v1` §8).

## Result

(appended below after the run; nothing above changes)

**Run 30 Sep 2026 ~10:37-10:48Z** by `tools/fec_same_day.py`, which reproduces its
outputs from the committed inputs (their sha256 values are in each JSON):

- `data/eval/fec_v1/sd_dev_lag10.json` with `_rows.csv.gz`
- `data/eval/fec_v1/sd_holdout_lag10.json` with `_rows.csv.gz`

The fitted versions (rd1 and rd2, per hour and cutoff) are listed under `fits`.
Receipt lag is 10 min. The as-of reconstruction is approximate.

### Development (January to July 2026, read only for debugging)

69,927 city-day-hours, 165 dates, 48 cities.

- Log loss: rd1 1.1140, rd2 1.1140.
- Pooled gain: +0.0000 (90% interval -0.0005 to +0.0007).
- By hour the effects were small and mixed. The largest were +0.0030 at 13h, +0.0016 at 11h,
  -0.0024 at 12h and -0.0041 at 17h, each with an interval that excluded 0.
- Top-1: 55.1% vs 55.2%.

### Untouched window (1 Aug to 25 Sep 2026)

29,308 city-day-hours, 56 dates, 48 cities.

| | rd1 (incumbent) | rd2 (Challenger A) |
|---|---|---|
| log loss | 1.0942 | 1.0900 |
| top-1 | 55.8% | 55.8% |
| Brier | 0.5400 | 0.5374 |
| median MAE / bias, °C | 0.637 / -0.098 | 0.632 / -0.091 |
| 80% coverage / width, °C | 0.842 / 2.151 | 0.845 / 2.155 |

- **Pooled gain:** **+0.0042**, with a 90% interval of +0.0024 to +0.0060 (95%: +0.0020 to +0.0063).
- **Gain by hour, with its 90% interval:**
  - 7h -0.0062 (-0.0090, -0.0034); 8h -0.0056 (-0.0084, -0.0027); 9h -0.0019; 10h +0.0006;
  - 11h +0.0055 (+0.0023, +0.0084); 12h +0.0043; 13h +0.0099 (+0.0054, +0.0146); 14h +0.0084;
  - 15h +0.0153 (+0.0081, +0.0222); 16h +0.0124 (+0.0058, +0.0197); 17h +0.0031.
- **Rows where a reading after `H:00` was available** (6,340 rows): +0.0184 (+0.0116, +0.0248).
- **The market,** on the 27,992 rows whose ladder was complete and fresh, scored 0.7153 against
  rd2's 1.0747. rd2 minus the market: -0.3593 (-0.3753, -0.3422). Top-1: market 70.0%, rd2 56.1%.

### Verdict under the pre-registered rule: **not accepted**

| rule | result |
|---|---|
| 1. pooled 90% interval above 0, at least 20 dates | holds: +0.0024 to +0.0060 over 56 dates |
| 2. no hour's 90% interval wholly below 0 | **fails**: 7h and 8h regress |
| 3. top-1 not lower beyond Wilson | holds |
| 4. 80% coverage within 0.75-0.88 | holds: 0.845 |

rd2 does not replace rd1.

- The afternoon gains were the hypothesis. The morning regressions were not, and in
  development those hours showed nothing (+0.0009 and +0.0008).
- Restricting rd2 to the later hours would be a new candidate chosen after seeing this window.
  It cannot be validated on it, so it is not claimed here.
- The only valid evidence left for such a variant is forward, fully as-of shadow data, using
  live receipt times.

**Cautions.**
- The incumbent's form was chosen in P7.2 on months that include this window.
- The as-of reconstruction assumes a 10-minute receipt lag. Sensitivity runs at 0 and 20 min
  follow below.
- Both models remain far behind the market's price at the same decision times.

### Sensitivity to the receipt lag (the same window, the same rule)

- **0 min** (every reading with a valid time before the decision counts as received, which is optimistic):
  - pooled +0.0084 (90%: +0.0055, +0.0114);
  - larger afternoon gains: 13h +0.0300, 15h +0.0370;
  - larger morning regressions: 7h -0.0211, 8h -0.0163, 9h -0.0144.

  Not accepted, by the same rule (`sd_holdout_lag0.json`).
- **20 min:**
  - pooled +0.0008 (90%: -0.0001, +0.0016), so insufficient evidence;
  - 7h +0.0019 and 8h +0.0017 (both intervals above 0);
  - 17h -0.0035 (-0.0057, -0.0015) (`sd_holdout_lag20.json`).

**What the three lags show.** The size and even the sign of the effect by hour depend on how
recent a reading is assumed to have been received:
- At 0-10 min, a half-hourly report at `H:20`/`H:30` enters, and the morning hours regress.
- At 20 min it mostly does not, and they do not.

The same-day gain from decision-time readings is real in the afternoon at 0-10 min. It is not
robust enough to promote. The pre-registered verdict (10 min) stands: **not accepted**. The
honest test of any successor is forward, with real receipt times. Those are recorded in
`weather_observations.observed_at` and its nightly mirror, `data/mirror/weather_observations`.
rd2 is not tuned further on this window.

