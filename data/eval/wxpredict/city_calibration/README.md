# Frozen city calibration evidence

The existing nightly candidate job commits one JSON report per UTC fitting date.
Each report includes the prior, bounds, training/validation date counts, input
identity digest, accepted or held parameters, and version. It uses only served
calls with a server freeze receipt, prior input packet, earlier model training
cutoff, and a label received before the fitting date. Repeated runs use the
same nightly anchor. Exact parameters used by a call remain on that immutable
checkpoint. No legacy receipt is backfilled.
