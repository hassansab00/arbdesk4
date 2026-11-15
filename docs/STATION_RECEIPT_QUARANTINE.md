# Future station reports

The nightly station update retains an upstream report dated more than the
existing five-minute tolerance beyond its actual receipt in a distinct
quarantine schema inside the append-only version journal. It records a count
and continues the valid members of the response. Original station files stay
unchanged. Quarantined rows do not enter legacy nightly readings, daily
reductions or as-of training, even after a later decision clock passes their
claimed valid time. Only a separate genuine later receipt can admit a report.

The journal's default API still refuses a future report. Quarantine is an
explicit nightly-ingest path; malformed identities, timestamps and conflicting
receipts retain their existing failures. This is a collection repair, not a
relaxation of label chronology, a model promotion or an accuracy improvement.
