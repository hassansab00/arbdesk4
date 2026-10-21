# Optional engine coverage

The model-only pass now reserves order-fill time only for enabled strategies
with an active ledger. Disabled research proposals remain recorded without
reserving time for orders they cannot place. Submission still rechecks
enablement. Enabled strategies retain their fill reserve.

Collector deadlines, experimental capture order, forecast math, frozen calls,
calibration gates and capital settings are unchanged. Source recovery and
free storage coverage remain separate work and are not claimed complete.
