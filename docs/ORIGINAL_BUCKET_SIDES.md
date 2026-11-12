# Original predicted bucket: YES and NO paper v2

Implementation branch; deployment and live acceptance must be reported separately.
This feature does not change forecasts, frozen probability ladders, calibration,
model weights, existing v1 configuration, or historical paper rows.

The immutable checkpoint (or proven original pre-midnight receipt) identifies
both sides of the same original band. Native D−1 09:00 `day_ahead`, D−1 18:00
`d1_eve`, and the legacy last-pre-midnight receipt remain different cohorts.
History adapters require the exact original timestamp, band and checkpoint;
they never borrow a quote or fill using city/date alone. The old accuracy
aggregations remain intact. A NO win never changes a prediction miss to a hit.

`original_side_paper.py` runs within the existing Tick. Observation attempts
are independent of policy activation. Each side needs its own token, book,
current rule receipt matching the freeze, fees and minimum/precision evidence.
The venue SDK size profiles are dated; unknown profiles fail closed. The new
path uses the book's minimum shares rather than relabeling Gamma's minimum as
dollars. Existing v1 recorded fills are displayed with their original terms.

The existing public-token reader and Decimal IOC simulator are reused. New
paper accounts, decisions, allocations, execution receipts and financial
ledger are private v2 sleeves because legacy mirror/read paths publish paper
rows. They are integrated into the existing Day Max panel and paper page via
an owner-authorized read boundary, never copied into the public mirror or
static trade exports. Existing v1 YES/$10 guards remain; its dedicated reader preserves the original last-pre-midnight cohort.

Saving the mixed preset is inactive and creates no contribution or account.
Explicit activation freezes its selection and effective time and funds exactly
one $100 paper account. There are no top-ups. Dated equal allocations reserve
settled available cash for every configured slot before execution. Failed
execution cannot reuse another city's reserve. Unused unexecuted slots expire
after that city's local target day with an append-only release receipt.

The same numeric server calculation sizes quote comparisons and decisions.
Actual fills use the existing simulator and are rechecked against their direct
private receipt, depth, tick, share step, fee arithmetic, lease and cash ceiling.
A fill links its own post-order quote attempt, including when the captured book
content is unchanged from a pre-order observation. A captured empty book remains
retryable; comparisons select the first executable side quote for their stake
without deleting earlier failed or insufficient-depth attempts.
Actual recorded P&L and independent hypothetical comparisons have separate
fields. Missing evidence and unresolved positions remain null. Account P&L
includes only resolved recorded positions; pending costs are not labeled losses.

Settlement packets verify original condition/token identity, current original
rule hash and venue finality. Revisions append receipts, reconcile cash once
and can withdraw a disputed payout. Split/void evidence without an independently
verified actual payout remains unresolved; it is never treated as the inverse
of an original hit. No fabricated settlement is used for live acceptance.

The optional research-only derived NO helper uses a saved YES **bid**, defaults
off, records its assumptions, and cannot pass any execution gate. There is no
derived-price fill path.

The private archive freezes immutable membership and mutable projections in
one MVCC snapshot, then reads only those immutable IDs in bounded pages.
`archive_original_sides.py` writes a private bundle, verifies hashes, and runs
`restore_original_sides.cjs` in isolated Postgres with `EXCEPT ALL` in both
directions and checks account cash/reserves against the immutable ledger. It has explicit membership, page and size limits and performs no
retention action. Ephemeral access credentials are reissued after restoration.

Acceptance distinguishes source, schema, production UI, new side capture,
recorded fills and later verified settlement/P&L. The mixed preset must remain
inactive unless explicitly activated prospectively. Source tests and a green
workflow do not establish a live fill, payout or predictive improvement.

Normalized rule/ladder receipts and one raw depth packet per book hash bound
repeated evidence. Each retry retains its own source and server receipt clocks.
A 16 MiB limit for these new private tables holds further capture before it
expands without bound; no observations are deleted. Existing queued execution
and independent settlement recovery remain available. The limit does not
change the existing Supabase plan or repair pre-existing quota usage.

Native and original legacy inserts retain call identity in the freeze
transaction. A metadata registration failure cannot abort the forecast and
is retained as an independent stage failure. Historical links use only exact
existing immutable source receipts; no historical quote or fill is created.

Fractional payouts support only the explicit legacy Polygon CTF derivation.
Two allowlisted read-only RPC providers must agree on one block at least 64
blocks behind both heads, original condition/collateral/collection/position
IDs, denominator and numerators. Protocol V2 or any unknown identity remains
unresolved until its own supported payout evidence exists. Binary outcomes
require the matching original-token verdict from both venue responses.
