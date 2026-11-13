# Status health and private regression audit

P6.5 follow-up: reuse venue-band resolution, market confirmation, cleaned facts
and coherence once per status read. Preserve the public aggregate shape and
security-invoker boundary. Existing page status scheduling is unchanged.

The service-only `predictive_regression_audit` RPC reads the already-produced
original-call validation reports. It has keyset pagination and an explicit byte
limit. It exposes matched comparisons, metric disagreements, training readiness,
source timestamps and incomplete retained daily details to the operator.
It performs no fitting, promotion, order entry or financial writes.

Verification: executable database contracts cover equivalence, disputed/missing
outcomes, role access, repeat installation, cursor boundaries and source-data
preservation. Production comparisons and measured results belong in the private
operational checkpoint, not in this public source note.
