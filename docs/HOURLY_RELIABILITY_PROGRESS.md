# Hourly capture reliability and capacity acceptance

The primary's inference shares the hourly collector's original runtime.
The collector now has a bounded larger allowance, with an explicit reserve
outside the shadow engine's deadline for paired same-day captures and the
final log. Engine requests and retry waits respect that earlier deadline.
Experimental captures still follow the engine's decisions. Missing anchored
or model-only coverage is attention even
when checkpoint persistence succeeded. No capture is reconstructed later.
Forecast recipes, freezes, calibration, trading permissions and cadence stay
unchanged. The existing job's finite timeout includes its collector backstops.

The archive also measures database size against the configured capacity
target separately from its export/prune result. Pressure writes attention;
an unreadable measurement fails instead of reporting health. This does not
shorten retention or change the archive's commit/read-back/count safeguards.

Capacity is still open. Successful reclaims and this monitoring check do not
make retained live history fit the configured budget. A broader cold-history
migration or a separately authorized capacity increase is needed if the live
footprint cannot reach its target. Accuracy and sustained reliability remain
forward-validation questions, not results established by these changes.
