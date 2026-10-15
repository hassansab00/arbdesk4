# Predictive repair — 15 October 2026

## Findings and repairs

The serving Gaussian/floor engine and wxrd-v3 remaining-day shadow model are
separate prediction paths. Lead-1 correction switches do not correct lead 0.
A blanket claim that the richer research model already supplies serving is false.

Recent station/forecast records advanced, but venue outcomes did not. Before
repair, 93 recent venue events remained marked open and the table builder had
no labeled venue events for 12–14 October. The new bounded nightly step appends
settled labels and public hourly venue prices in separate files, preserving
original history and first answers. Empty/failed series retry on the next run.
The catch-up recorded 188 event rows and 131,520 price points; its final
22-series catch-up took 27.08 seconds, with zero failures or refusals.
Training/table/provenance readers now include those additions.

Parity now maps exact slugs or complete YES-token sets between numeric venue
IDs and live UUIDs, verifies city/date and refuses ambiguity or duplicates.
The live-reader CLI accepts the model's report lag (480 seconds for v2);
a failed parity CLI exits unsuccessfully.

## Accuracy evidence and rejected experiment

The 08:43:46 UTC paired snapshot had 391 settled first calls on 13–14 October.
Same decision instants, ladders and outcomes, after the model's 11 October
training cutoff: before peak, wxrd-v3 hit 166/302 (55.0%) against serving's
139/302 (46.0%). Morning regressed: 27/73 versus 33/73. Only two dates;
this does not justify a blanket serving replacement.

The fixed 09–11h specialist experiment used the existing weather features,
target and LightGBM parameters, 250 rounds, seed 20261011, and 63,853 training
rows only through 11 October. On 408 complete venue decisions on 12–14 October
it hit 181 (44.4%), against frozen v3's 166 (40.7%) and the market's 234 (57.4%).
Log loss worsened from 1.373504 to 1.385515 and worsened on all three dates.
**Reject the specialist as a serving replacement.** No subsequent tuning on
these inspected dates. This was exploratory out-of-sample evidence; its recipe
was fixed before fitting, after inspecting the baseline's weak early hours.

The separate frozen v3 09–16h hourly replay hit 637/1,088 (58.5%), against
market 747/1,088 (68.7%). Its 136 complete venue days were 47/47/42 by date.
Do not mix that hourly cohort with the 391 live checkpoint pairs.

## Validation, delivery and open work

Full local validation before interruption: 4,088 Python tests passed, 9 skipped,
and all database contracts. Release checks after recovery are recorded in the PR.
The new steps use the existing archive schedule; minute estimates include their
4 + 3 minute timeout upper bounds, not claimed measured production cost.

The nightly paired audit reports per-checkpoint hits/log loss, coverage,
training cutoff, morning regressions and a 14-date review gate. It changes no
serving model, capital or allocation. Inputs now reach the builder; automatic
nightly model refits are not implemented. Keep wxrd-v3 frozen for its existing
forward test.

Full input parity still fails (13–14 October: 1,270 unexplained cells and 359
unmatched rows). Late US local-day forecast hours had not matured in the UTC
record; market snapshots have different decision times; Moscow had 26 weather
cell differences on the 13th. Do not loosen tolerances to obtain a pass.
Next model work needs mature local-day coverage, station report/correction
identity, forecast residual diagnosis and a recipe tested on future dates.
This batch repairs data collection and evaluation; it does not establish a
profitable trading edge or solve the morning accuracy gap.

Aggregate reports and experiment input/model hashes are committed under
data/eval/wxpredict/forward. Optional raw evaluation snapshots remain unpublished
after automatic approval review rejected their public uploads. Synthetic
regression fixtures exercise the audit instead. The full existing operational
progress-log upload was also rejected; that file stays unchanged and this
focused report records this batch's progress instead. No alternative upload
route is used for those rejected payloads.

To run the paired audit on current settled calls, use
tools/wxpredict/forward_audit.py with the existing service environment. A new
experiment needs requirements.wxpredict.txt, a table built with 480-second
report lag, explicitly frozen training/evaluation cohorts and their hashes,
then tools/wxpredict/early_trial.py --table <full-table> --test-table
<frozen-evaluation-table> --out <report>. Later settlements change denominators;
current data is not an exact replay of the earlier captured cohort.
