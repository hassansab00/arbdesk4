# Day Max Prediction paper experiment

Owner-requested daily forecast-following strategy on its own shadow ledger.
Each configured city has a fixed checkpoint and a ten-dollar fee-inclusive
daily ceiling. The city/date allocation, frozen pick and input provenance are
retained. Repeated runs cannot allocate again, including after a blocked or
unfilled order. No backfilled trades or mutable forecast replacement.

The existing hourly tick calls the bounded runner after checkpoint capture.
No new schedule, forecast request or model fitting is introduced. Existing
paper execution, risk rails, settlement and the verified repository mirror
remain in use. Only public book GETs are made; no exchange orders are sent.

Private runtime configuration records the selected cities, their entry
checkpoints, selection evidence and prospective activation time. The
selection uses only calls received at least an hour before the reviewed high,
at least five settled dates, and the lower confidence bound of hit rate.
Choices remain fixed rather than being retuned against the same experiment.
Small retrospective samples do not establish profitability. This experiment
follows the original top pick even without a positive price edge; the UI says
so. Execution may be smaller or blocked by cash, depth and normal risk rails.

The Paper Trades panel lists city checkpoints and timezones, the original
receipt, execution state, fee-inclusive spend and the last worker's skips.
Before activation it explicitly waits for the next eligible original call.

Validation and live acceptance are recorded in the pull request.
