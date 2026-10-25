# Scoped market settlement lookups

A city/day settlement check previously joined an aggregation of unrelated
historical band verdicts. The market resolution view now aggregates proofs
within each selected band's exact condition, YES token and NO token, using
the existing verdict primary key.

The market view retains its columns, invoker security, grants, latest
confirmation time and original rules for missing bands, incomplete evidence,
conflicting proofs, and confirmation of every band with exactly one YES winner.
The shared band resolution view remains unchanged, so its historical bulk
readers keep their existing aggregation plan.

The migration changes only the market view definition. It does not change
source rows, archive behavior, schedules, grants, or the freeze trigger.
Outcome checks continue to use live venue evidence.

The database contract compares against independent original view definitions.
It covers absent bands and proofs, repeated proofs, disputes, incorrect token
identities, zero or multiple YES winners, timestamps, rerunnable DDL, reader
roles and invoker row-level security.

Before application, compare old and replacement results in one REPEATABLE READ
snapshot with EXCEPT ALL in both directions. After application, check actual
reader roles and existing refresh paths. Query-level improvements do not
establish that a provider's project-wide I/O budget has recovered.
Check the full cache workload as well as individual lookups: a scoped query
plan can improve one caller while making a historical bulk join more expensive.
