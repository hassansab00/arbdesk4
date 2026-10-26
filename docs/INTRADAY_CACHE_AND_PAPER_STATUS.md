# Intraday cache handoff and paper strategy status

The intraday pipeline's final step requests a dashboard-cache refresh through
the existing durable database worker. It ends after an acknowledged receipt
for that exact request ID. Queued, waiting and running receipts are explicitly
pending; they do not claim that display rows have refreshed. A lost or invalid
acknowledgement and immediate errors still fail the step. Ambiguous transport
retries reuse the same request ID.

The worker retains its bounded execution, retries, immutable terminal receipts
and schedule cleanup. Background errors remain visible in ingest_log and the
existing health watchdog. The daily and archive paths that require acknowledged
completion keep their existing blocking helper. No new schedules or workers
are introduced, and collection, freezing and scoring still precede the handoff.

The paper desk read route now includes strategy suspension provenance alongside
the enabled flag used by execution. A disabled strategy suspended by its loss
guard is shown as suspended, with its recorded date and reason. Recorded BUY
decisions remain distinct from filled trades. Enabled strategies retain their
actual enabled state even if an older state label is present. Missing or failed
activity reads continue to preserve the account list and report the error.

These changes do not enable strategies, change risk limits, write trades,
replace frozen predictions, or alter archive retention. Validation covers the
request acknowledgement and retries, existing synchronous completion behavior,
paper status calculation and the real paper-desk route against fixture HTTP.
