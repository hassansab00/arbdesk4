# Superseded model skill archive

The existing nightly archive gains superseded per-model skill measurements.
It retains a week and every city/model/lead/evidence scope's latest full row
regardless of age, including null and legacy scopes.

The mirror, committed export, read-back and exact count guards precede pruning.
A signed dataset refuses pruning until its verifier can read the archive.
A table lock protects the final count/delete from concurrent fits, and a
comparison of all latest full rows rolls back any changed retained value.
Content-addressed filenames prevent a later export from replacing an earlier
asset covering the same dates. The existing archive API reads its date alias.

The reclaim allowlist appends the table while preserving all existing slots;
a quiet-window database reclaim backstop follows the existing archive pattern.
No prediction, observation, outcome, learner gate or capital setting changes.
This addition alone does not establish free database capacity. Real export,
prune and read-back acceptance remain required after deployment.
