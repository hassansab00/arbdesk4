-- ===========================================================================
-- THE WIDE TRADE INDEX GOES (WXPredict build 2.A group C, after
-- 20261008170000).
--
-- ad4_uq_trade_dedupe held the five columns of a print's dedupe key, 19 MB on
-- 88,746 prints (8 Oct 14:52Z). ad4_uq_trade_dedupe_hash holds the same key as
-- 16 bytes (trade_dedupe_key), and turns away exactly the same prints: on the
-- live table, 88,746 distinct five-column keys and 88,746 distinct hashes,
-- none shared.
--
-- RUN IT ONLY ONCE THE INGEST THAT CALLS insert_trade_prints() IS ON MAIN. The
-- ingest before it names the five columns in on_conflict, and without this
-- index every one of its inserts would fail (42P10, the failure ad4_53
-- fixed). Re-runnable.
-- ===========================================================================

drop index if exists public.ad4_uq_trade_dedupe;
