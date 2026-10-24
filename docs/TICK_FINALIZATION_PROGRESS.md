# Tick finalization allowance — P6.1

An acceptance run froze its original predictions successfully but exhausted the
tick allowance before the paper engine and paired variant captures could run.
The pricing pool had retained only eight seconds for books and writes. Its
waiting limit now retains a bounded fraction of the tick for finalization,
capped at 80 seconds, instead of consuming the downstream stages' time.

The checkpoint allowance is capped at 210 seconds, within a 220-second workflow
deadline and ten-minute job backstop. These are configured operational limits,
not accuracy parameters or measured runtimes. Existing collector caps and the
separate shadow timeout retain their bounds. Dispatch cadence, local capture
windows, source proof, immutable freeze guards, model weights and allocation
are unchanged. The engine still cannot spend the paired-capture reserve.

The receipt now reports pricing completion, book-fetch time and immutable-write
time separately. Deferred work and lost coverage remain attention states; a
completed workflow does not erase an internal warning. No historical prediction
or missed experimental capture is reconstructed to conceal the gap.

Regression checks cover bounded pool waits, expired budgets, slow finalization
with time left for the downstream stages, and the workflow backstop. Full local
suites, hosted CI and a deployed run are the release acceptance checks.
