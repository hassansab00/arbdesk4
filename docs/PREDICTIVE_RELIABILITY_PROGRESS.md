# Predictive reliability — implementation follow-up

Authorized after the city/horizon audit. No original prediction, settlement,
archive or blinded experiment is rewritten. Existing schedules, portfolio
allocation and risk rails remain unchanged.

- The unpostprocessed same-day public forecast width cannot be narrower than the nearest
  verified advance-lead error prior. The latest same-day revision's errors
  do not establish morning uncertainty. This is a declared conservative
  prior, not a new validated city fit. An applied postprocessing fit keeps
  the centre/width pair its validation tested. The prior never shifts the centre or changes
  a direct WXPredict distribution. A validated trajectory retains its own
  centre/width.
- New checkpoints preserve the public input's model, source, run, raw
  maximum, priced centre/width, skill lead and uncertainty provenance in
  their existing JSON context. No historical context is reconstructed.
- Future-dated station reports fail freshness. Malformed primary inputs
  fall back explicitly instead of being accepted as valid weather features.
- City calibration and the city/horizon selector expose available/required
  training, validation and compatible pooled evidence. Their existing
  chronological, contract and weight-identity gates are not weakened.
- Accuracy reports compare the model and the saved complete market on the
  same calls, with proper scores. Reviewed-high reporting adds explicit
  one-hour and two-hour lead subsets. Unknown timing stays unknown. Strict
  reports do not borrow full-cohort market scores.
- The existing nightly remaining-day candidate planner fits the serving
  recipe's five seeds, at most weekly, only after independent input coverage
  is ready. Proper-score and well-sampled city/horizon regressions reject a
  new candidate. Weight assets use the existing hash-checked Release path.
- An accepted challenger stays fixed for its first complete forward window.
  Only matching original shadow predictions with timely server receipts,
  exact ladders/stations and later settled labels enter that gate. It also
  compares proper scores with the saved market. New candidates remain shadow;
  this gate does not install unvalidated production weights.

Local full-suite, database, UI and deployed acceptance are recorded in the
release PR. No accuracy gain is claimed from implementation tests. Effective
city calibration and model promotion still require qualifying forward data.
