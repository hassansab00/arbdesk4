# Shared pre-day calibration challenger

This is a separate research workstream. Its new modules do not change pipeline
deadlines, database queries in production, capture, city calibration, strategy
execution, serving, UI or workflow schedules. It adds no production model.

## What it tests

`tools/wxpredict/pre_day_pairs.py` reads the hash-verified checkpoint and outcome
mirrors. It selects the original evening-before call before testing eligibility;
a later complete or correct call cannot replace a missing first receipt.
The research pair must have an original server freeze before the target's local
day, a matching saved schedule, valid original input status, the exact saved
served ladder and the raw-centre alternative already attached to that call.
It supports both original pair policies without rebuilding either distribution.

Missing receipts, unknown policies, late calls, missing inputs and pending labels
have separate counts. Invalid hashes, distributions and conflicting identities
fail instead of silently becoming usable data. Labels must be banked by the
research cutoff and remain within the original ladder. No settlement temperature
is invented. The archived city timezone interprets the saved schedule; this
does not manufacture an original timezone receipt or approve old data for the
production learner. Mirror bytes and counts are verified, and the manifest must
remain unchanged throughout the read.

`tools/wxpredict/pre_day_challenger.py` fits one shared conditional mixture of
the actual served ladder and that recorded raw-centre alternative. Its inputs
are signed correction divided by saved uncertainty, correction size and saved
uncertainty. Labels, market prices, city rankings and future observations cannot
enter the prediction features. A city's different weather context can change
the mixture through its frozen correction and uncertainty, without fitting a
separate model for that city. Existing city and station learning is untouched.

The raw alternative is bounded to at most half the mixture. Parameters have
fixed bounds, a fixed prior and regularization. Training uses equal weight per
date and requires the declared development coverage. This is an experiment in
calibration, not an independent replacement weather forecast model.

## Timing and evaluation

Chronological target dates alone are insufficient: a target-day label can arrive
after the next evening-before call. The split purges every training label not
banked before the earliest evaluation decision across all cities' timezones.
This prevents cross-city label timing from leaking into a historical fit.

Use the existing `requirements.txt` and `requirements.wxpredict.txt`:

```bash
python tools/wxpredict/pre_day_challenger.py \
  --first FIRST_TARGET_DATE --fit-last LAST_TRAIN_TARGET_DATE \
  --eval-first FIRST_EVALUATION_TARGET_DATE --eval-last LAST_EVALUATION_TARGET_DATE \
  --through RECEIVED_EVIDENCE_CUTOFF --out NEW_PRIVATE_OUTPUT_DIRECTORY
```

The output directory must be new. It contains exact fitted parameters and their
hash, source hashes, creation time after fitting, training identity, original
coverage/exclusions, per-city diagnostics and scores against the unchanged
incumbent, raw alternative and saved market. Matched-market scores use the same
calls on all sides. A crossed book is excluded from that comparison. These
prices are snapshot proxies, not executable fills or trading returns.

Do not put learned parameters, archive-derived results or private evidence in a
public PR. Source and synthetic tests can be published separately.

## Fit is not activation

The command fits an actual candidate. Its historical evaluation remains
retrospective development, even though its inputs were genuinely frozen before
their outcomes. The candidate itself did not exist at those historical calls.
A short pilot is not independent forward validation, nor does its evidence
transfer to a model refitted on the evaluation dates.

The artifact is integrity checked and always has `changes_serving: false` and
`eligible_for_promotion: false`. The forward-call helper rejects calls before
the model existed, captures from the future, stale captures and execution after
the target local day begins. A local artifact timestamp still is not a server
prediction receipt. No helper grants promotion or rewrites a historical call.

Serving integration and genuinely subsequent frozen predictions belong in a
separate coordinated change after the candidate demonstrates a reason to run.
Do not activate a candidate that loses to the unchanged incumbent, or retune
its evaluated window and call the result independent.
