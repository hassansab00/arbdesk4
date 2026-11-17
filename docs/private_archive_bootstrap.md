# Dedicated private archive bootstrap

Approved destination: `hassansab00/Arbdesk4-archive-001` (private).

The GitHub connection can access the destination. Actions and the deployed
application use separate runtime credentials; granting the connection access
does not configure those runtimes.

## Runtime configuration

In the **source** repository's Actions secrets, set `AD4_PRIVATE_ARCHIVE_TOKEN`
to a fine-grained credential with **Contents: read and write**, scoped only to
`hassansab00/Arbdesk4-archive-001`. Do not put a token in source, chat, a remote
URL or a public artifact. The source repository's built-in `GITHUB_TOKEN` cannot
access a different private repository and is deliberately not a fallback.

The source workflow already uses `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`;
the bootstrap uses those existing source secrets without copying them into
the archive repository.

Run **Private archive snapshot**, first with `check-only`, then `snapshot`.
The PR check reports missing runtime configuration as blocked without claiming
any export. A manual run fails when configuration is absent. No new schedule
is introduced, and no automatic snapshot runs from a pull request.

The script clones the archive outside the public checkout. Access checks
validate private visibility, canonical origin, default branch and an
authenticated dry-run push. Snapshot mode seeds committed archive history,
then uses the existing export-only guards. It independently restores legacy
mirror versions only when bytes from the previously verified source commit
match the catalog's recorded SHA-256 and CSV row count. Current source files
remain intact. A different or missing historical version refuses recovery.

No private payload or recovery packet is uploaded as an Actions artifact.
Snapshot mode is a first recovery bootstrap, not an incremental daily worker.
It refuses existing private history instead of overwriting it.

## Remaining production gates

Bootstrap does **not** enable live retirement. That requires deployed private
readers, reader parity and restore evidence, and a remote verification receipt
bound to the records being retired. Existing public readers continue to use
their current repository until that switch is independently verified.

Currently mirrored-only tables need consumer-specific support before their
live copies may move. Finally, measure physical database allocation after
retirement and suitable compaction; a snapshot alone cannot prove headroom or
resolve the separately accumulated bandwidth quota.
