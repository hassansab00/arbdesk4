# Shared pre-day weather research

`tools/wxpredict/pre_day_weather_study.py` builds a **research-only** weather
candidate from recorded provider temperatures. It has no serving integration,
registry write, workflow, Supabase mutation or automatic promotion. Existing
frozen calls, eligibility rules, paper strategies and live cards are unaffected.

## Inputs and evidence

The reader verifies the mirror manifest's SHA-256 and row count for each selected
CSV. It uses only `open-meteo-models-current` rows, with both producer fetch time
and the database's `observed_at` receipt no later than the decision. Historical
`previous-runs` forecasts are excluded. `run_at` is a fetch stamp, **not** the
numerical weather model's initialization time. Conflicting values with the same
city/date/provider/run key are quarantined. Identical duplicate receipts use
the later availability time. Inputs expire after 36 hours; at least four of
the seven declared providers are required. Local-date lead must be one or two.

Venue and internal ladders must have exact identity and interval geometry.
Labels are actual closed venue winners confirmed by banked outcomes. No exact
final temperature is guessed from a bucket. Known original station mismatches
are excluded. The feature hash includes the contract station, unit, local date,
timezone, static latitude and each provider's recorded value and receipt.

Two evidence classes stay separate:

| Evidence | Training | Original accuracy comparison |
| --- | --- | --- |
| Received weather-input replay at local 18:00 the day before | Yes | No |
| Original evening-before call with server freeze, correct station, valid ladder and on-time receipt | Yes | Yes |

The first original call is selected before eligibility checks; a successful
later retry cannot replace it. Original comparison features use its actual
decision time within the saved local 18:00 window plus 75 minutes. The saved
18:00 schedule is not treated as the actual decision time. No target-day noon
or before-peak observations enter this experiment.

## One pooled model

The candidate learns one set of provider weights shared across all cities. It
models a mixture of provider forecast temperatures in Celsius with a shared
season/latitude/receipt-age adjustment, bounded station-and-unit offsets and
residual uncertainty responding to provider disagreement and receipt age.
Station offsets remain zero without twenty distinct training dates. Unknown
stations receive the shared prior. This is one model with station context,
not a selection of whichever city model won a backtest.

It optimizes probability assigned to the actual winning venue interval. Native
integer boundaries use half-up rounding, including negative Fahrenheit; open
tails retain all probability mass. Exact temperature error cannot be measured
without a verified exact-temperature target. Market prices, incumbent
probabilities, settlements and target-day observations are not forecast
features. Labels enter only fitting and subsequent scoring.

Fit requirements, priors and parameter bounds are fixed in source. Each date
has equal training weight. The recipe uses NumPy and SciPy in a separate local
research environment; production requirements and Actions installs do not
change. The dependency versions, code hashes, training evidence hash, fit
completion time and immutable candidate hash are recorded.

## Run privately

Install `requirements.txt`, NumPy and SciPy in a research virtual environment.
Supply chronological ISO dates and an archived evidence cutoff:

```bash
PYTHONPATH=tools python tools/wxpredict/pre_day_weather_study.py \
  --first YYYY-MM-DD --fit-last YYYY-MM-DD \
  --eval-first YYYY-MM-DD --eval-last YYYY-MM-DD \
  --through YYYY-MM-DDTHH:MM:SSZ --out /private/new-study-directory
```

Output must be a new directory outside the repository. The command saves the
dataset, candidate, historical predictions and evaluation privately. Preserve
the package in private durable storage; never commit proprietary records,
fitted parameters, per-city results or provenance-derived data publicly.

Training labels must have arrived before the earliest evaluation decision
across **all city timezones**. Availability is no earlier than the local day
ending and the banked receipt. Late labels are purged. Historical comparison
is restricted to verified original calls. Candidate and incumbent figures
against the market also use an identical quoted cohort. Missing quotes do not
become losses. Incumbent hits retain the original saved favourite, including
ties. Prices are probability diagnostics, not evidence of executable profits.

Historical predictions are clearly marked development replays. A candidate
fitted now did not exist at those old decision times. `predictions(...,
forward=True)` additionally requires a candidate already available, genuine
current pre-day feature time, receipt within fifteen minutes, and an unstarted
target local date after training. This guard alone is **not** a durable freeze
receipt or an automated forward collection/scoring service. Do not label a
historical replay as prospective validation.

## Limits and completion criteria

Daily summaries lack the original provider request coordinates, full hourly
completeness and detailed wind/cloud/humidity shifts. Historical contract-family
metadata is incomplete. A station target does not prove the provider request
used that airport's coordinates. A short autumn sample cannot establish
seasonal accuracy. These gaps must remain explicit, not filled with invented
features.

A development run completes when its verified dataset, frozen candidate and
paired comparison are preserved. It does **not** complete forward validation
or activation. Both artifact flags remain `changes_serving=false` and
`eligible_for_promotion=false`, even when a historical score is better. A
candidate that loses on the target hit rate stays offline. Do not tune it on
the same evaluation outcomes until it appears to win. Stronger verified
weather features and independent future predictions remain separate work.
