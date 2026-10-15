# Learning and record build — 15 October 2026

This batch prepares repeatable learning without claiming a new accuracy result.
The serving model, pricing switches, capital and the frozen wxrd/wxrd3 weights
stay unchanged. Accepted automatic challengers have a separate `wxrd_auto`
shadow variant. It cannot trade or replace a serving prediction.

## Exact inputs and station corrections

* `live_capture.report_rows` keeps all distinct reports for one station/minute
  in a single versioned set. Selection follows the response's explicit order,
  rather than an unspecified order of equal-time database rows. Legacy arrays
  remain readable.
* The nightly IEM fetch adds a separate receipt-time journal under
  `data/training/wxpredict/station_report_versions`. Corrections, special
  reports and reversions survive. An as-of reader requires both the actual
  receipt and the eight-minute report lag. Existing historical files retain
  their original answers; missing arrival times are never invented.
* Each live model run saves the engineered decision vector, without outcome
  columns, in private `live_capture` kind `wx_features`. Its record includes
  the first checkpoint ID, ladder, feature policy, history checkout commit and
  commit time, decision time, actual capture time and vector hash. Capture
  failures appear in the shadow job's detail and partial status.
* Evaluation uses these recorded vectors, never features rebuilt from later
  station history. A capture over 30 minutes late, or a history checkout newer
  than the decision, is ineligible. A later engine retry cannot replace the
  first city/day/checkpoint. Final venue labels are joined by exact contract
  identity and ladder geometry; the market benchmark is the first checkpoint's
  frozen bid/ask book, with the established midpoint rule for one-sided books.

## Guarded candidate cycle

The existing daily workflow gets a bounded candidate job after its core job.
A cheap planning and recorded-input coverage check precedes ML installation.
No new scheduled workflow or clock dispatch is added.

The fixed recipe is remaining-v2, seed 20261011, 250 rounds. A candidate needs
at least 50,000 training rows and a fourteen-date validation window ending two
UTC dates before the run, with one whole calendar date between training and
validation. Neither the incumbent nor the candidate may have trained inside
that window. The first incumbent is frozen wxrd-v2; wxrd3 continues separately.

Acceptance is shadow-only and requires all of:

* at least twenty cities on every validation date at each of the five
  checkpoints, and at least 80% common first-call coverage;
* no checkpoint loses hits or worsens mean log loss against the incumbent;
* the lower 90% date-clustered bootstrap bound on log-loss improvement is
  positive; and
* no validation bucket probability moves by more than 0.10 from the incumbent.

The aggregate report includes the market benchmark. Passing these gates is
not evidence of a trading edge or permission to serve the model. The real-ask
walk-forward requirements in `CLAUDE.md` still apply before pricing or trading.
The cycle refuses to fit on 15 October because the current incumbent was
trained inside the proposed comparison window. Recording the new vectors
starts only with deployment. A complete future window is still required;
older reconstructed data cannot shorten that wait.

Accepted weights are immutable repository Release assets, with bounded
size/time downloads and SHA256 verification before LightGBM import. Uploads
must pass a public read-back hash check before registration. Metadata and
reports precede the atomic canonical registry update. A separate pointer is
only a cache hint. Rejected or failed fits cannot change the active pointer.

Rollback, after checking out current main:

```sh
python tools/wxpredict/candidate_cycle.py --rollback ACCEPTED_MODEL_ID
# Or disable the automatic variant, leaving both frozen variants running:
python tools/wxpredict/candidate_cycle.py --rollback baseline
```

Commit and push the changed registry and pointer to deploy that choice. Both
rollback forms pause automatic refits. `--resume` clears the pause; commit and
push that change as well. Prior models and reports remain available.

P6.1 accounting explicitly includes the new job's 35-minute timeout ceiling:
`pipeline_daily.yml` becomes 66 estimated minutes including its existing
31-minute estimate, and the monthly guard becomes 6,500 minutes. These are
bounds, not new runtime measurements. The waiting path skips fitting.

## Database pressure and the cold record

Live shadow inference can need 46 hours of captures. It now joins the older
part from the committed mirror, checking manifest coverage, file hashes and
row counts. Missing, stale or corrupt cold history refuses prediction instead
of silently changing the feature row. The tick checkout includes those files.

The archive's live-capture window becomes yesterday's UTC midnight and newer.
The SQL prune caps every requested cutoff at that mirrored boundary. This is
not a rolling 24-hour deletion. Export, commit, read-back and exact-count
verification remain mandatory before a prune. No records are deleted by the
migration itself.

Only explanatory model payloads older than two full dates move to the
repository; numerical forecast rows stay. The payload reader starts yesterday.
A repeatable-read production rehearsal on 15 October compared all 318 current
reader rows before and after the hypothetical change with `EXCEPT ALL` in both
directions: zero differences. It counted 7,095 additional eligible payloads at
that instant. This was a read-only proof, not an actual prune or space saving.
The existing model-table reclaim becomes daily at 07:15 UTC, after the archive.
Actual disk savings require a successful archive and reclaim and remain to be
measured. Database pressure was still above the configured 500 MiB threshold
when this batch was built.

## Validation

The tests cover version receipts and reversions, cold-record equivalence and
integrity failures, recorded-vector timing and labels, exact contract/book
matching, evaluation regression gates, immutable weight validation, registry
selection, rollback and a real small LightGBM fit/inference/rejection path.
Both complete repository suites are required before publishing. Results and
deployment verification are recorded in the pull request for this batch.

The historical full progress log and optional raw evaluation exports are
excluded from this batch after automatic approval review blocked their upload.
This focused note records the new behavior and its limits.
