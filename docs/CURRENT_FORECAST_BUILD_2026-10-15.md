# Forecast input follow-up — 15 October 2026

The nightly hourly record stopped at UTC midnight. American local dates extend
past that boundary. The recorder now asks through the latest local midnight of
the preceding calendar date across active cities, with daylight saving. Daily
aggregates keep their original date window. Saved hourly answers are never
rewritten, and refetch differences remain visible.

The same live forecast requests now also ask for unsuffixed current values.
They are recorded separately as hourly0 and models0, with each response's actual
receipt time. The Previous Runs API does not return current values under a
_previous_day0 key. No new request cadence or workflow is added.
Each response is kept as one complete snapshot per city and kind, rather than
one database/index row per forecast hour. All returned hours and earlier
snapshots remain available; the same archive safeguards apply.

Current forecasts are not historical model initialisation records. Receipt
times establish when the application obtained them; no earlier availability is
invented. The existing lead-one/lead-two publication assumptions remain.

Each exact first-decision input record can additionally carry current forecast
context, with a separate integrity hash. Context reads arrivals no later than
the decision, requires a complete local forecast day before reporting its
maximum, records model coverage, and excludes future corrections. Frozen
feature vectors, model weights, serving and allocation stay unchanged. The
automatic candidate recipe still uses its existing feature vector. A model
using current context requires a separate evaluated recipe.

A fixed forecast-error target was tested retrospectively: train through
30 August, leave 31 August out, evaluate September. These dates were inspected
previously, so this is development evidence. Its morning result was 1,493 hits
of 4,221 versus the incumbent recipe's 1,707 and the market's 2,261. Mean log
loss worsened from 1.5280344769799394 to 2.1056014004934704. The challenger was
rejected and is not connected to serving, the registry or automatic refits.
The fixed experiment, tests and aggregate result are retained for reproducibility.

The historical training table still approximates publication times, and the
Lucknow live and historical forecast grids differ. These repairs do not claim
complete historical/live parity, an accuracy improvement, or profitable fills.
Archive/reclaim acceptance and independent candidate evaluation still need
their scheduled runs and settled data.

Local validation passed: 4,118 Python tests, 9 skipped (166.49 seconds),
90 focused tests, and the complete database contracts. A read-only live API
probe for NYC returned 68 current hourly rows and 68 current model rows,
alongside both existing leads, with no reported errors. The probe wrote no
database rows. Snapshot tests verify identical context before and after
compaction, receipt-time exclusion and unchanged frozen inputs.

Hassan approved public publication, merge and deployment after CI passes.
CI, production migration application and the first production capture with
these changes remain pending.
