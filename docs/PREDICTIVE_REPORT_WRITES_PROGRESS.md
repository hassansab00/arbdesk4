# Predictive report delivery

Follow-up to the predictive reliability release. Live acceptance reached
pricing and settlement but the validation report write received a gateway
failure; the subsequent cache submission also failed.

- Derived reports are preflighted and published in requests bounded by
  encoded bytes and row count. Every report and nested evidence field stays
  intact; a single oversized report fails explicitly before any report write.
- Gateway 520/521 responses share the existing bounded transport retries.
  Cache retries retain the original durable request ID and still require a
  valid acknowledgement. A persistent outage remains a failed run.
- No schema, scheduling, trading, model parameter, original prediction,
  settlement, archive or data-retention change is included.

This bounds request memory; it does not claim to eliminate database I/O or
prove the cause of a database restart. Tests and live acceptance belong in
the release PR. Forecast accuracy still needs future settled evidence.
