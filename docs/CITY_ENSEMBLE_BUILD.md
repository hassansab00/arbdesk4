# City ensemble and market-aware predictive build

This build connects source-weight learning, original prospective captures,
chronological evaluation and the Predictive page. It does not establish an
accuracy gain merely by deploying. Existing frozen calls, the registered
independent temperature trial, and production weight settings are preserved.

## Forecast inputs and city uniqueness

The existing day-ahead station path corrects each source's local bias and
averages the corrected sources equally. MOS adds station/history, source
contrasts, temperature, humidity/dew point, cloud, radiation, wind, rain,
pressure and seasonality. The intraday primary includes observations and
trends, running maxima, forecast peak timing, remaining-day weather, city
climatology and past-only forecast errors. These are recorded features, not
an exhaustive enumeration of every weather permutation or a claim that all
attributes have predictive value. Some early MOS source contrasts cover only
four named models; the ensemble mean/spread and new weighting cover all seven.

## Early horizons

Tick now makes a real day-ahead call at 09:00 on the preceding CITY LOCAL day,
in addition to the existing evening and morning calls. It is independent of
paper strategy configuration. The existing receipt trigger accepts exactly
that new slot and keeps all previous identity, timing, immutable-history,
paired-input and pre-outcome checks. Old day-ahead dashboard summaries remain
legacy evidence. A new original call owns its city/date cohort, including a
pending call: a favourable legacy summary cannot replace it.

Before freeze, `ensemble_early` records seven complete original corrected
source distributions, the incumbent, the saved market when complete and comparable, and a
bounded weighted candidate. Only compatible dated station rules and fresh
sources qualify. Missing inputs carry a reason and cannot become candidate
losses. Components use the actual saved ladder, unit, uncertainty and floor.
The candidate distribution is 80% incumbent, 10% learned source mixture and
10% saved market when comparable; without a complete comparable market it is
90% incumbent and 10% source mixture. These are declared priors, not optimized
on the latest day. Valid one-sided quotes use the existing Tick mark: the last
trade bounded by the quoted side. Missing/invalid quotes are never imputed.
Source learning retains its original packet even when the market is incomplete;
that call cannot enter the promotion-validation market comparison.

Nightly scoring reuses already-loaded calls/outcomes. City/station/unit/horizon
weights update from labels available before that night's cutoff, using a
14-day recency half-life and 80% shrinkage to uniform weights. This prevents
one last-cycle winner from dominating. Weights stay uniform for the first 10
independent dates, then move at most 0.02 per source per nightly update; retrying
the same cutoff cannot step twice. Sources are ranked by log loss on the
actual winning bucket, not an invented exact maximum or a rounded midpoint.
The next shadow call retains its exact working-weight version and comparison
ladders. Fitted weight values stay in the existing service-only calibration
store; public reports and nightly source snapshots retain summaries only.

Forward validation requires 20 older independent training dates and at least
10 later dates inside the fixed 14-day window. Validation requires a complete comparable saved market and uses ORIGINAL
candidate ladders frozen before outcomes, whose weights already had 20
training dates. Candidate hits and Brier may not regress against either the
incumbent or saved market, and paired-date log-loss gain must have a positive
lower bootstrap bound against BOTH. The state is explicitly held or validated
shadow. This research state never silently replaces the served forecast.

## Intraday challenger

`current-city-ensemble-v2` extends the existing recorded-input classifier with
past-only source skill, city/station/horizon weighting, local history and
saved market probability/entropy/disagreement features. Existing rich weather
features remain available for nonlinear interactions. Per-row training skill
features read only older target dates whose labels had arrived before that
row's decision. Same-day or future labels cannot improve their own features.
Compatible pooling shrinks sparse city estimates; source weights remain uniform
until the compatible pool has 10 independent dates. Absent forecasts remain
absent and surviving weights are renormalized.

The artifact contains aggregate source parameters plus the classifier, not
individual training calls. Reloading reproduces inference and rejects using
the artifact before its training evidence was available. The recipe and
preprocessor are source-hashed. Historical metadata and the independent
fixed trials are not rewritten.

The previous current-forecast global gate did not require beating the market.
Both global and city/horizon gates now compare hits, Brier and log loss with
the saved market as well as the incumbent. Forward calls remain a separate
fixed test. Production promotion remains a reviewed action after validation;
a historical development improvement cannot authorize it.

## Acceptance and operational limits

- Database rehearsal installs twice, proves every old checkpoint unchanged
  with EXCEPT ALL in both directions, tests the new slot and rechecks existing
  freeze/refusal and role contracts.
- Tests cover future labels, late receipts, incompatible models/contracts,
  duplicate first calls, incomplete sources/markets, source ranking, bounded
  weights, private-summary redaction, exact artifact reload and the market gate.
- The production migration precedes source rollout. Tick's existing deadline
  still bounds additional captures; missed slots are gaps, not backfills.
- One compact early-state query is shared with the existing adaptation reader;
  nightly fits reuse loaded original records and do not scan new DB histories.
- Deployment acceptance requires a subsequent normal Tick and learning run,
  then newly settled forward dates. No test suite proves stable accuracy.

## Measured development check

A historical development run trained on October 25–November 1 and examined
November 2–8 original intraday calls (four checkpoints, including post-peak).
Ninety-three training labels unavailable at the first validation decision were
excluded: 1,363 training calls on eight dates remained. On the same 1,248 later
calls, candidate hits were 840, incumbent 823 and market 940. Candidate/old/
market log loss was 0.7543/0.7862/0.6175 and Brier 0.4202/0.4379/0.3449.

This is inspected historical DEVELOPMENT, not untouched prospective evidence,
not a day-ahead hit rate, and not a promotion. It fails the required training
history and still loses to the market. It establishes a testable improvement
hypothesis over the incumbent; the new market gate must keep it held.

Live quote audit on 10 November: eight of ten latest early checkpoints lacked
two-sided quotes on every band. Such ordinary market structure must not discard
valid seven-source weather evidence. Research coverage reports source captures
and comparable market captures separately.
