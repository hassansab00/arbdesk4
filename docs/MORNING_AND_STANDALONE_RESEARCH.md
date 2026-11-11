# Morning evidence and standalone shadow recipe

Morning capture reads the existing receipt-stamped `models0` snapshots once
for due morning cities, within the existing local-day clock and an eight-second
read deadline. It uses one latest received complete snapshot, all expected UTC
hour samples inside the city-local target day, and all seven original models.
DST and fractional-offset clocks use local-day bounds. Missing hours/models,
future receipts and stale data remain gaps. No data are filled from another
snapshot or reconstructed after the outcome.

The packet retains source names, raw local-day maxima, original snapshot ID,
receipt times, original-payload hash, and request/response locations. Raw current
model forecasts have a separate family from station-corrected early forecasts.
Their labels, states and source weights cannot silently mix. A source packet
never changes the served forecast; ordinary chronology, station/rule receipts,
market comparison, minimum dates and proper-score gates still apply.

`current-city-standalone-v3` is the next **research-only** current-forecast recipe.
It uses the existing classifier and features with its own normalized output,
without forcing 90% of an incumbent distribution back into it. This is a new
prospective recipe, not a regrade or replacement of a fixed experiment.
Existing v1/v2 artifacts retain their registered 10% blend. New artifacts bind
recipe and blend together; mismatched metadata is refused. Legacy probability
bounds remain the default for legacy assessment. The new shadow recipe declares
its full-probability output domain explicitly, while retaining all sample,
coverage, incumbent, market, Brier, bucket-hit and date-clustered log-loss gates.

The nightly workflow first requires sufficient original training and separate
later evaluation dates before fitting/registering an eligible candidate. The
first fixed forward window is still scored only on original frozen shadow
calls. Held/rejected results never imply production improvement. There is no
production promotion, allocation change, schema change, new collector or new
scheduled job in this implementation. Only the isolated shadow path consumes
the new artifact. Further deployment acceptance requires normal scheduled
capture rows and later newly settled forward evidence.

Detailed research metrics are private and are not part of this source change.
