# City forecast quality and bounded adaptation

This change closes three validation/freshness gaps and adds prospective early
correction learning to the existing prediction pipeline. It does not establish
an improvement in production accuracy; that requires new, settled forward calls.

- Station combinations must have fresh, ordered input-run timestamps, at least
  four sources and a finite centre. Rewriting the combination cannot refresh old
  underlying weather. Postprocess coefficients expire after three days and must
  satisfy the fitter's finite bias and width bounds.
- An unfittable nested shrinkage fold uses the fixed conservative prior
  `(40, 120)`, never the winner selected with the fold's later outcomes.
- A city/peak-phase calibration that fails fresh validation falls back to the
  identity immediately rather than retaining its last usable parameters.

## Early city correction strength

The engine freezes eleven candidate ladders, with correction strength from zero
(raw centre) to one (the current corrected centre) in increments of 0.1. Each
candidate uses the same original width, probability calibration and observation
floor. Both endpoint forecasts and the selected strength are saved with the
original call; old forecasts are never rebuilt. Promoted-model and trajectory
centres remain outside this comparison.

Evening-before and morning cells are distinct, on each city's clock, including
DST. The recipe identity separates the source model and lead, correction path,
width policy, settlement contract, station and unit. Unknown historical recipes
remain diagnostic evidence only. Morning learning also needs the original saved
peak evidence to place receipt at least an hour before the expected peak.

The prior strength is one. Learning uses a trailing 90-day record, with the
latest 14 days held for validation. A city/horizon/recipe needs at least 20 older
training dates and 10 later validation dates. Training is seasonally weighted
and shrunk toward the prior with 30 pseudodates. Strength can move by at most
0.1 per nightly fit; a rerun uses that night's original anchor. Every candidate
must improve held-out log loss against both prior and incumbent with a positive
90% date-bootstrap lower bound, and must not reduce hit rate or worsen Brier
score. An unchanged incumbent must revalidate; rejection restores the prior.
Expired or incompatible state cannot price a call.

This runs inside the existing daily validation job, using the existing private
calibration table and daily committed evidence artifact. The original live
30-day database reads are unchanged. Older training calls come from existing
repository exports after hash, row-count and coverage verification. A missing
mirror cannot provide approval evidence. Intraday score refreshes do not fit.
The UI exposes the strength, version, readiness counts and reason per city.

## Verification

Regression coverage includes causal label/receipt ordering, first-call selection,
source/contract isolation, city-local DST, unseen-date rejection, same-night step
limits, immediate fallback, raw/calibrated price coherence, floored morning
ladders and verified offload reads. Run the full Python, database, TypeScript,
route and production-build checks before publishing. Production acceptance must
also verify a new ordinary tick and the unchanged historical frozen receipts.
