# City and horizon model selection

Each original call keeps the raw WXPredict distribution, the corrected
weather baseline, the distribution actually served, and the selector version.
Selecting the baseline must not erase WXPredict's counterfactual miss. The
Predictive accuracy panel has a separate raw paired scope for that reason.
The existing served scopes retain their original calls and history.
The all-served overall score keeps its six common horizons. WXPredict overall
uses complete dates with noon and both before-peak checkpoints; its unsupported
pre-day and morning calls cannot enter that comparison. Both exclude the
explicit after-peak checkpoint.

The selector compares two fixed predictor families at each city and exact
checkpoint. Raw v3 remains the authorized prior for noon and peak-relative
calls; the existing baseline remains the prior before the day and in the
morning. The raw model is not extrapolated into an unsupported horizon.
All weather models and ensemble feeds keep running. Calibration is a separate
validated adjustment to raw v3; the selector compares raw v3 with the exact
frozen baseline rather than reconstructing either candidate after settlement.

The declared policy uses 180 days, at least 20 older training dates and 10
later validation dates in a 14-day validation window. Seasonal weights apply
to training. Training chooses the predictor by log loss. On later dates its
paired log-loss gain must have a positive lower bound from a 95% date
bootstrap, with no hit-rate or multiclass-Brier regression. There are 2,000
bootstrap draws with a fixed seed. A city/horizon has one original call per
target date. Different checkpoints and cities do not inflate that cell's
independent date count. These constants are policy priors, not measured gains.

Labels must have been received before the fitting day's UTC midnight; the
city's local day must also have ended. The raw model's training cutoff must
precede the target. Model versions and complete bucket ladders must match.
No blinded experiment or historical replay enters selection. A winner outside
the saved ladder counts as a miss for both models, not an omitted hard case.

Choices are bounded to baseline or raw v3 and at most one switch per nightly
date. Repeat runs retain that night's anchor. A choice is usable only for
its exact city, checkpoint and raw-model version, after its fit actually
existed, with fit and validation no older than three days. Insufficient or
expired evidence retains the authorized horizon prior. No improvement or
winner is claimed while those gates are held.

The existing nightly validation job stores the choice in the derived current
report and its versioned calibration JSON. Score-only runs retain fitting
dates. The tick prices the baseline once and selects independently for every
due checkpoint. Its current ladder uses the latest due checkpoint. Between
checkpoints, a live reprice borrows a checkpoint choice only inside that
checkpoint's existing capture window; otherwise it uses the horizon prior.
The blinded variants keep the input flags of their own original parent call.

The server validates the exact prior input packet for either selected model.
A baseline choice must serve the frozen baseline distribution and name its
actual baseline provenance; a raw choice must name the raw model. Historical
records receive no manufactured receipts or rewritten timestamps. Future raw
calls show the station-report time implied by their saved input vector,
rather than borrowing a newer baseline report's time.

## Initial miss audit — 18 October 2026

The live queries returned 13 settled paired calls across eight city-days,
all for the single target date 17 October. Raw v3 got 8 right and the baseline
11 right. Those calls are not 13 independent weather dates and cannot establish
which predictor should be promoted for a city/horizon.

| City and checkpoint | Raw pick | Baseline pick | Settlement |
|---|---|---|---|
| Austin, one hour before peak | 70–71°F | 68–69°F | 68–69°F |
| Dallas, two hours before peak | 68–69°F | 64–65°F | 64–65°F |
| Dallas, one hour before peak | 66–67°F | 64–65°F | 64–65°F |
| Houston, one hour before peak | 70–71°F | 70–71°F | 72–73°F |
| Seattle, noon | 58–59°F | 58–59°F | 62–63°F |

Austin's saved maximum was already 68°F and its public-model mean was
19.64°C; raw v3 still picked the next warmer bucket. Dallas's later saved
maximum was 65°F, while its remaining hourly forecast still reached 19.2°C.
These describe excessive remaining warming in those raw predictions. Houston
and Seattle missed in the cooler direction in both models. They do not prove
a causal weather explanation or justify tuning on this single date.

The audit also found that the call's displayed report timestamp could name
the baseline's newer report while raw v3 used its older saved arrival-mode
report. The exact vector and hash were preserved. Future receipt metadata
now names the report actually used; the historical record remains intact.
The paired audit uses saved input age and direction of bucket errors, and
separates raw-only wins, baseline-only wins and shared misses.
