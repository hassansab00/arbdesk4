# City model selection progress

The matched-call miss audit and city/horizon selector are implemented with
chronological training/validation gates, bounded nightly switches and exact
raw/baseline receipts. The raw comparison remains visible when the baseline
is served. Overlapping checkpoints select independently, and station-report
provenance comes from the vector actually used. The policy and measured audit
are in CITY_MODEL_SELECTION.md.

The complete checks and production acceptance are recorded in this change's
pull request. Held choices are expected until enough independent dates arrive;
no predictive accuracy improvement is claimed from the initial small sample.
The existing workflow cadence and capital settings are unchanged.
