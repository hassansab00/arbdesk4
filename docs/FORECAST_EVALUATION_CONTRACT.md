# Forecast evaluation contract, `fec-v1`

**Frozen 30 Sep 2026, before any challenger in it was scored.** It answers one
question: does a candidate forecast of a city's daily maximum, and of the
settlement bucket that maximum falls in, beat the incumbent on days it never saw?
It does not decide whether anything trades (section 8).

A result names this version. Changing a definition below makes `fec-v2`;
results under `fec-v1` stay reported under it and are never re-scored silently.
It builds on `s10-contract-v1` (`docs/S10_MAX_TEMP_WINNER.md`) for the
same-day lane and does not replace it. Where the two differ, this file says why.

## 1. Two lanes, never pooled

| lane | question | decision time | incumbent today |
|---|---|---|---|
| **DA** day-ahead | the day's maximum and bucket, before the day begins | the last pricing before the city's local midnight (`v_city_hit_history.called_at`) | `band_probabilities` as priced (the engine) |
| **SD** same-day | the FINAL maximum and bucket, from the day so far | local `H:MM` for `H` in 7..17, where `MM` is the local minute of the hourly tick (`:36` UTC; `:06` in a half-hour zone) | S10, `rd1` (`scripts/remaining_day.py`, `data/models/remaining_day/current.json`) |

Every table reports each lane on its own, SD by decision hour.
Repeated intraday forecasts of one city-day are never counted as independent
days. The unit of independence is the **date** (section 5).

## 2. The target

- **Station and city.** The station is the one the venue's event names (`station_icao` in
  `data/training/market_history/events.csv.gz`, or the market's rules live).
  A city-day is eligible only if that station is the one whose readings the
  candidate used (`data/eval/fec_v1/stations.json`, from `cities.icao`).
  Example: Paris settled on LFPG until April 2026, while the readings are LFPB, so those days are excluded.
- **Local date and timezone.** The city's IANA zone, with daylight saving. A day is a
  wall-clock day.
- **Units, buckets and rounding.** The market's ladder, half-open `[band_lo, band_hi)` in the
  market's unit, open tails included. This is `v_canonical_bands`' convention, and
  `market_history/bands.csv.gz` uses it. A °C bucket is one degree wide and a °F bucket two
  (`band_hi - band_lo`). A candidate's distribution in °C is read on the ladder by
  `probability_engine.unit_edge_c`, the engine's own conversion. The floor atom and the
  one-bucket measurement layer are `remaining_day.ladder_probabilities` (P3.1).
- **Label.**
  - **Scoring:** the venue's winner. Only that bucket is scored.
  - **Training:** the station's whole-day maximum (`labels_whole_repaired.json.gz`,
    `derived_city_day_features`, whole days only) where no venue answer exists. A label
    repair makes a new label version, and the original frozen predictions are never rewritten.

## 3. What a prediction may know

- **Observations:** readings RECEIVED by the decision time.
  - **Live:** `weather_observations.observed_at <= decided_at`. `observed_at` is set once, at
    first insert (ignore-duplicates upsert).
  - **History:** no receipt time exists before 29 Aug 2026 (IEM) and 5 Sep (NWS). A historical
    run uses valid time `<= decision - 10 min` and is labelled **approximate as-of**.
  - **Why 10 min:** on the 673 live S10 decisions of 27-30 Sep, readings under 10 min old at
    decision had been received 7.7% of the time, readings 10-20 min old 87.9%, and older ones
    92-100% (`weather_observations`, query in `docs/CHALLENGER_A_PREREG.md`).
  - **Sensitivity:** every such result is also reported at 0 and 20 min.
  - **Not allowed:** an observation timestamp before the decision proves nothing about receipt.
    Neither are finalized daily observations, hindsight peak times, or later forecast revisions.
- **Forecasts:** a run issued before the decision.
  - The SD incumbent uses Open-Meteo best_match `_previous_day1` all day.
  - For Previous Runs the publication time is assumed, not recorded (`s10-contract-v1` §3),
    and every result relying on it says so.
- **Parameters:** fitted only on target dates before the first test date (an expanding window,
  refitted monthly). Nothing is evaluated on the dates it was fitted on (Rule 11).

## 4. Periods

- **Development** (SD, where the venue record exists): test months January to July 2026.
  Each month is fitted on every labelled day before it.
- **Untouched evaluation** (SD): **1 Aug to 25 Sep 2026**, fitted on every day before 1 Aug.
  - It is run once per pre-registered candidate, after its design and acceptance rule are
    written down.
  - The incumbent's FORM was chosen on months that include this window (P7.2, 26 Sep). That
    selection favours the incumbent, and it is stated beside every result.
- **Forward shadow:** every live decision after a candidate is deployed in shadow, with its
  own version, scored as settlements arrive. This is the only fully as-of evidence.

## 5. Metrics

- **Primary:** mean full-ladder **log loss** of the venue winner's bucket, with probability
  floored at 1e-6.
- **Also reported:**
  - exact **top-1** accuracy (the highest-probability bucket), with a Wilson 95% interval;
  - multiclass **Brier** over the ladder;
  - the median's **MAE** and signed **bias** against the station label, in °C;
  - **80% interval coverage and width**, in °C.

  An 80% interval says nothing about the exact-bucket hit rate. The two are reported side by
  side, never as one.
- **Uncertainty:** a **date-clustered bootstrap**. Dates are resampled with replacement, and
  every city and hour of a date moves together, because cities share weather and a date's
  errors are correlated. 1,000 resamples, seed 11; 90% and 95% intervals of the paired gain.
- **Counts, always:** rows, distinct dates, cities, and the exclusions by reason.
- **Breakdowns:** by decision hour (SD) and by city, with n. A city is never chosen as a winner
  after looking at its test result.

## 6. Comparators, on identical rows

1. **Raw provider forecast** (DA only): the public forecast bucketed with the venue's rule.
2. **Current corrected forecast** (DA only): the engine's priced centre and ladder.
3. **Current S10** (SD): `rd1`, refitted at the same cutoffs as the candidate.
   - Its live artifact (`current.json`, fitted through 25 Sep) is scored only on forward days.
4. **The market**, where the ladder is complete, synchronized and fresh:
   - each bucket takes the first hourly price at or after the decision, within 60 min;
   - a ladder with any bucket missing is not compared;
   - raw prices must sum to 0.9-1.1 before normalisation.

   The market is a comparator for information, never a label.

A candidate and a comparator are scored only on rows where both predict, and
the rows each one could not predict are counted.

## 7. Promotion decision 1: a better FORECAST

A candidate replaces the incumbent's published forecast only if, on the untouched window:

- the mean log-loss gain (incumbent minus candidate), pooled over the lane's decision hours,
  has a **90% date-clustered interval above 0**, over **at least 20 dates**;
- no decision hour shows a regression whose 90% interval lies wholly below 0;
- top-1 accuracy is not lower than the incumbent's beyond the Wilson interval;
- and 80% coverage stays within 0.75-0.88.

Otherwise the verdict is **rejected** (a significant regression), **insufficient evidence**
(the interval spans 0), or **accepted in development only** (never shown on the untouched
window). The verdict is written down. The rule is not moved afterwards to rescue a candidate.

## 8. Promotion decision 2: influence on TRADING (separate)

A better forecast earns no trading weight by itself. Weight is decided by the
market-anchor gate (P5.3, `market_anchor.fit`), from forward market-relative
evidence after costs, with Rule 6 (capital is Hassan's). Weather forecasts are
recorded and scored whatever weight trading gives them: a zero weight in a
decision never stops a forecast being recorded.

## 9. Reproduce

`tools/fec_same_day.py` (SD lane) writes its report, the per-row scores and
the inputs' checksums; running it again on the committed inputs gives the same
bytes. `tools/market_vs_model.py` and `v_city_hit_history` hold the DA lane's
existing measurements. The DA lane's harness under this contract is not built
yet (section 1's incumbent is scored daily on the page).
