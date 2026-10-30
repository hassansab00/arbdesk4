# City forecast quality and bounded adaptation

This change closes three validation/freshness gaps and adds prospective early
correction learning to the existing prediction pipeline. It does not establish
an improvement in production accuracy; that requires new, settled forward calls.

- Station combinations must have fresh, ordered input-run timestamps, at least
  four sources and a finite centre. Rewriting the combination cannot refresh old
  underlying weather. Postprocess coefficients expire after three days and must
  satisfy the fitter's finite bias and width bounds.
- An unfittable nested shrinkage fold uses the fixed conservative prior
  `(40, 120)`, never the winner selected with the fold's later outcomes.
- A city/peak-phase calibration that fails fresh validation falls back to the
  identity immediately rather than retaining its last usable parameters.

## Early city correction strength

The engine freezes eleven candidate ladders, with correction strength from zero
(raw centre) to one (the current corrected centre) in increments of 0.1. Each
candidate uses the same original width, probability calibration and observation
floor. Both endpoint forecasts and the selected strength are saved with the
original call; old forecasts are never rebuilt. Promoted-model and trajectory
centres remain outside this comparison.

Evening-before and morning cells are distinct, on each city's clock, including
DST. The recipe identity separates the source model and lead, correction path,
width policy, settlement contract, station and unit. Unknown historical recipes
remain diagnostic evidence only. Morning learning also needs the original saved
peak evidence to place receipt at least an hour before the expected peak.

The prior strength is one. Learning uses a trailing 90-day record, with the
latest 14 days held for validation. A city/horizon/recipe needs at least 20 older
training dates and 10 later validation dates. Training is seasonally weighted
and shrunk toward the prior with 30 pseudodates. Strength can move by at most
0.1 per nightly fit; a rerun uses that night's original anchor. Every candidate
must improve held-out log loss against both prior and incumbent with a positive
90% date-bootstrap lower bound, and must not reduce hit rate or worsen Brier
score. An unchanged incumbent must revalidate; rejection restores the prior.
Expired or incompatible state cannot price a call.

This runs inside the existing daily validation job, using the existing private
calibration table and daily committed evidence artifact. The original live
30-day database reads are unchanged. Older training calls come from existing
repository exports after hash, row-count and coverage verification. A missing
mirror cannot provide approval evidence. Intraday score refreshes do not fit.
The UI exposes the strength, version, readiness counts and reason per city.

## Verification

Regression coverage includes causal label/receipt ordering, first-call selection,
source/contract isolation, city-local DST, unseen-date rejection, same-night step
limits, immediate fallback, raw/calibrated price coherence, floored morning
ladders and verified offload reads. Run the full Python, database, TypeScript,
route and production-build checks before publishing. Production acceptance must
also verify a new ordinary tick and the unchanged historical frozen receipts.

## Historical display refresh

The checkpoint display now reads the immutable banked market scores through
`v_checkpoint_outcome`, including the linked correction for the original book
rule. It computes only the favourite's displayed quote from the saved book.
It no longer expands and regrades every historical bucket on each refresh.
The migration compares every old and new display column with `EXCEPT ALL` in
both directions and refuses a difference. View options and grants stay in
place; calls, labels and banked scores are untouched. This addresses a slow
historical query that could abort the shared page-cache refresh and leave its
health sample overdue, without increasing refresh cadence or timeout limits.

## Prospective rules recovery and early paper entries

Missing future-event rules are recovered only for due city/dates, with bounded
parallel public reads and a private fill-only RPC. The raw initial rules receive
an immutable server receipt and go through the existing repository mirror.
Known text is retained. Unknown or conflicting station contracts do not become
eligible merely because recovery was attempted. The original call keeps the
rules fingerprint and receipt/observation time; old forecasts stay unchanged.

Day Max supports one configured early timing per city: evening-before at 18:00
local or the last complete original pricing ladder before local midnight. The
latter is copied once by the server in the first 75 minutes of the target day,
using only pre-midnight forecast inputs, with no target-day observation floor.
Partial or mixed ladders, prices older than eight hours and captures after the
saved expected peak are refused. This separate retained receipt never inserts
synthetic calls into the global checkpoint history. Execution uses current books
and the existing shadow-ledger risk rails. Existing allocations remain unique
per city/target date even if the configured timing changes. No schedule is added.

Timing choices compare both early horizons on matching settled dates. Legacy
day-ahead scores lack server freeze receipts and remain diagnostic evidence,
not validated learning or profitability evidence. Configuration stays fixed
for prospective paper measurement. The UI lists each selected city's waiting,
blocked, missed-window or execution status for the chosen settlement date.
