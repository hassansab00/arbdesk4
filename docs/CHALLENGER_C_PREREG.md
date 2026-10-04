# Challenger C: S10 with the individual models' day-before maxima, bias-corrected (pre-registration)

**Written 30 Sep 2026 ~11:00Z, before any Challenger C score existed.** Scored
under `fec-v1`. Challenger A was not accepted (`docs/CHALLENGER_A_PREREG.md`),
so the incumbent stays `rd1` and C is compared with `rd1`, not with rd2.
Challenger B (the freshest forecast) is not trainable yet (fresh runs with
receipt times begin 6 Sep 2026), so C is tested before it. Each contribution
stays separately identifiable.

**This is the second candidate scored on the untouched window** (1 Aug - 25 Sep).
It is reported as such: two looks at one window raise the chance that one of
them passes by luck. Its rule is the same as A's and is not loosened.

## The confirmed limitation

`rd1` takes the seven day-before model maxima (ECMWF IFS 0.25, GFS, ICON, UKMO,
JMA, GEM, Meteo-France; `models_daily.csv.gz`, lead 1) only as their population
spread (`models_spread`). Their level enters only through best_match's
trajectory (`fc_rest_minus_now`, `fc_day_minus_R`). Each model's own warm or cold
bias at a station is not used at all.

## Hypothesis

The models' bias-corrected maxima, as a constrained summary of how much more
warming they expect above the running maximum, improve the probability of the
final maximum's bucket. The effect should be largest in the morning, where the
forecast carries most of the information and where Challenger A regressed.

## What changes (and nothing else)

- **Features:** `rd1`'s twelve, plus three, computed at the decision hour from the models
  available for the city-day:
  - `models_rise_c`: the mean over models of `max(0, (tmax_m - bias_m,city) - R)`, the
    expected remaining rise above the running maximum.
  - `models_frac_up`: the share of models with `tmax_m - bias_m,city > R + 0.5`.
  - `models_n`: how many models are present (4 to 7).
- **The bias,** a Rule 11 learned parameter:
  - `bias_m,city` is the mean of `tmax_m - station maximum` over training days before the
    cutoff;
  - it is shrunk to the model's pooled bias with a weight of `n / (n + 30)` (a prior of
    30 days);
  - the pooled bias is shrunk to 0 with a weight of `n / (n + 30)`;
  - it is bounded at +/-4 C and refitted at each cutoff;
  - it is never computed on a test date.
- **Correlated models.** They enter only as a mean and a share, never as seven free
  coefficients, so seven related models are not counted as seven pieces of evidence. The
  ridge in `fit_hour` shrinks the three new inputs like the others.
- **Missing providers.** Fewer than 4 models means no row, so the rd1 prediction stands.
  Excluded rows are counted.
- **Unchanged:** the form, the fit (`remaining_day.fit_hour`), the readings (`features`, the
  whole-hour cut, as rd1), the forecast (the day-before best_match trajectory) and the
  decision hours (7..17 at `H:36`, at a 10-minute receipt lag).
- **Cost:** none new. The inputs are in the day-before record S10 already fetches at 07-09
  local (`s10_day1_inputs.models`).

## Evaluation (fixed now, identical to A's)

- **Rows:** the venue record's city-days with a resolved winner, the settlement station equal
  to the observed station, a whole-day station label and the day-before forecast, where
  **both** rd1 and C predict. Both are refitted at each cutoff on the same training rows.
- **Development:** test months January to July 2026, for debugging only.
- **Untouched window:** 1 Aug to 25 Sep 2026, fitted on every day before 1 Aug. Run once.

## Acceptance rule (`fec-v1` §7)

On the untouched window, **accepted** only if all hold:

1. The pooled mean log-loss gain (rd1 minus C) over hours 7..17 has a 90% date-clustered
   interval above 0, over at least 20 dates.
2. No hour's 90% interval lies wholly below 0.
3. C's top-1 is not lower than rd1's beyond the Wilson 95% interval.
4. C's 80% coverage is within 0.75-0.88.

A significant pooled regression means **rejected**. An interval spanning 0 means
**insufficient evidence**. Anything accepted then runs in shadow (forward, as-of) before it
replaces rd1, and earns no trading weight by this (`fec-v1` §8).

## Result

(appended below after the run; nothing above changes)

**Run 30 Sep 2026; development finished 10:56Z and the untouched window 10:59Z (file times)**, by
`tools/fec_same_day.py --challenger rd3`, which reproduces its outputs from the committed
inputs (their sha256 values are in each JSON):

- `data/eval/fec_v1/sd_dev_lag10_rd3.json` with `_rows.csv.gz`
- `data/eval/fec_v1/sd_holdout_lag10_rd3.json` with `_rows.csv.gz`

**When the rule was fixed.** The header's "~11:00Z" is loose. The session log records this
file's write at 10:49:31Z, and `remaining_day.py`'s rd3 code after it (10:49:49Z). The first rd3
output (development) was written at 10:56:21Z. Nothing above "Result" was edited after that.

The fitted versions (rd1 and rd3, per hour and cutoff) are under `fits`. Receipt lag is
10 min. The as-of reconstruction is approximate. **No row was excluded for having fewer
than 4 models:** the build counted no row where rd1 predicts and rd3 does not. Its
`row_counts` hold only `no_forecast` 72 (city-days) and `no_row_both` 1,265 (rows rd1
lacks too), over all dates.

### Development (January to July 2026, read only for debugging)

70,071 city-day-hours, 165 dates, 47 cities.

- Log loss: rd1 1.1145, rd3 1.0499.
- Pooled gain: **+0.0646** (90% interval +0.0593 to +0.0699).
- Every hour positive, largest in the morning: 7h +0.1373 ... 12h +0.0607 ... 17h +0.0035,
  each 90% interval above 0.
- Top-1: 55.0% vs 57.6%. 80% coverage 0.829. One city negative (shenzhen -0.111).

### Untouched window (1 Aug to 25 Sep 2026)

29,334 city-day-hours, 56 dates, 48 cities.

| | rd1 (incumbent) | rd3 (Challenger C) |
|---|---|---|
| log loss | 1.0942 | 1.0224 |
| top-1 (Wilson 95%) | 55.8% (55.2-56.3) | 58.6% (58.0-59.2) |
| Brier | 0.5401 | 0.5158 |
| MAE / bias of the median, °C | 0.637 / -0.097 | 0.568 / -0.073 |
| 80% coverage / width, °C | 0.842 / 2.152 | 0.844 / 2.001 |

- **Pooled gain:** **+0.0718**, with a 90% interval of +0.0644 to +0.0787 (95%: +0.0629 to +0.0793).
- **Gain by hour, with its 90% interval** (about 2,665 rows each):

  | hour | rd1 | rd3 | gain (90%) | top-1 rd1 → rd3 | market top-1 |
  |---|---|---|---|---|---|
  | 7h | 1.6159 | 1.4745 | +0.1415 (+0.1283, +0.1536) | 35.6% → 40.5% | 50.9% |
  | 8h | 1.5851 | 1.4485 | +0.1366 (+0.1229, +0.1485) | 35.5% → 41.3% | 51.7% |
  | 9h | 1.5295 | 1.4047 | +0.1248 (+0.1121, +0.1366) | 36.3% → 41.9% | 53.1% |
  | 10h | 1.4393 | 1.3455 | +0.0938 (+0.0817, +0.1049) | 40.2% → 44.4% | 54.9% |
  | 11h | 1.3713 | 1.2780 | +0.0934 (+0.0831, +0.1035) | 41.1% → 45.3% | 57.2% |
  | 12h | 1.2459 | 1.1686 | +0.0773 (+0.0680, +0.0872) | 45.6% → 48.9% | 63.0% |
  | 13h | 1.0913 | 1.0340 | +0.0573 (+0.0485, +0.0661) | 53.2% → 55.3% | 71.0% |
  | 14h | 0.8572 | 0.8235 | +0.0337 (+0.0246, +0.0435) | 65.1% → 66.0% | 81.2% |
  | 15h | 0.6200 | 0.5973 | +0.0227 (+0.0149, +0.0302) | 78.0% → 78.5% | 90.4% |
  | 16h | 0.3993 | 0.3926 | +0.0067 (+0.0009, +0.0125) | 88.3% → 88.2% | 97.5% |
  | 17h | 0.2859 | 0.2831 | +0.0028 (+0.0009, +0.0049) | 94.1% → 94.1% | 99.0% |

  (The market column is on each hour's complete-ladder subset, about 2,600 rows.)
- **By city:** 45 of 48 gain. Three lose: busan -0.0309, zhengzhou -0.0066 and
  houston -0.0065 (per-city means, no interval). Median shanghai +0.0702; largest
  mexico_city +0.230.
- **The market,** on the 28,017 rows whose ladder was complete and fresh, scored 0.7154 against
  rd3's 1.0064. rd3 minus the market: **-0.2909** (-0.3065, -0.2760). Top-1: market 70.0%,
  rd3 58.9%, rd1 56.0%.

### Verdict under the pre-registered rule: **accepted** (as a better forecast than rd1)

| rule | result |
|---|---|
| 1. pooled 90% interval above 0, at least 20 dates | holds: +0.0644 to +0.0787 over 56 dates |
| 2. no hour's 90% interval wholly below 0 | holds: every hour's interval is above 0 |
| 3. top-1 not lower beyond Wilson | holds: 58.6% against 55.8% |
| 4. 80% coverage within 0.75-0.88 | holds: 0.844 |

What this does and does not mean (`fec-v1` §8):

- rd3 is a better same-day forecast of the final maximum's bucket than rd1, on data it was not
  fitted or chosen on. It does **not** replace rd1 yet: the pre-registration requires forward
  shadow evidence first, fully as-of, with live receipt times.
- It earns **no trading weight**. The market is still far ahead at every hour, by more at the
  hours where rd3 gains most.
- The effect is where the hypothesis put it: largest in the morning, where the forecast carries
  most of the information and where Challenger A regressed.

**Cautions.**

- **The second candidate on this window.** Two looks at one window raise the chance that one of
  them passes by luck. The margin here (lower 90% bound +0.064, against A's +0.0024) is far
  outside what a second look explains, but the window is now spent for any further candidate.
- **The incumbent's form was chosen in P7.2 on months that include this window**, as for A.
- **The as-of reconstruction is approximate** (10-minute receipt lag, no historical receipt times).
- **Late hours in `tmax_c`.** `tmax_c` is the whole day's maximum over the lead-1 series
  (`data/training/previous_runs/README.md`: for an hour H, the value from a run started at least
  24 h before H). Its hours after 17:00 local come from runs started up to midnight the day
  before; if a provider published one of those late, it would be out after a 7h decision. The
  share of lead-1 model-days where `tmax_c` exceeds the 00-17 maximum `tmax_00_17_c`, measured
  from the committed file: 5.1% by more than 0.05 °C and 1.4% by more than 0.5 °C (146,460
  model-days); in the window, 4.5% and 0.8% (18,816). The sensitivity run below removes those
  hours entirely.

### Sensitivity: the model maxima stopped at 17:00 (not an acceptance test)

The same run with `--models-column tmax_00_17_c` (each model's maximum over 00-17 local, whose
runs all started by 17:00 the day before), on the same window and rows:
`data/eval/fec_v1/sd_holdout_lag10_rd3_m0017.json` with `_rows.csv.gz` (its `verdict` field
says "sensitivity only").

- Log loss rd1 1.0942, rd3 1.0237; pooled gain **+0.0705** (90%: +0.0635, +0.0770), against
  +0.0718 with the whole-day maxima.
- Every hour positive: 7h +0.1435 (+0.1304, +0.1554) ... 15h +0.0200, 16h +0.0045 (+0.0006,
  +0.0084), 17h +0.0008 (+0.0002, +0.0015).
- Top-1 58.6%; 80% coverage 0.845, width 2.011 °C. The same three cities lose (busan -0.0198).
- The market still ahead by -0.2921 (-0.3074, -0.2772).

So the result does not rest on the late hours. The afternoon gains shrink slightly (16-17h),
which is where a late peak would matter. The forward shadow must use the maxima as they
are actually known at each decision, whichever form the live record stores.

## Forward shadow (fixed 30 Sep 2026, before rd3's first forward row)

**What runs.** `data/models/remaining_day/challenger_rd3.json`, version `rd3:2026-09-25:555719d4a1`:

- **Fitted by** `tools/fit_remaining_day.py --challenger rd3` on the same six inputs and labels as the live
  rd1 file. The same run refits rd1 to its live version, `rd1:2026-09-25:f5372ebb05`, with identical hours.
- **Bias table:** 336 model-city biases from 142,776 pairs. Models run cold against the station maxima:
  pooled from -0.29 °C (GFS) to -1.47 °C (JMA). One cell sits at the -4 °C bound (JMA at jeddah).

**Where it runs.** At every tick checkpoint S10 records, `s10_shadow.record` writes an rd3 row beside
rd1's, under rd3's own `model_version`. The inputs are the same:

- the IEM readings, cut at the whole hour;
- the `s10_day1_inputs` row fetched at 07-09 local, whose `models` are the whole-day lead-1 maxima,
  the `tmax_c` this was scored on.

**What it cannot touch.**
- rd3 rows never reach the `ladders` the engine's S10 decisions read.
- `strategy_learn` reads rd1's rows only (`model_version like 'rd1:*'`).
- A failure in rd3 never touches rd1's rows.

**Replay check.** Three live rd1 rows of 30 Sep (noon, ankara, helsinki and moscow, tick 09:36:40Z),
replayed from the database's own inputs and receipts, match the stored rows exactly: top bucket, top
probability, median and all 12 inputs. rd3 on the same inputs gave:
- ankara: the same top bucket, 0.565 → 0.427;
- helsinki: the same top bucket, 0.332 → 0.363;
- moscow: the same top bucket, 0.514 → 0.506.

**The forward rule (fixed now).**

- **Rows:** pairs of rd1 and rd3 rows for the same city, date and checkpoint, both recorded live. They
  are scored against the venue winner of `v_checkpoint_outcome`, on the full ladder: log loss with p
  floored at 1e-6, and top-1. The 80% interval (`q10_c`..`q90_c`) is checked against the settled
  station maximum.
- **Looks:** the first at 20 settled dates. Only if the pooled interval there spans 0, one final
  look at 40.
- **Accepted,** that is, S10's shadow ladders switch from rd1 to rd3, only if all of these hold:
  1. the pooled log-loss gain (rd1 minus rd3) has a 90% date-clustered interval above 0;
  2. no checkpoint's 90% interval lies wholly below 0;
  3. rd3's top-1 is not below rd1's beyond the Wilson 95% interval;
  4. rd3's 80% coverage is within 0.75-0.88.
- **Rejected** on a significant pooled regression, at either look: rd3 is then removed from the tick.
  **Insufficient evidence** if the interval still spans 0 at 40: rd3 stays in shadow and changes nothing.
- **Fixed during the test:** neither file is refitted. A refit of either is a new version and starts a
  new pair and a new count.
- **What acceptance does not do.** It changes no trading weight and no capital. The engine's belief
  keeps its own gate (P5.3: w at its prior 0 until a scope passes its forward test), and the market
  still leads rd3 by -0.29 in log loss on the historical window.

### Scoring (built 4 Oct 2026, before any rd3 forward score was computed)

`tools/fec_s10_forward.py` applies the rule above.
- `sql --export-date D` writes the export statement. Run it through the Supabase SQL tool: the
  table is the service role's, so not as anon.
- `score` reads the gzipped export and writes the report.
- On 4 Oct the export for the real pair returned no rows: rd3's first rows are 3 Oct, which was
  not yet settled. So no rd3 score existed when the four points below were fixed. They make
  precise what the rule left open. None of them loosens it.

1. **"The settled station maximum" for the coverage check means the label the model is trained,
   and its 80% interval calibrated, on:** `derived_city_day_features.max_c`, the station's
   whole-day maximum.
   - The settlement's `fact_band_outcome.observed_max_c` is a different series.
   - On 602 rd1 pairs (30 Sep - 2 Oct, the live rd1 version's first three dates) the two differ by more than 0.05 °C on 108 pairs (mean
     absolute 0.112 °C).
   - Most of those are US cities, where the label is the whole-°C METAR maximum and the
     settlement comes from °F reports. On 1 Oct at houston the label was 33 °C and the
     settlement 31.67 °C.
   - The settlement figure is reported beside the label and never used for the check.
2. **A date is settled** once it is at least 3 days before the export date. From 30 Sep to 2 Oct,
   every market reached the page within 13.7 h of its local day end. Pairs on a settled date
   without exactly one winner are left out and counted.
3. **A look is a fixed set of dates:** the first 20 (and, if needed, the first 40) settled dates
   from rd3's first. So when the tool is run cannot change a verdict. Below 20 dates it reports
   counts only and computes no comparison.
4. **A cross-check stops the scoring.** If any scored pair's winner (`v_checkpoint_outcome`)
   disagrees with `fact_band_outcome`'s settled band, no look is computed until that is
   explained.
   - The plumbing check run on 4 Oct, rd1 paired with itself for 1 Oct (234 pairs), returned exactly
     one agreeing winner on every pair. Five of its rows, verbatim, are the tests' fixture, which
     scores a gain of exactly 0.
   - On 30 Sep - 2 Oct the two sources agreed on all 602 pairs.

The market (`v_checkpoint_outcome`, only when its ladder is complete and its decision is within
15 minutes of rd1's row) is reported for information. It is not part of the rule.

**When the first look can run.** rd3's 20th date is 22 Oct, which is settled from an export on
25 Oct.
