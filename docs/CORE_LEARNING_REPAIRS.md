# Core learning repairs

The probability calibration fit and remaining-day candidate benchmark are separate
from data collection. A completed collector or fitting job does not establish that
a new predictor was trained, passed independent evaluation, or entered service.

## Compatible calibration pools

Local training, validation, state keys and serving approvals retain the exact
model version, city and peak phase. Station-qualified AWC versions share only
the earlier training pool when the model weights and observation-contract version
match. Other model hashes and observation contracts remain excluded. Labels and
inputs retain the same receipt/time guards; later validation dates never enter
the training pool. States expose the compatible pool identity, rows and dates.

A separate station version for every city had unintentionally made each pool
single-city. The phase record keeps one call per city/date, so its bounded window
could not meet the declared pool-row minimum. Sharing a compatible prior restores
the intended city-shrinkage design without transferring a local approval between
stations or reducing sample, step, freshness or validation requirements.

## Supported candidate evaluation

The automatic remaining-day candidate requests noon, prepeak two-hour, prepeak
one-hour and postpeak one-hour first calls. It does not require the morning slot,
which the remaining-day input contract excludes before 09:00 local. Scoped first
calls define the evaluation denominator; missing calls inside that declared scope
still count against coverage. The generic dataset and assessment defaults retain
their existing scopes for other research callers.

Positive independent improvement must come from the noon and prepeak calls.
Postpeak performance remains a regression check and cannot supply the gain that
admits the candidate. Date separation, per-date city coverage, prediction-change
bounds, hashed original inputs, artifact checks and shadow-only acceptance remain
in place. No frozen model artifact or served prediction is changed by this repair.

## Acceptance

Regression checks exercise station-isolated local validation with a shared
compatible pool, refusal of other weights/contracts and late labels, bounded
same-night reruns, supported-horizon candidate admission, sparse-scope rejection,
and rejection of gains confined to postpeak. The fit/inference integration uses
synthetic inputs and tied predictors; it does not claim real forecast improvement.

The current-forecast research export uses the same supported remaining-day scope
under a new dataset policy version. Its earlier dataset default could not become
coverage-ready when the attached model excluded morning inputs. Missing eligible
first calls stay in the scoped denominator; historical exports and frozen input
vectors are not rewritten. Current-forecast features remain a research recipe,
not an unvalidated substitution into the frozen production model.
