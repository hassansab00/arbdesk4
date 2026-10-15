# Current forecast research dataset — 15 October 2026

Current forecasts are now captured separately, but recording them does not
make them predictors in the existing candidate recipe. This extension makes
the exact first-call context available to explicit research exports. It does
not fit a model, change automatic refits, replace a frozen model or trade.

`arrival_dataset.build(..., include_current=True)` adds the current context
only after verifying its independent hash. Missing context stays missing;
neither later captures nor a weather replay fills an earlier call. The
existing exact contract, first-call market, receipt, history-vintage and
settled-label checks still apply. The default export is unchanged.

`tools/wxpredict/current_dataset.py` writes a separate deterministic CSV gzip
and a JSON coverage manifest. It preserves full numeric precision, the
checkpoint identifier, and both input hashes. The labels table supplies only
settled labels. The manifest records the labels-table and output hashes.
Missing labels, books and on-time vectors remain in the first-call coverage
denominator. Every requested checkpoint-date is shown, including empty ones.

The coverage gate reuses the existing candidate policy: at least 20 cities
per checkpoint-date and 80% of first calls with complete current hourly
context. These are policy bounds, not measured coverage. Readiness means
input coverage only; it is not approval to fit, serve or promote a model.

The separate `current_recipe.features` adapter uses an explicit whitelist of
the frozen weather columns and recorded current context, then expresses
forecast gaps and model spread in the venue's steps. It excludes outcomes,
market predictors, checkpoint identifiers, provenance and unknown future
columns. No model uses it automatically.

Example, once the committed mirror and settled labels cover the window:

```bash
python tools/wxpredict/current_dataset.py \
  --from 2026-10-16 --to 2026-10-29 --through 2026-10-31T00:00:00Z \
  --labels-table /path/to/settled-labels.csv.gz --out /path/to/current-context.csv.gz
```

This is an example window, not a claim that the future inputs exist. Before
fitting, freeze the training and evaluation dates, require enough recorded
training dates, leave the calendar-day gap, and register one recipe. Compare
against the frozen incumbent and market on the same calls, retain checkpoint
regression and coverage gates, and judge any promotion on independent dates.
There is no accuracy or profitability result from this dataset work.

Local validation passed: 4,122 Python tests, 9 skipped (176.11 seconds),
the complete database contracts, and 17 focused tests. Synthetic regressions
verify first-call identity, tamper refusal, missing and incomplete context,
coverage denominators, stable export bytes, full precision, C/F conversion,
and predictor exclusion of outcomes and provenance. No live training export
or current-context model fit is claimed before recorded coverage exists.
