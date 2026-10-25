# Scoped settlement proof lookups

The band and market resolution views previously joined a global aggregation of
settlement proofs. A lookup for one city and date could therefore aggregate
unrelated historical proofs before returning the selected market.

The replacement uses a lateral aggregate scoped to each selected band's exact
condition, YES token and NO token. It uses the existing verdict primary key.
The market view keeps the original rules for missing bands, incomplete evidence,
conflicting proofs, and confirmation of every band with exactly one YES winner.
The band view retains its original columns, repeated-proof counts and latest
confirmation time.

This migration replaces view definitions only. It does not change source rows,
archive behavior, schedules, grants, or the freeze trigger. Both views remain
security invokers. Outcome checks continue to use live venue evidence.

The database contract test compares against independent copies of both original
view definitions. It covers absent bands and proofs, repeated proofs, disputes,
incorrect token identities, zero or multiple YES winners, timestamps,
idempotent application, reader roles and invoker row-level security.

Before application, compare old and replacement results in a single REPEATABLE
READ snapshot with EXCEPT ALL in both directions. After application, verify the
actual reader roles and existing refresh paths. Query-level improvements do not
establish that a hosting provider's project-wide I/O budget has recovered.
