-- ===========================================================================
-- THE ARCHIVE'S PRUNES GET EIGHT SECONDS, AND TWO OF THEM NEED MORE.
--
-- PostgREST connects as `authenticator`, which carries statement_timeout=8s.
-- service_role had no entry in pg_db_role_setting at all, so every RPC this
-- desk makes - including the ones that move tens of thousands of rows out of
-- the database - ran on a budget sized for a web request.
--
-- prune_book_redundancy and prune_resolution_evidence both died on it:
--
--     HTTP 500 {"code":"57014","message":"canceling statement due to
--                statement timeout"}
--
-- FOUR TIMES, on four separate dispatches, while the database sat at 112% of
-- its tier and these were the two largest tables on it.
--
-- WHAT DOES NOT WORK, and cost a day finding out: putting
-- `SET statement_timeout` on the FUNCTION. Postgres arms the timeout when the
-- outer statement starts; changing the GUC inside a function that statement
-- called does not re-arm it. The function-level SET is silently inert, and it
-- reads exactly like a fix. Measured: with `set statement_timeout to '120s'`
-- on the function, `set local statement_timeout='8s'; select prune_...()`
-- still dies at 8 seconds.
--
-- What does work is a role setting, which PostgREST reads from
-- pg_db_role_setting and applies per request. anon and authenticated keep
-- their 8 seconds, so nothing the browser can reach gets a longer leash -
-- this widens only the key the archive and the pipeline scripts hold.
--
-- 180s is sized from measurement, not taste. The committed book prune ran
-- 2.6s against a warm cache and 50.6s cold on this instance; the archive
-- always reads the whole prunable set immediately before pruning it, so the
-- warm figure is the realistic one and the cold figure is the headroom.
-- ===========================================================================
alter role service_role set statement_timeout = '180s';
alter role service_role set lock_timeout      = '30s';
notify pgrst, 'reload config';


-- ---------------------------------------------------------------------------
-- AND THE DELETE WAS QUADRATIC, which no timeout would have saved.
--
-- edges.book_snapshot_id and signals.book_snapshot_id are foreign keys into
-- book_snapshots, and NEITHER WAS INDEXED. Postgres must prove no child row
-- references a parent before deleting it, so each of 51,090 deletes triggered
-- a sequential scan of edges - 127,550 rows - for something like 6.5 billion
-- row comparisons. A rolled-back trial of that delete did not finish inside
-- sixty seconds; with these indexes it takes 2.6.
--
-- Partial, because the column is null on most rows and only the non-null ones
-- can block a delete.
--
-- CONCURRENTLY is deliberately NOT used here: this file runs inside a
-- migration transaction, where it is not allowed. The two indexes were built
-- concurrently against production by hand before this was written, so this
-- statement is a no-op there and the definition of record everywhere else.
-- ---------------------------------------------------------------------------
create index if not exists ad4_ix_edges_book_snapshot
  on public.edges (book_snapshot_id) where book_snapshot_id is not null;

create index if not exists ad4_ix_signals_book_snapshot
  on public.signals (book_snapshot_id) where book_snapshot_id is not null;
