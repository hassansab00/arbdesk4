# The day-ahead ladder with the floor (`da_floor`): forward test, pre-registration

**Written 4 Oct 2026 at about 12:00Z, before any forward row of this candidate existed.**
The tick has not recorded it yet. The capture is built in the same pull request as this file
(`scripts/variant_shadow.py`, table `variant_shadow_checkpoints`). Scored under `fec-v1`
(`docs/FORECAST_EVALUATION_CONTRACT.md`).

## Where the candidate comes from
`docs/P11_INTRADAY_DIAGNOSIS_2026-10-04.md` (P1.1) replayed every same-day checkpoint of
27 Sep – 3 Oct.
- **The switch at midnight.** At local midnight the engine moves to a same-day centre and a
  narrower width. Before the peak, that cost 0.110 – 0.297 in log loss against keeping the
  day-ahead centre and width with the observed floor. The width was the larger part.
- **The floor** helped at every checkpoint.

Those seven dates chose the candidate. They are design data, not evidence for it.
**No date before the first forward row counts toward the decision below.**

## Hypothesis
At the same-day checkpoints, the day-ahead call's centre and width, cut by the observed floor,
give the venue's winning bucket more probability than the ladder the engine serves.

## The candidate, `da_floor:v1` (nothing else changes)
At each same-day checkpoint the tick writes: `morning`, `noon`, `prepeak_2h`, `prepeak_1h`,
`postpeak_1h`. `d1_eve` is not one: the day has not begun.

**The tick records these inputs in the same run as the served call:**
- **The day-ahead call.** This is the `centre_c` and `sigma_c` of the last `band_probabilities`
  row for that market's buckets computed before the city's local midnight that begins the
  target day. The newest `computed_at` wins, and `prob_id` breaks a tie. It is the call
  `v_city_hit_history` grades (P4.6). It is known before every same-day checkpoint.
  - Checked live on 4 Oct, before this file was finished: on all 186 graded city-days of
    30 Sep – 3 Oct (48 cities), the capture's lookup returns the same pricing row as the
    view. The time is equal, the width is equal, and the centre is equal to the view's display
    rounding of 0.01 °C (at most 0.005 °C apart).
  - P1.1 used the rounded value; the capture stores the unrounded one.
- **The floor and q.**
  - The floor is the one the served ladder used (`prediction_checkpoints.running_max_c`).
  - q is the measurement layer the engine read for that city in the same run
    (`cities.observation_q_down` / `_up`), or the pooled default where the city has none.
    q is recorded only where there is a floor.

**How the ladder is computed:**
- `probability_engine.compute_band_probabilities(centre, sigma, unit, buckets, floor, q_down, q_up)`
  over the market's buckets, the engine's own integrator with the P3.1 atom.
- Each bucket is clamped to [1e-6, 1 - 1e-6] and rounded to 6 decimals, as the served ladder is.
- No calibration, because the served ladder has none while `settings.calibration_map.applies`
  is false. If it turns on during the test, see the exclusions below.
- **Nothing is learned.** There is no parameter, so Rule 11 has nothing to bound. A changed
  definition is a new version (`da_floor:v2`), never an edit; the table is append-only.
- **Same as the research tool.** `tests/test_variant_shadow.py` holds the live capture to the
  research definition. On recorded P1.1 rows, `variant_shadow.ladder` equals
  `tools/p11_intraday_ablation.py`'s `da_floor` ladder.

## Rows
**Included.** Each `variant_shadow_checkpoints` row is matched to the served call of the same
city, date and checkpoint: the first capture, `v_checkpoint_calls.first_call`. The served call is
graded by `fact_checkpoint_outcome`. A row counts when the venue's winner is on the ladder
(`ladder_has_winner`).

**Left out, and counted by reason:**
- no day-ahead call: no pricing of the market before midnight;
- the served ladder was calibrated (`calibrated:` in its reasons; the shadow row records it);
- no served first call, or no graded outcome yet;
- the winner is not on the ladder;
- the tick ran out of time before the capture (counted in the tick's log).

**The first forward date** is the first target date with a shadow row, expected to be 5 Oct. The
date that row was decided is recorded on it.

## Scores (fixed now)
**Measures:**
- log loss on the winner, with p floored at 1e-6;
- top-1, with ties broken by bucket id and a Wilson 95% interval;
- multiclass Brier, secondary.

**How they are reported:**
- Per checkpoint and pooled over the five.
- Each gain is paired: served minus `da_floor`, on identical rows.
- Intervals: a date-clustered bootstrap (`tools/fec_same_day.cluster_boot`, seed 11, 1,000
  resamples), 90% for the rule and 95% reported beside it.
- The market is shown beside both on the rows where its book was whole, as paired differences.
  So is S10 rd1, where it has a row. Both are descriptive: neither enters the rule.
- Rows, dates, cities and each exclusion are reported by reason.

## When it is looked at
- Before 20 target dates have scored rows, only counts are read: rows written, rows skipped by
  reason. No score is computed or reported.
- **The first look** is the first day on which at least 20 target dates have scored rows, about
  25 Oct.
- If that look reads "insufficient evidence", there is one more look at 40 dates and then the
  test stops. Nothing is looked at in between.

## Acceptance rule
At the first look (or the second), `da_floor` is **accepted** as a better same-day ladder than the
served one only if all of these hold:

1. The pooled mean log-loss gain (served minus `da_floor`) over the five checkpoints has a 90%
   date-clustered interval above 0, over at least 20 dates.
2. No checkpoint's 90% interval lies wholly below 0.
3. `da_floor`'s top-1 is not lower than the served top-1 beyond the Wilson 95% interval.

**Other outcomes:**
- A pooled 90% interval wholly below 0 means **rejected**.
- Anything else is **insufficient evidence**.

## What a verdict does
**Nothing changes by itself.**
- **Accepted:** a proposal to serve `da_floor` at the same-day checkpoints, with its result. Hassan
  decides, as for S10 (P7.6 / P7.7). Acceptance is against the served ladder only. Beating the
  market is not claimed by this rule and is reported separately.
- **Rejected:** the capture is switched off with the result recorded here.

## Known limits, stated before the data
- **The served engine can change during the window** (new `engine_version`s). The comparison is
  always with the ladder actually served at that checkpoint, and the result lists the changes
  that touched the same-day path.
- **The day-ahead call depends on `pipeline_intraday`'s hours.** From 4 Oct (#302) those hours are
  02/08/14/20 UTC. Over the 48 cities' midnights that puts the call 1.64 h before midnight on
  average, against 2.01 h on the six runs the P1.1 dates had. The forward test measures the
  candidate as it runs from now on.
- **The floor's feed.** Floors before 4 Oct included the NWS five-minute readings for US cities
  (#297). Every forward row is after that fix.

## Result
(appended below after the first look; nothing above changes)

### First look, 25 Oct 2026 (~10:40Z): insufficient evidence

Run as fixed above, read-only against production
(`data/eval/p11/da_floor_first_look_2026-10-25/`: `query.sql`, its result
`aggregates.txt` (md5 b4d0c726…, equal to the md5 the database computed),
`result.json` by `tools/p11_da_floor_look.py`, tested in
`tests/test_p11_da_floor_look.py`).

**Rows.** 4,783 `da_floor:v1` rows at the five checkpoints, target dates
4-25 Oct. Scored: **4,666 rows on 21 target dates (4-24 Oct)**; both ladders hold the same buckets on every scored row. Left
out: 117 rows with no graded outcome yet (18 Oct and 25 Oct). None left out for
a missing day-ahead call, a calibrated served ladder or a winner off the
ladder. Tick time-outs before the capture were not counted here (the tick's
logs).

**The rule** (log loss, served minus `da_floor`; 90% date-clustered interval,
seed 11, 1,000 resamples; 95% beside it):

| | rows | gain | 90% | 95% | top-1 served | top-1 `da_floor` |
|---|---|---|---|---|---|---|
| pooled | 4,666 | +0.0020 | -0.0569 to +0.0655 | -0.0687 to +0.0736 | 51.48% (50.04-52.91) | 48.26% (46.83-49.70) |
| morning | 926 | +0.1746 | +0.1134 to +0.2405 | +0.0962 to +0.2556 | 37.37% | 39.20% |
| noon | 934 | -0.0000 | -0.0678 to +0.0720 | -0.0867 to +0.0860 | 47.11% | 43.68% |
| prepeak_2h | 934 | -0.0349 | -0.1081 to +0.0396 | -0.1194 to +0.0513 | 43.68% | 37.47% |
| prepeak_1h | 933 | -0.0209 | -0.0864 to +0.0461 | -0.1053 to +0.0576 | 50.91% | 45.44% |
| postpeak_1h | 939 | -0.1067 | -0.1861 to -0.0180 | -0.2028 to -0.0001 | 78.06% | 75.29% |

- Rule 1 fails: the pooled 90% interval includes 0.
- Rule 2 fails: post-peak's 90% interval lies wholly below 0 (`da_floor` worse).
- Rule 3 fails: `da_floor`'s top-1, 48.26%, is below the served top-1's Wilson
  95% lower end, 50.04%.
- Not rejected: the pooled interval is not wholly below 0.

**Verdict: insufficient evidence.** One more look at 40 scored dates, then the
test stops; nothing is looked at in between. Brier (secondary), pooled
served minus `da_floor`: -0.0126 (90% -0.0284 to +0.0043).

**Beside the rule, descriptive.** The market, on the 4,122 rows with a whole
book: top-1 65.94%; log loss served minus market +0.5073 (90% +0.4390 to
+0.5765), `da_floor` minus market +0.5056 (+0.4494 to +0.5607). S10 `rd1`
(`rd1:2026-09-25:f5372ebb05`), on 4,550 rows: top-1 51.27%; served minus `rd1`
+0.0994 (+0.0368 to +0.1647), `da_floor` minus `rd1` +0.1063 (+0.0596 to
+0.1488). Neither beats the market; this rule does not ask it to.

**The served engine changed during the window.** Every served first call
carries its `engine_version` (the deployed commit): 151 distinct versions over
the 4,911 first calls at these checkpoints, 4-24 Oct (47 cities). The one change of the same-day path itself: from 17 Oct
(15:37Z, first call) the served noon, prepeak_2h, prepeak_1h and postpeak_1h
calls are WXPredict's frozen v3 (`model_path` `wxpredict`, `wxrd3-horizons-v1`,
#409): 1,274 of those 4,911 first calls, 17-24 Oct; morning stayed on
the forecast path. The comparison is with the ladder actually served, as
fixed above.
