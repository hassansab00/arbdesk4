# Original source evidence reaches the nightly learner

The nightly checkpoint projection must include `station` and `inputs_ok`.
The source learner requires the recorded station to match the original source
packet, and a typed true eligibility flag. Omitting these columns silently
discarded otherwise eligible original calls before a source-weight state could
be fitted. The regression exercises the real nightly reader projection,
collector and learner together rather than supplying extra fixture fields that
the actual Data API response would omit.

CSV mirrors preserve eligibility as text. Decode recognized boolean encodings;
retain false and missing values as ineligible. Malformed evidence fails the
history read before any calibration state can be replaced. No missing status
is inferred from the forecast, outcome or later successful call.

Every nightly learning receipt now counts original source packets, eligible
rows and exclusions, including pending labels, missing eligibility, station or
rule mismatches, source-family mismatches and chronology failures. A zero-cell
fit is no longer the only clue that an entire handoff failed.

This change repairs the transport of existing immutable evidence. It adds no
forecast reconstruction, model coefficients, promotion, new scheduled job or
portfolio action. Independent-date, source-weight, proper-score, saved-market
and validation gates remain in force. A held state remains held; capture and
test success do not establish improved predictive accuracy. Acceptance still
requires a later normal learning run and newly settled original evidence.
