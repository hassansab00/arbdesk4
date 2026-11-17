# Reading a verified private snapshot

The Python history readers can opt into a checkout of the approved private
repository without changing their parsers, weather timing, frozen calls or SQL.

After cloning and independently verifying current private repository access,
pin its commit and configure the worker process:

```
AD4_PRIVATE_ARCHIVE_READ_ROOT=/private/path/outside/the/source/checkout
AD4_PRIVATE_ARCHIVE_READ_COMMIT=<verified 40-character private commit>
```

These are non-secret identities, not credentials. The clone/access credential
stays in the runner's existing secret environment. Never place the private
checkout beneath the public source repository or upload it as an artifact.

The reader checks the canonical private origin, checkout root, pinned HEAD,
committed snapshot receipt and manifest digest. Each file must have a matching
SHA-256 and byte count before the existing CSV parser reads it. Missing,
unregistered or changed private files refuse instead of falling back to an
incomplete public checkout. Payload verification also runs before a cached
parse may be reused.

Covered Python paths: weather history, book history (including ladders),
weather/model calibration consumers that use those readers, resolution verdict
restoration, and wind-direction backfill. The default source-root behavior is
unchanged when the private read root is not configured.

This change does not mount a checkout into production workflows, change the
web application's credentials or access boundary, turn on private app reads,
or enable retirement. Those switches require live reader/restore proofs for
each affected consumer. Mirrored-only datasets still need their own consumer
support; a private mirror by itself does not make them eligible for retirement.
