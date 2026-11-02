# Shared frozen-forecast state research

`tools/wxpredict/forecast_state.py` tests a bounded, shared residual correction
of the probabilities originally served by the early forecast engine. This is
an offline development tool. It neither serves a replacement nor changes
historical calls, settlement, learning eligibility, strategy or schedules.
Calls from the separate WXPredict serving path are counted outside this
forecast calibrator rather than misdiagnosed as missing forecast inputs.

The experiment asks whether the original forecast's error state is useful:
the raw and corrected centres, width, observed maximum, observation age and
time remaining until the seasonal peak reference saved at that decision.
Observations above a centre do not themselves establish that the predicted
bracket is wrong or that the day has finished warming.

## Evidence contract

The local input consists of private daily `prediction_checkpoints` exports,
their `fact_checkpoint_outcome` rows and exact-ID band geometry. The exporter
must include all first calls in the requested city/date/checkpoint scope;
this local tool cannot verify omissions upstream. The original first call is
selected before exclusions. A later successful retry cannot replace it.

Every accepted call needs its original server freeze close to the decision,
a valid full probability ladder, forecast timestamp, station and city clock.
Same-day calls need a fresh observation from that local date. A later
observation or inconsistent identity raises an error. Missing historical
evidence is counted by reason, never manufactured or silently repaired.

Same-day calls less than an hour before their saved reference peak are
excluded. That reference is not a realized peak or proof the maximum had not
already occurred. Original morning calls missing the historical reference
remain explicitly `original_morning_without_peak_proof`. They have their own
scores and are never described as proven pre-peak predictions. Noon calls
cannot use this exception. Evening-before decisions use no same-day floor.

Unknown historical rule context remains unknown. Brackets are labels, not
invented exact station temperatures. This experiment cannot approve records
for production learning or independently authenticate an upstream receipt.

## One fixed shared model

The same global coefficients operate on all cities and both early horizons.
Its only prediction inputs are an explicit allowlist of original forecast
probabilities and forecast/observation state. Market quotes, settlement labels,
city IDs, later weather and the final peak do not enter its feature vector.
The underlying forecast already incorporates its original weather inputs;
this correction adds no new weather information and cannot repair a cold
upstream forecast by itself.

Coefficients start at zero, have fixed regularization and hard bounds. Each
bucket's residual logit is smoothly bounded by a fixed constant; structural
zero probabilities remain zero. Dates receive equal total training weight,
split equally among cities and then the city's slots. Repeated slots are not
treated as extra independent weather days. There is no city model selection
or hyperparameter search on evaluation dates.

Training labels must have been banked before the earliest evaluation decision,
including evening-before decisions on the previous local date. A configurable
gap in target dates alone is insufficient. Model artifacts retain the actual
fit time, source hash, input hash, recipe, bounds and dependency versions.
Retrospective fitting cannot create historical forward predictions.

## Local execution

Install the repository requirements plus NumPy and SciPy in a research
environment. Keep real inputs, coefficients, predictions and reports in a
private directory outside this public source repository.

```bash
python tools/wxpredict/forecast_state_study.py \
  --calls /private/research/calls-*.json \
  --bands /private/research/bands.json \
  --through "$EVIDENCE_CUTOFF" \
  --fit-last "$LAST_TRAINING_DATE" \
  --eval-first "$FIRST_EVALUATION_DATE" \
  --eval-last "$LAST_EVALUATION_DATE" \
  --out /private/research/new-development-run
```

Each calls file has `rows` of objects with `call`, `city`, `fact`,
`timing_reference`, `contract` and `rules_sha256`. The bands file has `rows`
containing `band_id`, `lo` and `hi`, with null open endpoints. Synthetic
fixtures in `tests/test_forecast_state.py` document the field shape.

The script refuses to overwrite output or write inside the source repository.
It emits a private dataset, model, development predictions and evaluation.
Results include exact-bucket hits, Brier and log loss against the unchanged
incumbent and the complete, non-crossed quotes captured at the same call.
City, horizon, error state, peak-evidence stratum and date remain separate.
Paired date-block uncertainty keeps correlated cities together. Original
recorded favourite choices survive tied baseline probabilities.

Development holds explicitly identify a horizon whose hit rate or probability
quality worsened; an aggregate gain cannot conceal an evening-before loss.
All artifacts retain `changes_serving=false` and
`eligible_for_promotion=false`. A successful development result still requires
separately frozen future predictions and independent validation before any
activation is considered.
