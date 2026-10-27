# City manual-review guide

The city popup reads the existing anonymous `v_city_prediction_validation`
view for that city's served checkpoints. A single bounded read on opening
supplies the guide. It creates no endpoint, permission, database object,
schedule, model update or trade. Source predictions remain immutable.

A descriptive timing reference compares available pre-peak named checkpoints
on the same earlier target dates in the page's original-call history, within
the last 30 target dates. Reports contain only seven recent score summaries;
those summaries cannot supply the ten-date comparison. At least ten
common dates and two checkpoints are required. Ties remain ties; duplicate,
missing, stale, future-dated and non-served reports do not become evidence.
Explicit post-peak calls never enter the reference. Scheduled records may
include legacy or late-information calls, so receipt-verified eligibility
before the saved expected peak is reported separately on those same dates.
Insufficient timing proof leaves the entry rule unestablished. Selecting the
largest historical hit rate is not a later-date validation or a profit test.
Historical served versions and recent weather do not establish seasonal skill.

Selected-moment temperature errors use unique settled earlier dates, never
today's pending call or future records. Positive actual-minus-predicted error
means a cold tendency; the guide never applies that average as a correction.
Weather surprises are associations among reviewed misses, with denominators,
not causal explanations. Review clocks and the monthly peak use the city's
calendar and timezone. The monthly peak is not today's known final maximum.

Current readiness, missing or stale forecast/market marks, unavailable depth,
expired observation dates and an out-of-date live pick can produce `Wait`.
Otherwise the state is `Review only`. Stored marks are not executable asks;
actual buy price, spread, market-specific fees, size and station/rules require
checking together. A city's winner hit rate is never converted to a share's
fair price, a limit price or a return promise. No order or stake is generated.

Synthetic tests exercise matched dates, no lookahead, ties, ambiguous records,
receipt exclusions, source/read failures, bias direction, city clocks, stale
quotes and the rendered guide. Production weights, calibration gates and
original-call selection are unchanged.

## Compact card indicator

Each city card shows the strongest recorded checkpoint, ties, correct/total and
hit rate on the same matched dates used by the popup guide. It compares the six
scheduled forecast checkpoints, excludes the explicit after-peak checkpoint and
does not use today's outcome. A missing or stale report is not a recommendation.
The indicator flags limited receipt-backed timing proof and always states that
entry timing has not been validated. This is historical winner accuracy, not a
current entry signal or expected return. Open the popup for city-local clocks,
timing coverage and execution checks.

The cards make one bounded anonymous read for the displayed city set. JSON-field
projection fetches only date scores and timing receipts from the existing report
view, not full weather, fitting or model-selection reports. There is no new
polling loop, scheduled workflow, database write or model change. Reload refreshes
the timing reference together with the other card data.
