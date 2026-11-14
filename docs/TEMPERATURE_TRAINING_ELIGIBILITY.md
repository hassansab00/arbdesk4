# Temperature training eligibility

Preparation and fitting share the same original-label validation. A settled
label remains excluded until the target city's local day has ended at the
evidence cutoff. The frozen call must be within the existing five-minute
input-to-freeze allowance, and all input, freeze, settlement and label-receipt
timestamps retain their causal order. Local days use the frozen timezone,
including daylight-saving and fractional offsets.

First-original-call selection still happens before eligibility filtering.
Excluded evidence is reported, never replaced by a later duplicate. This
change does not lower sample minima, fit or register an artifact, reset a
forward window or alter a production weight. Fitting eligibility and later
untouched forward validation remain separate gates.

An exact source-hash compatibility rule preserves the existing unfitted,
zero-coefficient reference and its registered trial. Only the two changed
training-source hashes may differ; all other source hashes and all artifact
integrity, coefficient and policy checks still apply. Legacy fitted artifacts
receive no compatibility exception. Existing artifacts are never rewritten.

Regression fixtures exercise unfinished local days in three timezones and
both sides of the existing five-minute boundary. These synthetic cases
demonstrate a preparation/fitter mismatch; they do not establish that the
mismatch caused the currently observed accuracy deficit.
