# Egress reduction — 21 November 2026

The feed-health panel previously selected 400 complete diagnostic `detail`
objects every minute. It uses only job/status/time/count and, for tick rows,
the observation-step summary. The new projection retains the same jobs,
ordering, 400-row window, polling cadence and health logic while excluding
unrelated forecast/book/model payloads. Original diagnostic rows remain intact.

A read-only comparison of the last 400 matching stored rows before 19 November
measured 11,741,366 bytes of SQL-serialized row JSON for the original fields
versus 61,385 bytes for the projected fields, about 99.48% less. This measures
decoded JSON for one query, not compressed wire traffic or total account egress.
Actual billed savings require normal operation and provider usage measurements.

The shared Python HTTP helpers now recognize an observed Supabase HTTP 402.
Authenticated requests to that configured origin fail explicitly for a bounded
60-second process-local cooldown. Reads, RPCs, writes and failure-log attempts
share that cooldown; external weather/venue requests and non-402 policies are
unchanged. After the cooldown a subsequent request may probe service recovery.
The change neither treats writes as successful nor erases the account quota.
It does not persist a circuit breaker across separate jobs or processes.

## Validation and remaining work

- Focused Python tests cover GET/write restrictions, blocked failure logging,
  bounded recovery, external-service isolation and non-402 permission errors.
- Feed health tests compare legacy and projected observation data for healthy,
  missing, stale, silent and failed station feeds.
- API logs for 20 November 15:00 through 21 November 15:00 UTC confirm 402
  restrictions across data reads and writes. A successful management SQL call
  does not establish application API availability.
- No database rows, archive files, model outputs, trade policy, credentials or
  billing settings are changed by these fixes.

Next stages remain incremental archive exports, historical reads served from
the verified private archive, shared bounded forecast inputs, compact serving
snapshots and direct archival of bulky raw capture where consumers permit it.
Do not clear whole tables nightly or retire records before their archive and
consumer-specific retrieval proofs exist.
