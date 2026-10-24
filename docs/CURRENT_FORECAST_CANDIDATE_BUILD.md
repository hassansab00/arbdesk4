# Current-forecast candidate build

This completes a separate, automated research path for the current NWP context.
It uses the existing daily candidate job and the existing hourly shadow step.
It needs no manual result collection, new schedule, paid service or additional
database table. A successful job can mean "waiting for evidence"; it does not
mean a new model is serving or that forecast accuracy improved.

## Original inputs and actual labels

The primary input packet now includes independently hashed current hourly and
ensemble context, the contributing snapshots' receipts and the city timezone.
Their hashes are recorded on the first frozen call. The pre-existing weather
vector, its hash and production probabilities keep their existing definitions.
An optional research snapshot failure leaves the working prediction intact.

The new reader verifies the committed mirror's hash and row count, selects the
original city/date/checkpoint call, and reads its exact primary packet. It checks
the server's packet and freeze receipts, original weather/context hashes,
training cutoff, local day end, station/source/version identity, forecast age
and immutable venue winner. It never fills an earlier call from a later packet,
forecast or retrospective weather reconstruction. Missing calls stay in coverage
denominators. Streaming mirror filters retain whole-file count/hash checks.

## Fixed model and limits

`current-band-v1` learns a weather-only binary probability for each venue bucket,
then normalizes the complete ladder. The target is the actual winning bucket;
an open-ended bucket does not become an invented exact maximum temperature.
Every first call has equal total training weight regardless of ladder size.

Predictors include the frozen weather features, current hourly/ensemble values,
receipt age, city, station, source, season, local/peak timing, bucket geometry
in venue steps, and the incumbent's raw probability. Market prices, outcomes,
checkpoint IDs and provenance are excluded from predictors. The market remains
an independently reported comparator on the original first-call quotes.

The fixed initial correction assigns 10% weight to the current-forecast model
and 90% to the actually served incumbent. Impossible buckets respect the
qualifying observed floor. An independent 10-percentage-point maximum-change
gate remains in place. These are configured bounds, not measured optimal values.
The existing ensemble weather models remain active.

This recipe covers noon, prepeak two-hour, prepeak one-hour and postpeak one-hour
calls. Day-ahead, evening and morning retain their separate existing forecast
and station-correction paths. This build does not claim a new model for those
horizons or change their frozen evidence.

## Automatic fit and independent evaluation

The recorded-input protocol starts on 24 October 2026. The training window
contains at most 60 earlier dates and needs at least 20 complete training dates.
A complete date has at least 20 cities at every supported checkpoint. Training
and evaluation each need at least 80% first-call coverage. Current hourly context
must cover the complete local day, including daylight saving, and include at least
four complete ensemble models. Contributing snapshots must be at most eight
hours old. These thresholds are declared policy, not measured readiness.

One calendar day separates training from the next fixed fourteen evaluation
dates; evaluation ends two dates before the nightly run. The existing legacy
candidate's training policy is unchanged. This separate bucket classifier
learns original first calls rather than its reconstructed half-hour training
rows, so its sample gate is explicitly based on independent dates/cities.

Acceptance requires positive date-clustered prepeak log-loss improvement, no
checkpoint hit-rate/log-loss/Brier regression, complete per-date city coverage,
and the bounded probability change. Postpeak gains cannot supply admission.
Per-city/station/source/model/horizon evidence reports hit rates, proper scores,
training/validation dates and explicit local approval/hold reasons.

## Freeze and forward validation

An accepted artifact is uploaded once to repository Releases and read back with
its raw weight hash verified before registration. Its metadata, evaluation
report and registry are committed together. The new lineage and stable cache
pointer are separate from the original model and existing shadow experiments.

The hourly shadow runner uses that model only for recent original calls whose
exact source identity has enough training dates and whose model existed before
the call. It uses the frozen weather/current packet, not a later reconstruction.
It freezes `wx_current` rows through the existing server-side guard before the
local day ends or the venue resolves. Existing frozen calls are never updated.
The shadow reserves bounded time only when an accepted artifact exists.

The daily job scores the first fixed fourteen complete forward target dates,
starting after a two-date margin from artifact availability. Missing predictions
remain in coverage, and late/post-settlement receipts never qualify. Results
include the city/horizon ranking and local evidence. This is forward evidence;
the earlier held-out benchmark is separately identified as retrospective.

The output is `wait`, `reject`, `accept shadow`, `wait forward`, `forward rejected`
or `forward validated`, with immutable nightly reports under
`data/eval/wxpredict/current_forecast`. A validated forward result makes the
candidate ready for a separate reviewed production-promotion decision. This
pipeline does not silently promote it, alter allocation or claim profitability.

## Release acceptance

The regression suite exercises real LightGBM fit/inference on synthetic calls,
open-ended labels, context tampering, receipt/label timing, first-call identity,
source-specific training counts, full coverage denominators, publication failure,
immutable lineage, and forward rejection of late predictions. Synthetic fixtures
verify mechanics; their scores are not real-weather accuracy evidence.

Forward-rejected challengers are retired without deleting their artifacts or
evidence; the next daily run can fit the fixed recipe on newer eligible data.
Forward-validated challengers remain available for the reviewed promotion
decision. Reports also distinguish repaired misses from broken hits, show when
the current forecast point landed in the actual winner, and retain measured
feature gains. These diagnostics describe errors; they do not establish a
causal explanation for a weather event.

Hosted CI, deployed capture and the first production readiness report remain the
release checks until they have actually completed.
