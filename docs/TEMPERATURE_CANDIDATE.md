# Shared temperature candidate

This is a separate research path, `shared-temperature-normal-v1`. It never
changes the serving registry, capital, paper entries or the existing
`shared-early-weather-v1` experiment.

The shared model predicts a temperature location and uncertainty from the
received hourly forecast: temperature evolution, cloud, solar, wind speed,
dewpoint, precipitation, pressure, season, forecast age and an available
contract-qualified observation innovation. Member/MOS histories and wind
direction are not invented when the original inputs lack them. Provider model
initialization is explicitly unknown; actual input receipts remain recorded.

The starting reference is **unfitted**: hourly forecast maximum with a fixed
1.5°C standard deviation. A pooled regularized interval-likelihood fit requires
at least seven dates, twenty cities on every included date and two hundred
original labeled rows. Those are eligibility minima, not proof of skill.
Confirmed venue brackets are interval targets, never midpoint point targets.
Independent date weights and shared city/date weights prevent repeated horizons
from multiplying the apparent sample. A fit has bounded coefficients, an 8°C
maximum location adjustment, 0.5–6°C width and a bounded runtime. These are
declared design limits, not measured accuracy results.

Capture slots are fixed in each station's local timezone, including DST:

| Horizon | Target-relative clock |
| --- | --- |
| day_ahead | D−1 at 09:00 |
| d1_eve | D−1 at 18:00 |
| morning | D0 at 09:00 |

The hourly Tick can capture within 75 minutes of the slot. The morning requires
both an original seasonal peak reference and the received forecast peak to be
at least one hour ahead. A missing or too-early peak is an exclusion, not an
invented noon reference. Each candidate freezes within five minutes of its
server-clock input packet, before any confirmed venue outcome.

The database binds the frozen ladder, dated rules, station, location, hourly
values, source receipts and any routine-METAR floor to the actual source. Calls,
scores, fixed registrations and fits cannot be updated, deleted or truncated.
Changed rules or geometry prevent a later settlement from silently becoming a
label for a different frozen contract.

Full input packets and fitted coefficients remain service-only research data.
They are explicitly excluded from the public mirror and are never pruned by
this build. `temperature_export` produces a bounded private research bundle;
back up those bundles privately before any future retention change. Existing
archive/offload mechanisms are unchanged. This build creates no paid service.

The existing Tick runs `scripts/temperature_forward.py` with a 25-second process
limit and a shared 20-second request deadline. The existing daily workflow runs
`scripts/temperature_learning.py` independently of trading stages, with a
110-second process limit. It reports input eligibility, exclusions, dates and
whether it fitted. A fit is stored privately as **unregistered**. Identical
evidence is not fitted again. Nothing replaces the trial artifact or reaches
serving through that job.

Registration validates the artifact and the database round trip. Coefficients
use exact hexadecimal strings. A registered artifact has a separate twenty-date
future window beginning at least two UTC target dates after registration, with a
fixed cohort. Its start moves later if any cohort day-ahead slot already passed.
Warmups stay preserved but training starts at that predeclared complete window.
An unfitted reference collects evidence but cannot pass as a learned model.
Every fitted challenger requires its own future registration/window.

Evaluation uses all three horizons separately, the unchanged as-of incumbent,
the explicit raw normal reference, the hourly reference and matched market
quotes. A genuinely saved controlled raw-centre alternative is additional;
the raw normal reference does not claim to reproduce probability calibration.
Missing pairs remain in the fixed-cohort denominator. Gates require twenty
dates, twenty cities, two hundred calls per horizon and 90% cohort coverage,
positive lower 95% paired-date log-loss gain and no hit/Brier regression.
Probability calibration uses fixed bins. MAE, continuous CRPS and 80% interval
coverage use only complete fingerprint-checked v6 routine-report points that
agree with the venue bracket. Point gates compare the two explicit temperature
references with the same sample/coverage minima, positive lower paired-date
CRPS gain and no MAE regression. There is no automatic promotion endpoint.

Previously inspected dates support development, not untouched final validation.
Use `tools/wxpredict/temperature_study.py` for private chronological research and
`scripts/temperature_evaluate.py --input PRIVATE_EXPORT.json` for the fixed
future evaluation. The input export and fitted artifacts never belong in the
public repository. Green tests establish engineering behavior, not forecast skill.
