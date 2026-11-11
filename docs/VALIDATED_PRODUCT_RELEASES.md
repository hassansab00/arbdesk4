# Validated product releases

This implements the production handoff following the ensemble learning input
repair (#495). Scheduled pricing replaces `band_probabilities` before the edge
stage. Tick replaces each horizon's probability result before native freeze,
variant comparison and current-card publication. An approved release changes
these actual rows, rather than adding only a research label.

## Evidence and private releases

The adaptive seven-source shadow experiment remains independent. Once a cell
has twenty original settled training dates, a separate production trial pins
its exact weights and registers fourteen future local target dates with a
full-day margin. It freezes a separate prospective candidate alongside the
incumbent and same-band market probabilities. Later refits cannot inherit this
trial's pass or reset its window. Missing calls remain in its denominator.

A release requires at least ten independent later dates, ninety percent of
the fixed window, sixty percent bucket hits, strictly more bucket hits and
lower Brier/log loss than both saved references, and positive paired date
bootstrap lower bounds. These are acceptance requirements, not measured gains.
Production uses the exact pinned weights and six-decimal candidate recipe.
Completed trial registrations and verdicts are retained in a private immutable
ledger. The next candidate receives a new future window after completion;
pending authoritative labels keep the original trial pending. Approved
production artifacts remain independent of the next research candidate.

The current-weather model additionally requires its immutable, hashed accepted
development report and fixed original forward trial for the exact artifact,
recipe and city/station/source/horizon. Only supported pre-peak cells qualify.
Live inference reads the original prepared feature packet and its actual
server receipt; it does not manufacture a historical forecast or freeze.

Private append-only release receipts pin payload, proof and serving-code
hashes. Reads are bounded to sixty cities and three seconds. No approval,
revocation, expiry, changed recipe, mismatched station/rule/geometry, missing
market/input evidence or exhausted deadline keeps the incumbent. Release
registration expires after fourteen days; retry cannot extend it or undo a
rollback. `revoke_predictive_product(receipt_id, reason)` appends a revocation.
No historical prediction, observation, outcome or evidence row is edited.

The probability version and native checkpoint record the actual release and
retain the original incumbent distribution. Later research reads that retained
benchmark rather than comparing a model with its own output. Cards label an
applied release; the predictive page separately shows sanitized approval
receipts and explains that approval does not prove a release was applied.
Weights, model artifacts and detailed research remain private.
New current-weather artifacts use service-only hash-verified database storage,
with a 512 KiB compressed transfer cap and shared per-run model cache. They
are never uploaded to public repository Releases. Oversized artifacts hold
the candidate rather than increasing the database plan.

## Verification and remaining gate

Regression tests cover actual replacement rows, native freeze and current-card
ladder agreement, retained benchmark lineage, source input/family guards,
private grants, invalid proofs, idempotent retries, revocation and expiry.
Full-suite results and deployment verification are recorded in the PR.

Deploying this code is not evidence of improved hit rate. New releases remain
held until their original chronological trials pass. Confirm operation on
later scheduled runs and newly settled forward calls before claiming repair
or a production accuracy improvement. Paper-only settings are unchanged; no
new scheduled job or paid database plan is introduced.

## Native freeze compatibility

The native trigger validates the current private release receipt, expiry and
revocation, station/unit/source identity and exact served ladder. It preserves
the primary input/selector lineage and checks that selector against the
retained pre-product incumbent and its original label. Legacy calls keep their
original checks. Database regressions insert raw-selected, baseline-selected
and seven-source product rows through the actual trigger as service_role,
then reject wrong receipts, revoked/expired releases, changed inputs, altered
incumbents and post-settlement attempts. Installing the guard leaves existing
frozen records unchanged.

A served source release keeps a compact serving receipt separately from the
next independently pinned source trial. New trial calls retain the actual
served production distribution as their comparison benchmark. Current-model
records likewise separate the causal pre-product feature prior from the actual
served incumbent used for scoring. A challenger cannot qualify by beating an
older weather prior while losing to the current production model.
