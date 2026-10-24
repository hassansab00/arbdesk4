# AWC routine-METAR authority

The final-weather collector recognizes saved contracts naming AWC's raw METAR
endpoint. The contract's range clause determines the unit independently of its
Celsius parsing instructions. Routine reports include unlabeled and off-hour
reports; SPECI is excluded. An exact eight-digit T remark supplies tenths Celsius,
otherwise the report body's whole Celsius temperature governs. Station-local
dates, exact half-up rounding toward the higher integer, source identity and the
next-day routine-publication/deadline gate are checked before evidence is written.
Conflicting reports for one observation time are refused. Canonical raw-source
and publication-check responses preserve provenance. Only a successful primary
read with no qualifying temperatures can reach the named WU fallback at its
deadline; a local failure cannot establish missing publication. No temperature
is invented for a lowest-bracket venue payout. Existing WRH contracts, finalized
weather evidence, frozen predictions and venue-based scoring keep their behavior.
Focused contract and weather-recovery checks, the full Python suite and database
contracts passed. Live read-only AWC collection was checked after station-local
day-end. The collector passed hosted CI and was merged. Deployment acceptance
captured new-authority evidence; a request-budget boundary identified during
that acceptance is addressed by explicitly deferring unfinished work.

Prediction inputs now keep receipt-stamped AWC snapshots separately from IEM
weather. Qualifying routine reports govern contract floors and the remaining-day
maximum; all reports remain available as weather/trend features. Missing, stale,
future or rule-mismatched authority snapshots cannot inherit an all-report floor.
Frozen vectors retain the authority receipt and digest. Later corrections append
new snapshots without changing earlier as-of inputs. Existing archive and mirror
mechanisms retain this additional capture kind. Per-city calibration and model
selection separate the new input-rule version from the original one and retain
their sample, forward-validation and bounded-update gates. Model artifacts are
not refitted by this compatibility update. Forward accuracy must be measured on
subsequent venue-settled frozen calls; source compatibility alone is not evidence
of improved accuracy.

Contract source windows can include padding after local midnight. The parser
accepts bounded raw-METAR windows only when they cover the entire station-local
day, and still excludes every reading outside that day. Publication checks start
from local day-end independently of the source URL's padded anchor.

The existing edge calculation adds a bounded recovery pass for missing or stale
books on priceable markets. It batches public CLOB requests, checks token and
condition identity, retries only missing tokens, and appends full YES/NO depth
without replacing previous snapshots. The edge reader reloads acknowledged rows
so generated snapshot identities remain attached to decisions. The normal book
collector and schedules stay active. The pipeline panel reads the recovery
receipt separately; incomplete recoveries remain attention states.
