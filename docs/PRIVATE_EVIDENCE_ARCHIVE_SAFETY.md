# Private evidence archive verification

Offline restore requires the complete schema-version table set, canonical
primary keys and one packet group per table, including empty tables. It rejects
missing or duplicate entities, malformed frozen membership and extra packets.
Mutable account/order rows must match their frozen manifest snapshot through
two-way EXCEPT ALL, in addition to immutable receipt and ledger reconciliation.
Hashes alone do not establish a complete snapshot.

Export writes a mode-0600 temporary file, runs the independent database restore,
then publishes the final name atomically without overwriting another bundle.
A failed verification removes the temporary file and leaves no final bundle.
The exporter performs no retention action and must never feed public artifacts.

The existing worker reports private evidence capacity as ok, warning, hold or
unknown. Warning begins at 80% of the existing database limit; this is an
operational headroom threshold, not a prediction or allocation gate. Pressure
and missing capacity evidence retain attention status. Existing queued fills
and independent settlement recovery remain active. Normal logs contain only
the capacity state, not a raw private packet.

These changes do not reclaim database space, increase its limit, alter a
model gate or prove an archive was durably retained. A real private export,
offline restore and durable retention are separate acceptance steps. Retained
research and financial facts are unchanged.
