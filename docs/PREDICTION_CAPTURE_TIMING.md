# Prediction capture timing

The scheduled checkpoint name does not establish when its prediction was
actually available. The writer accepts a server freeze during the existing
75-minute window after the city-local schedule. A nominal before-peak call
can therefore be captured after the expected peak; a noon call can also
follow an early-peaking city's expected maximum.

## Reporting change

`predictive_validation.py` now publishes an additional `capture_timing`
object inside each existing city/horizon/predictor report. The complete
scheduled scores remain available. A separate, explicitly selected
**Before expected peak · verified** view uses the actual server freeze:

- A call without `frozen_at` is excluded as missing server evidence. An old
  application timestamp never becomes a manufactured server receipt.
- A verified early call frozen before the target local day starts qualifies.
- An explicit `postpeak_1h` call is excluded.
- For a same-day call, the cutoff is the earliest valid estimate saved in
  `clim_peak_hour`, `fch_peak_local_hour`, or the original peak-relative
  checkpoint schedule. No current forecast or mutable peak table is used to
  invent missing timing evidence. A call at or after that cutoff is excluded.
- A same-day call without a saved estimate is excluded as unknown.
- Overall retains the scheduled report's common dates and additionally
  requires every participating horizon on a date to qualify. A partially
  qualified date cannot improve its average by dropping that date's late call.

The report includes the full denominator, exclusions by reason, strict
scores and recent original captures. The capture table shows schedule,
server freeze, delay, cutoff and result on the city's clock. The existing
hit/miss history also displays capture delay. Missing strict reports never
fall back to complete-cohort scores. Complete-cohort diagnostics are not
presented as explanations of the filtered subset.

## Interpretation and boundaries

**Expected peak is an estimate, not proof the day's final maximum was still
ahead.** This filter is a descriptive timing audit, not an independently
validated increase in forecasting skill. Exclusion counts and per-horizon
results must accompany its hit rate. Coverage can differ across cities.
Day-ahead and evening-before remain useful advance-forecast comparisons.

Prediction/outcome records, writer windows, frozen input packets, model
weights, fitting and promotion gates, blinded studies, trading settings and
workflow cadence are unchanged. Only existing derived reports are refreshed
by the existing scoring entry point. No database migration is required.

## Verification

Targeted tests cover late nominal pre-peak captures, equality at the cutoff,
early city peaks, missing receipts and saved estimates, city-local daylight
saving, original-miss preservation, strict common-date coverage, filtered
UI totals and missing-report handling. Release validation and deployed
acceptance are recorded in the pull request.
