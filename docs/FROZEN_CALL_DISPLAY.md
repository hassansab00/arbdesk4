# Original calls beside live forecasts

City cards and their detail popup show the selected original call separately
from the latest operational ladder. The saved call must match city, trade
date and checkpoint. A missing or failed read never borrows another horizon,
a newer capture or a live price. The recorded time is labelled in the city's
timezone; server receipt eligibility remains separate in validation. Bounded
anonymous reads use only the already-public original-call views. No new
endpoint, privileged table read, grant or authentication change is introduced.

Legacy day ahead remains the last pricing before city-local midnight in the
settled history. It is distinct from the evening-before checkpoint and is
not relabelled as having a server receipt.

Validation uses each checkpoint's stored local schedule. Fixed moments still
require their exact configured clock; relative moments preserve their saved
peak anchor on the target date. Receipt windows, local day-end, outcome-time
checks and original-first selection remain enforced. Later peak-table updates
cannot re-plan historical captures. Calls without receipts remain legacy.
Reports identify this policy as `saved-city-clock-v1`.

No source predictions, outcomes, freeze triggers, learning parameters or
workflow schedules are changed. Existing scoring cycles replace only derived
reports. Synthetic regressions cover the clock change, original misses,
malformed or late captures, city DST, read failures, paging and horizon/date
isolation. Run the complete Python and database suites and the web type,
route and production-build checks before release.
