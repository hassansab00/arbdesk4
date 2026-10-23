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
day-end. Hosted CI and deployed acceptance remain pending. Predictive all-report features and hard
floors require a separate contract-aware input update; this collector change
does not claim to have updated those paths or refitted model artifacts.
