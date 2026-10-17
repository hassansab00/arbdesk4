# Frozen city learning and validation

The first immutable city/day/checkpoint call is the evaluation record. New
server receipts reject completed city-local days, confirmed outcomes,
backdated decisions, future input timestamps, incorrect schedules, invalid
probabilities and replacement calls under another engine version. Existing
update/delete/truncate prohibitions remain. Old calls keep their original
fields and receive no invented receipt. Shadows need a recent original call.

WXPredict v3 retains the authorized horizon routing. Each served call freezes
its exact input key/hash/as-of and training cutoff, raw probabilities, paired
baseline, applied calibration version and context. The raw model uses the
city's weather, observation trends, forecast models, typical peak and seasonal
features. Its fixed weights are not silently retrained by this probability
calibrator. Independent shadow challengers retain their separate promotion
and holdout gates. Blinded experiments are not scored by this report.

The probability calibrator runs in the existing nightly candidate job. Each
city has before/near/after typical-peak cells. An annual cosine weight follows
the local season; city fits shrink towards the phase pool. The policy fixes
an identity prior, temperature bounds 0.8–1.5, ordinal tilt bounds ±0.2,
nightly steps at most 0.05 and 0.025 respectively, a 180-day history, minimum
20 older training dates, 10 later validation dates, and a phase pool of 200
rows on 20 dates. The later validation window is 14 days. These are declared
policy priors, not measured improvements.

The exact bounded candidate must improve log loss against raw and current
probabilities with positive date-bootstrap 90% lower bounds, without a Brier
or hit-rate regression. Unchanged fits can be revalidated. Reruns reuse the
same nightly anchor. A fit or its validation older than three days is not
served. The live raw model remains available when a cell is held or expires.
Only labels actually received before the fitting date qualify; a result
arriving after that cutoff cannot enter a historical rerun. Newly frozen
calls require the raw model training cutoff earlier than the target date.
Nightly JSON evidence and parameter versions are committed to the repository;
parameters used on a prediction remain in the immutable checkpoint.

Scoring runs after confirmation in the existing intraday/archive pipelines.
The public summary publishes every active city, with zeros/missing evidence
visible. Each horizon is separate. Overall compares dates with all six common
scheduled calls, excluding the explicit after-peak call; noon can itself be
after a city's typical peak. Day-ahead comparison uses calls before local
midnight. Legacy results are labelled separately from server-receipted ones.
Noon and morning calls are not counted as independent dates. The overall
interval resamples whole dates; individual-horizon intervals use Wilson 95%.

The ranking is winning-bucket hit rate, with correct/settled counts,
independent dates, pending calls, log loss, multiclass Brier, reliability bins
and confidence gap. Day-ahead history lacks a complete frozen distribution
in the read projection, so no Brier is fabricated for it. Paired raw/baseline
comparisons require the exact stored bucket ladder. Reviewed misses show
weather surprises, peak timing shifts and public-forecast MAE/bias. These
are observed associations, not proof of why the proprietary model missed,
and forecast accuracy is not evidence of trading returns.

A lack of new eligible samples must hold the identity prior; historical
post-hoc reconstructions are never presented as forward calibration evidence.
The page displays score freshness, per-cell fitting/validation dates and
held/active state. The existing ingest crash logger and workflow outcomes
surface failures. No schedule, allocation, bankroll or safety rail changes.
