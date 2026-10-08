-- ===========================================================================
-- THE PRICE PRUNE FINDS ITS EDGES BY INDEX (WXPredict build 2.A; Hassan,
-- 7 Oct: "proceed to next storage cut").
--
-- The night of 8 Oct was the first with the eighteen-day window
-- (20261006170000), and the archive committed
-- probabilities-2026-09-13-to-2026-09-19.csv.gz, 39,316 rows, then called the
-- prune:
--
--     archive_probabilities  attention  HTTPError: prune_band_probabilities
--                                       -> HTTP 504: upstream request timeout
--
-- Nothing was lost and nothing left: at 03:40Z the dry run still offered
-- exactly 39,316 rows for markets dated before 20 Sep, the file's count.
--
-- THE SAME QUADRATIC DELETE 20260921160000 FIXED FOR book_snapshots.
-- edges.prob_id is a foreign key into band_probabilities with no ON DELETE
-- and no index. Postgres proves no edge cites a price before removing it, so
-- each removed price runs
--
--     select 1 from only public.edges x where prob_id = $1 for key share of x
--
-- and with no index that is a sequential scan of edges: 45,714 rows, 1,088
-- buffers, 9.85 ms measured (explain analyze, 8 Oct 03:41Z). 39,316 of them
-- is about 387 s, against the service role's 180 s statement_timeout. The
-- dry run that counts the same rows took 6.5 s; the scans are the rest.
-- The 22-row prune of 4 Oct needed 22 of them and passed.
--
-- Every other foreign key into a table a prune_* function removes rows from
-- already leads an index (pg_constraint against pg_index, 8 Oct), except
-- ledger.trade_id into paper_trades. prune_exported_paper_trades moves each
-- link into ledger.detail first, and every paper trade it removes is then
-- checked against the ledger the same way. 362 ledger rows today, so no
-- cost yet. Indexed here too, so the rule holds without an exception and
-- tests/database/paper-contracts.cjs asserts it.
--
-- Partial, as 20260921160000's are: only a non-null reference can block a
-- removal, and a strict equality implies the predicate, so the referential
-- check uses it. 42,016 of the 45,714 edges carry a prob_id (8 Oct).
--
-- CONCURRENTLY is not used: a migration runs inside a transaction. Built on
-- production before this was merged; there these are no-ops.
-- ===========================================================================
create index if not exists ad4_ix_edges_prob_id
  on public.edges (prob_id) where prob_id is not null;

create index if not exists ad4_ix_ledger_trade_id
  on public.ledger (trade_id) where trade_id is not null;
