# Private archive recovery snapshot

This prepares a separate private copy using the existing export guards.
It **does not retire live rows or establish recovered database headroom**.
Current application and Python readers still need private read-through before
retirement is safe. Approval to preserve history and retire verified duplicates
does not make missing reader support complete.

## Destination setup

Use a private, initialized `hassansab00/arbdesk4-private-archive` repository.
Give the connected GitHub app access if it will manage that repository.
The snapshot runner needs `AD4_PRIVATE_ARCHIVE_TOKEN` with contents read/write
for that destination. Keep it in a secret environment; never put it in source,
an argument, a credential-bearing remote URL, or a public workflow log.
The public source repository's Actions token is not a fallback credential.

Clone the destination with its normal HTTPS origin into a directory **outside**
the public source checkout. Check out its default branch and leave it clean.
Run from a trusted local or private runner, with the usual Supabase server-side
credentials available when exporting. Do not place private files in the public
source worktree, even on a branch.

```bash
PYTHONPATH=scripts python scripts/private_archive_snapshot.py \
  --private-root /path/outside/source/arbdesk4-private-archive --check-only

PYTHONPATH=scripts python scripts/private_archive_snapshot.py \
  --private-root /path/outside/source/arbdesk4-private-archive --snapshot
```

`--check-only` verifies repository identity, private visibility, read/write
permissions, clean default-branch checkout, and both fetch and push origins.
It also authenticates a dry-run push without sending ref updates; owner-level
permission metadata alone does not prove this credential has contents access.
It does not read or export database rows. A missing destination, token, or
permission fails before export; redirects and public targets are rejected.

`--snapshot` first verifies and seeds only committed files from the source
archive, mirror, and archive index. It ignores untracked packets. Every indexed
CSV gzip must parse to its recorded row count; mirror file digests must match.
The existing archiver then runs with `--export-only`, writing into the separate
private checkout. It retains the per-dataset cache, count, retention and mirror
guards. A dataset refusal remains visible and yields a nonzero overall result.
The archiver may refresh its existing derived cache and write operational logs;
the snapshot path never invokes live-row retirement.

The runner verifies all resulting catalog files, records their SHA-256 values
and row counts, rechecks private visibility, commits, pushes, then fetches and
compares exact remote Git blobs. A failed push, changed remote files or changed
visibility cannot produce a successful receipt. A partial export can preserve
its verified subset privately but still exits nonzero. No force push or rebase
is attempted. Authentication is scoped to child processes and not persisted.

If a legacy mirror entry points to replaced bytes, snapshot mode stops before
any database export or push. Supply an offline historical recovery packet with
`--recovery-root /path/outside/source/recovery-packet`. Its `receipt.json` must
name this source repository and list the original path, source Git commit,
recorded SHA-256 and row count for each version. Payload files are named
`<recorded-sha256>.csv.gz`. Every payload must match the existing catalog's
digest and row count exactly. Missing or inconsistent evidence refuses recovery.

Recovery writes new files and corrected references **only in the private
checkout**. It retains the current files and a byte-exact copy of the original
catalog as `data/mirror/manifest.before-private-recovery.json`. Source files and
source catalog claims are preserved. No missing historical value is inferred.

The shared archive writer now publishes complete immutable files atomically.
A different payload at an existing path stops that dataset. The nightly mirror
keeps the first daily snapshot filename; changed same-day snapshots get a UTC
timestamp and content hash. Mirror catalog updates are atomic, and interrupted
temporary files are excluded from source commits.

The private `data/private_archive_snapshot.json` receipt explicitly records
`private_readers_verified=false` and `live_retirement_allowed=false`. Repeating
snapshot mode over existing private history refuses rather than overwriting
or recounting it. This is a one-time recovery snapshot, not a new daily job.
Keep a failed local preparation for diagnosis; do not wipe it to retry blindly.

## Before retiring any live duplicate

1. Make the destination accessible and complete the private snapshot and remote
   verification. Preserve the existing public archive copies.
2. Implement private read-through for every affected job and application view;
   prove the same frozen predictions, settlements and historical rows remain
   available. Test stale/missing private files as failures.
3. Bind retirement to those reader and remote-file receipts, retaining all
   current SQL cache/count checks. The private-root archiver currently refuses
   `--commit`, `--prune-only` and other modes even if the caller supplies them.
4. Run a bounded recovery, verify live versus archived identities, then measure
   physical headroom. Snapshot success alone cannot close the capacity incident.

No schedule, public archive workflow, app configuration, portfolio setting or
production schema is changed by adding this tool. Private destination creation
and access must be verified separately; source tests do not prove live access.
