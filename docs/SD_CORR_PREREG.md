# The engine's station-corrected path on the day itself (`sd_corr`): forward test, pre-registration

**Written 5 Oct 2026, before any forward row of this candidate existed.** The capture,
`scripts/variant_shadow.py` (`sd_corr:v1`, beside `da_floor:v1` in `variant_shadow_checkpoints`), was
built after this file and before any row, and does what this file says. Scored under
`fec-v1` (`docs/FORECAST_EVALUATION_CONTRACT.md`), beside `da_floor:v1`
(`docs/P11_DA_FLOOR_PREREG.md`), whose test this does not change.

## Where the candidate comes from
`docs/SD_CORR_REPLAY_2026-10-05.md` replayed P1.1's same-day checkpoints of 28 Sep – 3 Oct
(6 dates, 1,343 rows).
- **Against the served ladder:** keeping the engine's station-corrected centre and the station
  width on the day itself, with the observed floor, lowered log loss at every checkpoint, by
  0.044 – 0.481.
- **Against `da_floor`:** it was better before the peak, by 0.090 – 0.203.
- **The width did most of it.**

Those six dates chose the candidate. They are design data, not evidence for it.
**No date before the first forward row counts toward the decision below.**

## Hypothesis
At the same-day checkpoints, the station-corrected centre and the station width live at the
decision, cut by the observed floor, give the venue's winning bucket more probability than the
ladder the engine serves.

## The candidate, `sd_corr:v1` (nothing else changes)
At each same-day checkpoint the tick writes (`morning`, `noon`, `prepeak_2h`, `prepeak_1h`,
`postpeak_1h`; not `d1_eve`), the tick records, in the same run as the served call:

- **The live corrected row.** This is the `derived_corrected_forecast` row of the city and
  target date as the tick reads it at that moment:
  - it must have been computed at or before the decision;
  - it must be younger than `settings.station_correction_pricing.max_age_hours` (36 when absent),
    the age the engine uses.

  From it:
  - **the centre** is `combined_c` (P3.9: each of the 7 models' newest run, corrected for its
    error at the settlement station; lead 0 takes lead 1's corrections);
  - **the width** is `width_c` (P3.9 part 3: fitted per city on this combination's own errors;
    lead 0 takes lead 1's width).

  The row's `version`, `width_version`, `computed_at`, `lead_days` and `n_sources` are recorded
  beside the ladder.
- **The floor and q** are exactly as for `da_floor`:
  - the floor the served ladder used (`prediction_checkpoints.running_max_c`);
  - the q the engine read for the city in the same run, recorded only where there is a floor.

**How the ladder is computed:** exactly as `da_floor`.
- `probability_engine.compute_band_probabilities(centre, width, unit, buckets, floor, q_down, q_up)`
  over the market's buckets.
- Each bucket is clamped to [1e-6, 1 - 1e-6] and rounded to 6 decimals.
- No calibration.

**It is captured whatever the pricing switches say.** It reads the nightly fit's rows, not the
engine's decision to price from them, so a switch turned off does not stop the test.

**Rule 11.** Nothing new is learned here. The centre's corrections and the width are the
nightly fits of `scripts/station_correction.py`, which already carry Rule 11's prior, bounds,
minimum sample, nightly step and version. Each fit reads only target days before its night, and
every row this test scores is a target day after the fit it read. A changed definition is a new
version (`sd_corr:v2`), never an edit; the table is append-only.

**Same as the research tool.** The capture's tests hold it to
`tools/sd_corrected_replay.py`: on recorded inputs, the live ladder equals the replay's
`sd_corr` ladder.

## Rows
**Included.** Each `sd_corr` row is matched to the served call of the same city, date and
checkpoint: the first capture, `v_checkpoint_calls.first_call`. The served call is graded by
`fact_checkpoint_outcome`. A row counts when the venue's winner is on the ladder
(`ladder_has_winner`).

**Left out, and counted by reason:**
- no live corrected row (none computed before the decision, or older than the max age);
- the live row has no width;
- the served ladder was calibrated (`calibrated:` in its reasons; the row records it);
- no served first call, or no graded outcome yet;
- the winner is not on the ladder;
- the tick ran out of time before the capture (counted in the tick's log).

**The first forward date** is the first target date with an `sd_corr` row. The decision time is
recorded on each row.

## Scores (fixed now)
**Measures:**
- log loss on the winner, with p floored at 1e-6;
- top-1, ties broken by bucket id, with a Wilson 95% interval;
- multiclass Brier, secondary.

**How they are reported:**
- Per checkpoint and pooled over the five.
- Each gain is paired on identical rows: served minus `sd_corr`, and `da_floor` minus `sd_corr`
  where both have a row.
- Intervals: a date-clustered bootstrap (`tools/fec_same_day.cluster_boot`, seed 11, 1,000
  resamples), 90% for the rule and 95% reported beside it.
- The market is shown beside it on the rows where its book was whole, as paired differences.
  So is S10 rd1 where it has a row. Neither enters the rule.
- Rows, dates, cities and each exclusion are reported by reason.

## When it is looked at
- Before 20 target dates have scored rows, only counts are read: rows written, rows skipped by
  reason. No score is computed or reported.
- **The first look** is the first day on which at least 20 target dates have scored rows (about
  26 Oct if the capture is live from 6 Oct).
- If that look reads "insufficient evidence", there is one more look at 40 dates and then the
  test stops. Nothing is looked at in between.

## Acceptance rule
At the first look (or the second), `sd_corr` is **accepted** as a better same-day ladder than the
served one only if all of these hold:

1. The pooled mean log-loss gain (served minus `sd_corr`) over the five checkpoints has a 90%
   date-clustered interval above 0, over at least 20 dates.
2. No checkpoint's 90% interval lies wholly below 0.
3. At no checkpoint is `sd_corr`'s top-1 rate below the lower end of the served top-1's Wilson
   95% interval. This is stated because the design replay was worse an hour after the peak.

**Other outcomes:**
- A pooled 90% interval wholly below 0 means **rejected**.
- Anything else is **insufficient evidence**.

## Against `da_floor`
Both candidates fill the same place, so the result reports the pooled `da_floor` minus `sd_corr`
gain on the rows both have, with its 90% date-clustered interval.
- **Both accepted, interval above 0:** the proposal is `sd_corr`.
- **Both accepted, interval below 0:** the proposal is `da_floor`.
- **Both accepted, interval includes 0:** both are proposed side by side.
- **One accepted:** that one is proposed, with this comparison beside it.

## What a verdict does
**Nothing changes by itself.**
- **Accepted:** a proposal to serve it at the same-day checkpoints, with its result. Hassan
  decides, as for S10 (P7.6 / P7.7) and `da_floor`. Acceptance is against the served ladder only;
  beating the market is not claimed by this rule and is reported separately.
- **Rejected:** the capture is switched off, with the result recorded here.

## Known limits, stated before the data
- **The served engine can change during the window** (new `engine_version`s). The comparison is
  always with the ladder actually served at that checkpoint. The result lists the changes that
  touched the same-day path.
- **The station width has its own forward test** (`P3.9_width_score`, day-ahead, lead 1). If it
  is turned on for pricing during the window, the served engine's evening-before call changes,
  but its same-day ladder does not. That test and this one are read separately.
- **The centre is as fresh as the nightly fit.** `derived_corrected_forecast` is computed once a
  night (about 05:00Z) from the models' newest runs.
  - For the Americas, and for Asia before about 05:00Z, the newest is lead 1 at the same-day
    checkpoints. The candidate is measured as it runs.
  - More frequent runs would be a new version.
- **The nightly fit and the forward rows can fail on a night.** A city-day without a live row is
  left out by reason, never filled from another source.
- **The floor's feed.** Every forward row is after #297 (US floors from the settlement reports).

## Result
(appended below after the first look; nothing above changes)

### First look, 26 Oct 2026 (~10:35Z): insufficient evidence

Run as fixed above, from the nightly mirror in the repository (the database was
not reachable from the session that ran it): `tools/p11_sd_corr_look.py`, its
result `data/eval/p11/sd_corr_first_look_2026-10-26/result.json`, which names
the 114 mirror files it read with their sha256; `tests/test_p11_sd_corr_look.py`
rebuilds it from exactly those files. The mirror ran to the night of 26 Oct
(the newest `sd_corr` row is for 26 Oct). The market columns are
`fact_checkpoint_outcome`'s.

**Rows.** 4,765 `sd_corr:v1` rows at the five checkpoints, target dates 5-26
Oct. Scored: **4,656 rows on 21 target dates (5-25 Oct), 47 cities**; both
ladders hold the same buckets on every scored row. Left out: 109 with no graded
outcome yet (18, 25 and 26 Oct). None left out for a missing or widthless live
row, a calibrated served ladder or a winner off the ladder. Tick time-outs
before the capture were not counted here.

**The rule** (log loss, served minus `sd_corr`; 90% date-clustered interval,
seed 11, 1,000 resamples; 95% beside it; top-1 ties by bucket id, served with
its Wilson 95% interval):

| | rows | gain | 90% | 95% | top-1 served | top-1 `sd_corr` |
|---|---|---|---|---|---|---|
| pooled | 4,656 | +0.0460 | -0.0221 to +0.1094 | -0.0364 to +0.1232 | 52.00% (50.56-53.43) | 49.33% |
| morning | 927 | +0.2411 | +0.1771 to +0.3058 | +0.1683 to +0.3206 | 37.65% (34.59-40.81) | 40.02% |
| noon | 931 | +0.0389 | -0.0397 to +0.1189 | -0.0548 to +0.1385 | 47.91% (44.71-51.12) | 45.11% |
| prepeak_2h | 932 | +0.0063 | -0.0816 to +0.0916 | -0.1011 to +0.1085 | 44.21% (41.05-47.41) | 40.45% |
| prepeak_1h | 929 | +0.0124 | -0.0628 to +0.0827 | -0.0749 to +0.0933 | 51.45% (48.24-54.65) | 46.50% |
| postpeak_1h | 937 | -0.0672 | -0.1444 to +0.0110 | -0.1651 to +0.0199 | 78.55% (75.81-81.06) | 74.39% |

- Rule 1 fails: the pooled 90% interval includes 0.
- Rule 2 holds: no checkpoint's 90% interval lies wholly below 0.
- Rule 3 fails at prepeak_2h, prepeak_1h and postpeak_1h: `sd_corr`'s top-1 is
  below the served top-1's Wilson 95% lower end there.
- Not rejected: the pooled interval is not wholly below 0.

**Verdict: insufficient evidence.** One more look at 40 scored dates (about
13 Nov; `trig_01PFzq7b8YbCvk6WFf8UiB91`, 15 Nov 10:30Z, counts first), then the
test stops; nothing is looked at in between. Brier (secondary), pooled served
minus `sd_corr`: -0.0012 (90% -0.0214 to +0.0174).

**Against `da_floor`** (the same 4,656 rows, both have a row on each): pooled
`da_floor` minus `sd_corr` +0.0595 (90% +0.0338 to +0.0872): `sd_corr` loses
less than `da_floor`. Neither is accepted, so nothing is proposed.

**Beside the rule, descriptive.** The market, on the 4,112 rows with a whole
book: top-1 65.95%; log loss served minus market +0.4861 (90% +0.4152 to
+0.5640), `sd_corr` minus market +0.4436 (+0.3897 to +0.5084). S10 `rd1`
(`rd1:2026-09-25:f5372ebb05`), on 4,540 rows: top-1 51.50%; served minus `rd1`
+0.0824 (+0.0206 to +0.1464), `sd_corr` minus `rd1` +0.0452 (+0.0015 to
+0.0899).

**The served engine changed during the window.** 140 distinct `engine_version`s
over the scored rows' served calls. From 17 Oct the served noon to post-peak
calls are WXPredict's frozen v3 (#409): 1,354 of the 4,656 scored rows have
`model_path` `wxpredict`, 3,302 `forecast`. The comparison is with the ladder
actually served, as fixed above.
