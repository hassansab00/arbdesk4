-- ===========================================================================
-- CAPTURE REVISIONS, NOT COPIES (plan v2 P1.6, 24 Sep)
--
-- preserve_research_output_on_insert copied every NEW band_probabilities row
-- into research_captures. That row already exists twice: in band_probabilities
-- itself, which nothing prunes, and in the nightly repository mirror (P1.7,
-- data/mirror/band_probabilities, 74,056 rows through 24 Sep 00:00Z). The
-- third copy was ~1,000 rows a run and, until skip_capture, the whole 22 Sep
-- backfill burst: 72,505 of the 115,913 rows in research_captures on 24 Sep.
--
-- What research_captures alone can hold is a REVISION: a price changed in
-- place, which neither the table (it keeps the new value) nor an append mirror
-- (it exported the old day) records. preserve_research_output_on_reprice keeps
-- doing exactly that and is not touched. Nothing reads research_captures back
-- (checked 24 Sep: only its prune, freshness, integrity and archive code).
--
-- Idempotent.
-- ===========================================================================
do $$
begin
  if to_regclass('public.band_probabilities') is null then return; end if;
  drop trigger if exists preserve_research_output_on_insert on public.band_probabilities;
end $$;
