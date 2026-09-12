# ArbDesk proprietary data integrity

ArbDesk treats collected market, weather, forecast, model, decision, and
settlement history as evidence. Repairs must not replace that evidence.

## Protection contract

- Raw observations, forecasts, books, and trades are never cleared before a refresh.
- Settled forecast, band, and signal facts are insert-only.
- A wrong source row remains intact. Its correction is written to
  `proprietary_data_corrections`, linked by relation, source key, and original hash.
- Suspect data is recorded in `proprietary_data_quality_flags`; quarantine is a
  query decision, not a destructive move.
- Research revisions, paper evidence, corrections, flags, and integrity manifests
  reject UPDATE, DELETE, and TRUNCATE, including from the worker role.
- Historical rows that existed before the research archive are copied into
  `research_captures` when the safeguard migration is installed.

## Integrity manifests

After installing the migration, create a baseline from a server environment that
already has `SUPABASE_URL` and `SUPABASE_SERVICE_KEY`:

```bash
PYTHONPATH=scripts python scripts/data_integrity.py --code-version <commit-sha>
```

The command reads each dataset in stable pages up to one recorded cutoff, computes
a SHA-256 digest, and inserts one immutable manifest. It does not modify source
rows. Re-running with the same cutoff verifies the same scope; a different hash
means the scoped data changed and must be investigated before repairs continue.

## Repair rule

Every repair follows this order:

1. Record an integrity manifest.
2. Add a quality flag describing the defect.
3. Write a linked correction or a new derived version.
4. Re-run verification and document the expected difference.
5. Never delete the original evidence as part of the repair.

Retention is separate from repair. No retention job may delete source rows unless
a recoverable external archive for the exact range has been created and verified.
