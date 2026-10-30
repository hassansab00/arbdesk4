# Independent evaluation of the shared predictive model

## Scope and handoff

This package is the independent evaluation workstream authorized on October
31, 2026. It adds only `tools/predictive_evaluation/`, its dedicated test file,
and this document. It does not import production pricing/training modules,
call a database/API, read service credentials, add a schedule, train a model,
select a serving model, allocate capital, or alter any prediction or outcome.

The parallel workstream owns deadlines/retries, database reads, inference
features, archive recovery, training eligibility, fitting challengers and
learning status. Its exporter can supply the packet below once those repairs
provide the required evidence. This evaluator consumes that evidence; it does
not implement competing archive or learning-eligibility rules.

The candidate is **one shared model identity, version and artifact SHA256**
across the declared cities and slots. Weather, city, station, season and lead
can be inputs to that model. Per-city model selection is not implemented.
An approved experiment can compare several fixed baselines, but it cannot
choose a separate serving winner for each city.

Progress is recorded here rather than editing the shared `PLAN_PROGRESS.md`
while the other workstream is active. This is an offline evaluation deliverable,
not completion of the other workstream or proof of improved live accuracy.

## Running it

Python 3.11 standard library is sufficient. No new runtime dependencies.
Run from the repository root; output directories must be new. The command
never overwrites existing data or writes back to its inputs.

```bash
python -m tools.predictive_evaluation \
  --output-dir /tmp/shared-model-review \
  evaluate /path/to/original-evaluation-packet.json
```

The directory contains `report.json` (full evidence and exclusions) and
`report.md` (a readable summary). `--require-ready`, before the subcommand,
returns exit code 2 for `hold` or `not_assessable`; without it, successfully
generating a held report returns zero. Malformed input or an existing output
directory returns 2. A readiness result never changes production.

```bash
python -m tools.predictive_evaluation \
  --output-dir /tmp/shared-model-example \
  evaluate tools/predictive_evaluation/example.json
```

The example is **synthetic**, deliberately too short, and permanently held.
Its invented receipts and outcomes demonstrate the file format only. It is
not historical performance, a fitted candidate, or validation evidence.

## Input contract for the training/pipeline workstream

Use the complete `example.json` as a structural example. The top-level keys:

| Key | Meaning |
|---|---|
| `schema` | Exactly `arbdesk.predictive-evaluation.v1` |
| `synthetic` | Must be true for generated demonstration data; always holds readiness |
| `spec` | Evaluation window, cohort, baselines and explicitly declared thresholds |
| `models` | One candidate, fixed incumbent/weather/market comparators |
| `plan` | Registered expected city/event/slot cases and capture times |
| `forecasts` | Original full bucket distributions with timing/provenance |
| `outcomes` | One authoritative confirmed outcome per event |

### Specification

`registered_at` freezes the evaluation specification and plan before the first
scheduled validation call. `evaluation_as_of` is an explicit availability
cutoff, making reruns reproducible. `from_date` and `to_date` are local target
dates. The candidate artifact must have been fitted by registration; its
`trained_through` must be strictly before the evaluation window.

Declare `candidate_id`, unique `baseline_ids` and the complete `expected_cities`.
Fixed incumbent and market baselines are mandatory; a recorded weather
distribution can be an additional comparator. Declare `expected_checkpoints`
for the complete slot cohort in every city and `primary_checkpoints` for the
advance slots whose improvement can qualify the candidate. An explicitly
postpeak slot cannot be primary. For a full early-forecast test, register
day-ahead, evening-before and morning as primary rather than letting later
observations qualify the model. These are planned slots, not hindsight filters
on the eventual peak. All readiness claims apply only to the declared cohort.
Specify each planned event/slot instead of inferring the denominator from
whichever forecasts survived. Every city must appear in the plan. Each plan
row has `event_id`, `city_key`, `target_date`, `checkpoint`, `scheduled_at`.
Times are offset-aware ISO instants; city-local schedules must be resolved
using the historical timezone/DST before exporting. A single city/event/slot
is one case even if several engine versions later wrote revisions.

The exporter explicitly supplies these **policy assumptions**, not measured
facts or claimed optimal settings:

- `minimum_dates`, `minimum_city_dates`: required distinct target dates;
- `minimum_pair_coverage`: required paired proportion of the registered plan;
- `block_days`, `bootstrap_samples`, `seed`, `confidence`: uncertainty policy;
- `max_city_hit_regression`: maximum tolerated city hit-rate decline;
- `pair_time_tolerance_seconds`: maximum difference in prediction cutoffs;
- `max_market_quote_age_seconds`: maximum age of a market snapshot;
- `capture_grace_minutes`: permissible delay after the registered schedule.

Choose and register these before looking at validation outcomes. There is no
promise that any fixed number of days establishes skill. The code requires
at least two block lengths in the declared minimum date count and at least
200 resamples for the requested bootstrap. Those are implementation floors,
not recommendations or proof of adequate statistical power.

### Candidate and comparator identities

Each model supplies `model_id`, `role` and `version`. The single candidate
also supplies `scope: "global"`, `artifact_sha256`, `fit_at`, `trained_through`.
Each candidate forecast repeats that same artifact SHA256 and version. A
different artifact or version for one city fails its evidence check.

Comparators have role `incumbent`, `weather` or `market`. A weather comparator
must be an independently recorded original forecast distribution. This tool
will not turn a point forecast into guessed probabilities. A market comparator
must carry the original full ladder, price-snapshot time and receipt evidence;
it is not a reconstructed current quote or a promise of executable fill.

### Original forecasts

Every forecast requires:

- Identity: `forecast_id`, `model_id`, `model_version`, `event_id`, `city_key`,
  `target_date`, `checkpoint`; candidate rows also require `artifact_sha256`.
- Target: `station`, `timezone` (IANA name), `rule_hash` (lowercase SHA256).
- Timing: `scheduled_at`, `issued_at`, `frozen_at`, `input_as_of`,
  `input_available_at`, all offset-aware ISO strings.
- Evidence: `evidence: "original"`, `receipt_id`, `input_sha256`.
- Prediction: `probabilities` keyed by every canonical band ID,
  `top_band_id` identifying the actual saved choice. Probability mass must
  sum to one within 1e-6; incomplete ladders are not renormalized.
- Optional `predicted_max_c` for temperature error. Market rows also require
  `market_quote_at`.

`input_available_at <= input_as_of <= issued_at <= frozen_at` is mandatory.
The original is frozen within its capture window, before local target-day
end and before venue confirmation. Candidate fitting precedes its input
cutoff. Market quotes cannot postdate receipt or exceed the specified age.

If several forecasts share a model/event/slot, the earliest `issued_at` is
chosen **before** eligibility filtering. A later successful, correct or
better-documented revision cannot replace an earlier original. Duplicate
forecast IDs are rejected. Malformed chronology cannot be used to pick the
more favorable row.

**The evaluator checks supplied assertions. It cannot independently prove
that a receipt, artifact or hash actually existed at the declared time.** The
parallel exporter must retain verifiable upstream evidence; independent review
must inspect it before accepting the experiment. A generated hash alone is
not freeze proof. No existing data is relabeled as prospectively verified.

### Confirmed outcomes and peak diagnostics

Each event has one exported authoritative outcome with `event_id`, `city_key`,
`target_date`, `station`, `timezone`, `rule_hash`, ordered `band_ids`,
`winner_band_id`, `source: "venue_confirmed"`, `confirmed_at`, `received_at`.
Conflicting/duplicate outcomes are rejected; no winner is inferred from the
model or selected to improve its score. The label must have arrived by
`evaluation_as_of` and after the original freeze.

Optional `first_max_at` is a full offset-aware timestamp, with `peak_source`
such as `contract_qualified` or `station_review`. It must fall within the
city-local target date. Missing times remain unknown. This distinguishes
calls at/after the first observed maximum from calls with genuine lead; it
does not equate that first high with official market resolution or guaranteed
real-time certainty. Review observations may not satisfy a market's exact
routine-METAR rules. Keep that distinction in the source metadata.

Peak timing is **never used for prospective sample selection or readiness**.
The plan uses the schedule known before prediction. Realized peak time is
used only to describe difficulty after the fact. All cities, including
Jeddah, follow this rule; the word “noon” does not establish a forecast lead.

Optional `actual_max_c` is scored as temperature error only when
`temperature_rule_hash` matches the outcome's rule hash and both temperatures
are finite. No temperature is manufactured from a bucket midpoint. The
`settlement_value` helper requires an explicit conversion/rounding convention,
including the negative-half Fahrenheit case; it never replaces venue labels.

## Metrics and readiness

The report includes exact top-bucket hits, multiclass Brier and log loss,
optional Celsius MAE, reliability bins, and breakdowns by city, checkpoint
and actual forecasting lead. Log loss uses an explicitly fixed 1e-6 floor.
Call counts, city/date counts and target-date counts are separate.

Overall descriptive scores give each city/date equal weight across its slots.
Paired uncertainty evidence uses only the registered **primary** advance
slots. Later slots remain in descriptive reports but cannot hide weaker
early predictions or qualify a candidate. The method averages slots within city/date, then cities
within date, and resamples contiguous calendar-date blocks. A resampled day
retains the cities and slots together; repeated predictions and correlated
cities do not become independent weather-day trials. Missing calendar dates
are not bridged into a fictitious contiguous block.

The registered candidate must have an eligible original for every planned
case. Each fixed comparator must meet its paired coverage, date and city-date
requirements. City nonregression checks also use primary slots only.
Model and comparator must have the same registered case and
compatible station/rules, with both issue times and information-as-of cutoffs
inside the declared tolerance.

`ready_for_independent_review` additionally requires a positive lower bound
for exact-hit improvement and nonnegative lower bounds for Brier and log-loss
improvement against **each** declared baseline, with the declared city
nonregression check. Interval alpha is Bonferroni-adjusted across comparators
and these three metrics. Exploratory city tests average the declared primary
slots within each target date and apply a one-sided exact sign test to those
date-level gains. One-slot binary comparisons reduce to the discordant-day
binomial test. P-values are Holm-adjusted together across city/comparator
cells; they assume independent dates and are not the global gate.
Block bootstrap assumptions and sample
limits remain material even when every numerical gate passes.

`hold` means the report states explicit reasons: missing originals, stale or
unmatched baselines, training overlap, retrospective registration, thin city
coverage, insufficient date blocks, or improvement not established. Passing
is review readiness only: **no automatic promotion, trading instruction,
capital allocation, profitability claim or promised future accuracy**.

## Existing score extracts

Older exports with hit flags and stored scores remain valuable. Describe
them without reconstructing missing distributions or receipts:

```bash
python -m tools.predictive_evaluation \
  --output-dir /tmp/historical-score-audit \
  audit-scores /path/to/fact-checkpoint-outcome.json \
  --cities /path/to/city-timezones.json \
  --peak-reviews /path/to/day-reviews.json \
  --as-of 2026-10-31T00:00:00Z
```

Standard table fields and the compact audit aliases are accepted. An optional
`--city-cohort` freezes the included city keys instead of silently dropping
poor or retired cities. The historical mode always returns `not_assessable`
for replacement-model readiness. Its stored Brier/hit values are described,
not independently verified without ladders. It keeps the first **scored**
original; earlier unscored calls cannot be ruled out from that extract.

Rounded local peak-review hours are converted using the city's historical
timezone. Ambiguous/nonexistent daylight-saving times remain unknown, rather
than assuming a fold. Conflicting review rows are rejected. Source reading
time is not treated as proven publication/receipt time or market finality.
The output explicitly warns about repeated horizons and mixed evidence.

## Validation and ownership

Focused contracts: `tests/test_predictive_evaluation_offline.py`. They exercise
false-perfect post-outcome calls, replay evidence, training overlap, late
registration, mismatched stations/rules/artifacts, incomplete ladders, stale
quotes, missing cohort cases, original-versus-revision selection, DST folds,
negative-half rounding, correlated slots and non-overwriting output.

Run the repository's complete Python and database contract suites before
publishing. This package has no database schema or web/UI modifications.
The dated historical run/report is an external research artifact, not a
public repository upload of the user's collected data.

New fitting can continue in parallel. A new artifact is a new candidate
episode; retain each candidate's frozen shadow predictions and evaluate it
on later untouched cases. A replay under new weights does not become an
original prediction by reusing the old model's receipt. The evaluator checks
one frozen candidate artifact per episode; it does not require permanently
freezing the deployed model or prohibit future globally shared updates.
