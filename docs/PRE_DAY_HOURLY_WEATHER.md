# Received hourly weather for the shared pre-day model

This extends `PRE_DAY_WEATHER_STUDY.md` with real received hourly context,
an offline weather-conditioned candidate and mechanical miss diagnostics.
The research modules have no serving hooks, database writes, registry updates
or automatic promotion. No new scheduled workflow or production dependency
is introduced. Use the existing research environment with NumPy, pandas and
LightGBM (`requirements.wxpredict.txt`).

## Collection change

`scripts/live_capture.py` retains current `hourly0` and `models0` snapshots for
the city's entire current and next local date. The old fixed 24-hour-ahead
cutoff could omit the end of tomorrow at an evening-before decision. The new
window discards unnecessary past hours from **new** snapshots to bound storage.
An existing snapshot or frozen call is never rewritten or deleted.

The same chunk requests and compact per-city snapshots are used. Legacy
lead-1 and lead-2 capture windows and series are unchanged. Timezones with DST
retain the actual 23/25-hour day; fractional-offset zones keep the UTC hours
that fall inside each local date. Missing or invalid timezones retain the old
bounded window; their location metadata does not establish complete local days.

New snapshots preserve request latitude, longitude and timezone, plus returned
location/elevation metadata when the provider supplies it. These are request
and response metadata, **not** proof that every provider used the airport's
coordinates, a specific grid cell or a particular model initialization.
Receipt time remains distinct from initialization. Older metadata stays unknown.

Check the next real tick after merge: both current snapshot families should
contain every UTC forecast hour of today and tomorrow, carry original response
receipts and location metadata, and remain within the existing job budget.
A local request rehearsal is not evidence that the deployed tick completed.

## Research reader

`tools/wxpredict/pre_day_hourly.py` verifies the mirror's hashes/counts and uses
`max(captured_at, written_at)` for actual availability. No server receipt means
no eligible input. Missing values, incomplete days or receipts older than
36 hours cannot become complete context. Conflicting latest receipts block
that version rather than silently restoring an earlier value.

Source preference is `hourly0`, then `hourly2`, then `hourly`, only when the
entire target local day and all seven weather variables were received by the
decision. Current snapshots remain atomic; a newer partial snapshot does not
borrow its missing hours from an older snapshot. Change-only legacy records
use each hour's last received revision, explicitly recorded as such. They do
not imply a single initialization cycle. A previous-day forecast actually
received **before** the target day is legitimate input; it is not a forecast
downloaded after the outcome and relabelled as an old prediction.

Features include the forecast temperature range and peak hour, cloud/wind/
dew point around that forecast peak, radiation up to it, precipitation,
pressure change and receipt age. Peak-relative windows do not assume that
Jeddah or any other station peaks at a universal afternoon hour. Missing
context stays missing, with an explicit indicator and source category.

Where initial market-rule receipts exist, exact bucket-ID sets identify the
market and its city/date. Only server rule receipts before the feature cutoff
can supply contract context. A verified rules station mismatch excludes the
record. Later recovered text cannot repair an earlier model input. Original
audit context is used only as recorded, with its known receipt-time checks.

## Candidate and diagnosis

`pre_day_conditioned.py` fits one fixed pooled bucket classifier across
stations. It uses received provider temperatures, received hourly weather,
station, season and known contract context. Market prices, original model
probabilities, outcomes and diagnostic metadata cannot enter its feature
matrix. The recipe, feature order, source hashes, dependency versions, fit
completion time and model text are frozen in a private artifact.

Fitting requires twenty dates with twenty cities each, plus ten dates with
complete received hourly context for at least twenty cities. These are minimum
research gates, not evidence of statistical power or seasonal generalization.
The fixed tree size, regularization and training rounds are not tuned against
the development test. Candidate and incumbent probability scores and top-band
hits use the same original calls, with a separately matched market cohort.

`pre_day_rich_study.py` purges labels unavailable at the earliest evaluation
decision across timezones and preserves training-input replays separately from
original frozen comparisons. Output must be a new private directory outside
the repository. Never publish proprietary datasets, fitted parameters or
per-city reports to the public repository.

```bash
PYTHONPATH=tools python tools/wxpredict/pre_day_rich_study.py \
  --first YYYY-MM-DD --fit-last YYYY-MM-DD \
  --eval-first YYYY-MM-DD --eval-last YYYY-MM-DD \
  --through YYYY-MM-DDTHH:MM:SSZ --out /private/new-rich-study
```

Miss diagnostics distinguish candidate-only and incumbent-only hits, verified
original correction effects, native bucket distance and missing provenance.
Temperature distance to the winning interval is a **lower bound**, not an
invented exact-temperature error. Forecast weather alone cannot establish the
physical cause of a miss. Unknown historical rules and request coordinates
remain explicit. The same historical dates already inspected in development
cannot become independent forward validation by fitting a different model.

Both activation flags remain false. A small hit-count gain with worse
probability scores does not justify replacing the live predictor. A private
research artifact is not a durable future-prediction receipt or an automated
forward collection service. Historical expansion and genuine future validation
remain explicit prerequisites for any later activation decision.
