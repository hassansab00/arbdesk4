# Private regression audit screen

Predictive → Private audit reads the existing service-only original-call audit
through the existing owner-session gate. Legacy single-user mode cannot bypass
that check. No service credential or private report is embedded in the client.

The panel opens without a background read until expanded. City, checkpoint,
flagged/all and page-size filters reset pagination. Each explicit page uses a
complete city/checkpoint cursor, a default of 20 cells and a server ceiling of
25; the existing audit RPC retains its response-size guard. Source reports can
change between requests, so pages do not claim a fixed snapshot. Refresh starts
again at the first page. Closing the panel unmounts its evidence.

Cards retain recent/prior windows, matched market denominators, probability
confidence, saved raw/baseline correction pairs, learning readiness, source
timestamps/fingerprints and retained daily detail. Missing values stay unknown;
incomplete detail is explicit. Served adaptations and baseline correction pairs
remain separate. Alerts are descriptive, not causal explanations, executable
returns or approval to change a model.

No timer, migration, collector, learner, serving adjustment or paper-policy write
is added. Runtime access and real owner-browser acceptance must be verified
separately from the local DOM interaction tests. Existing private research,
immutable calls, policy slots and reports are outside this change.
