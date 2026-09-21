-- ===========================================================================
-- ad4_79_prune_book_redundancy.sql - THE 87% OF THE BOOK NOBODY READS TWICE.
--
-- book_snapshots is the largest table on the desk - 223,099 rows, 91 MB - and
-- it is the ONLY large table the archive has never covered.
-- ad4_prune_dead_book_detail already strips the LADDERS hourly; the rows
-- themselves have never been touched.
--
-- WHY IT COULD NOT SIMPLY BE PRUNED BY AGE, like the other five. Two things
-- read it historically and both would lose capability:
--
--   scripts/backtest/runner.py  asks for the newest snapshot per band at or
--                               before an arbitrary as_of, one per band
--   v_backtest_window           bounds every backtest by min(observed_at),
--                               so pruning by age SHORTENS what can be
--                               backtested, silently
--
-- A ninety-day window would archive nothing (the table is 29 days old) and a
-- short one would quietly take the backtest's history away. Neither is right.
--
-- WHAT IS ACTUALLY REDUNDANT, measured rather than assumed. The collector
-- writes about 7.7 snapshots per band per day. The backtest consumes exactly
-- ONE of them per band per as_of - the newest at or before that moment. So
-- for that reader, the overwhelming majority of rows are intra-day
-- duplicates.
--
-- This archives and removes those duplicates BEYOND A FULL-RESOLUTION WINDOW,
-- and keeps the last snapshot of each band-day forever. What survives:
--
--   * every band-day that ever existed, at its closing book - so
--     min(observed_at) does not move and the backtest window does not shrink
--   * the newest snapshot per band, whatever its age, which v_latest_book
--     reads
--   * every row inside p_keep_full_days at full intra-day resolution, which
--     is what v_band_price_history draws the monitor's chart from
--
-- So the backtest loses nothing it reads, the chart loses nothing it draws,
-- and the row that proves a band existed on a day is still there.
--
-- AND NOTHING LEAVES WITHOUT BEING ARCHIVED FIRST. The view below is what
-- scripts/archive_observations.py exports and what this function deletes -
-- the same set, by construction, which is the contract that makes the count
-- check meaningful.
--
--
-- 21 SEP: IT COULD NEVER HAVE RUN, AND THE REASON IS NOT IN THIS FILE.
--
-- The first scheduled run that included `books` died with
--
--     prune_book_redundancy -> HTTP 500: {"code":"57014",
--      "message":"canceling statement due to statement timeout"}
--
-- PostgREST connects as `authenticator`, which carries statement_timeout=8s;
-- service_role has no rolconfig of its own, so every RPC on this desk gets
-- EIGHT SECONDS. The old shape of this file needed far more than that:
--
--   * counting the doomed rows through the old view took 16.8s on its own,
--     measured - twice the whole budget, before anything was deleted
--   * and the function evaluated that view FOUR TIMES: the dry-run count,
--     the committed count, the DELETE's correlated EXISTS, and again for the
--     band-day totals either side
--
-- The python caller passes timeout=600 to requests, which is why this looked
-- survivable. That number is the HTTP client's patience. It has nothing to
-- do with what Postgres will allow, and the request never reached 600s -
-- Postgres killed it at 8.
--
-- So: the doomed set is computed ONCE into a temp table and everything
-- downstream reads that, and the function carries its own statement_timeout
-- rather than inheriting a web request's.
--
--
-- AND THE RANKING WAS NOT DETERMINISTIC, which is worse than slow.
--
-- The old view ranked with `order by observed_at desc` alone. 3,177 pairs of
-- rows in this table share a band_id AND an observed_at to the microsecond,
-- so row_number() picked between them arbitrarily - and it picked SEPARATELY
-- in the two window functions, and separately again on every re-evaluation.
-- Measured: the old view returned 53,915 rows where the deterministic one
-- returns 54,222, the 307-row difference being tied rows that one window
-- called the day's closing book while the other did not.
--
-- The archive reads the view over many paginated requests and the prune
-- counts it again afterwards. A set that can answer differently each time it
-- is asked is precisely the "verified N rows but prune would delete M"
-- failure that broke the resolution archive for three days. Adding
-- snapshot_id to the ordering makes the set a function of the data alone.
--
-- With a total order the second window is also redundant, provably: a row
-- with a strictly greater (observed_at, snapshot_id) in the same band-day is
-- a strictly greater row in the same band, so anything that is not its
-- band-day's closing book is not its band's newest either. Checked against
-- the live table before this was relied on - the two-window form and the
-- single EXISTS below return the same 54,222 rows, with zero difference in
-- either direction.
--
-- RUN ORDER: after ad4_00_preflight.sql and ad4_prune_dead_book_detail.sql.
-- Re-runnable.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- WHAT THE ARCHIVE READS AND THE PRUNE DELETES - one definition, both halves.
--
-- A row is redundant when a LATER row exists for the same band on the same
-- UTC day. That later row is what any reader asking for "the book as of the
-- end of this day" would be handed, and it is also newer than this row
-- band-wide, so neither the backtest nor v_latest_book can reach this one.
--
-- The upper bound is written as a half-open range on observed_at rather than
-- as a date() equality so the (band_id, observed_at desc) index can bound the
-- scan instead of filtering every row of the band.
-- --------------------------------------------------------------------------
create or replace view v_prunable_book_redundancy as
select s.*
  from public.book_snapshots s
 where exists (
   select 1
     from public.book_snapshots k
    where k.band_id = s.band_id
      and k.observed_at >= s.observed_at
      and k.observed_at < ((((s.observed_at at time zone 'UTC')::date + 1)::timestamp)
                             at time zone 'UTC')
      -- The tie-break. Equal timestamps are common here (3,177 pairs), and
      -- without this a tied pair is either both-redundant or neither.
      and (k.observed_at, k.snapshot_id) > (s.observed_at, s.snapshot_id));

comment on view v_prunable_book_redundancy is
  'Book snapshots that are not the closing book of their band-day. The backtest reads one book per band per as_of and v_latest_book reads the newest per band, so nothing here is read by either. Ordered by (observed_at, snapshot_id) so the set does not change between evaluations. Age is applied by the caller.';

grant select on v_prunable_book_redundancy to service_role;


create or replace function public.prune_book_redundancy(
  p_keep_days     integer     default 7,
  p_dry_run       boolean     default true,
  p_before        timestamptz default null,
  p_expected_rows bigint      default null
) returns jsonb
language plpgsql
security definer
set search_path to 'public', 'pg_temp'
-- EIGHT SECONDS IS A WEB REQUEST'S BUDGET, NOT A PRUNE'S. Inherited from
-- `authenticator` through PostgREST; see the header. The work below is
-- bounded and measured in single-digit seconds, so this is headroom for a
-- table that keeps growing, not licence to run unboundedly long.
set statement_timeout to '120s'
as $fn$
declare
  v_before timestamptz := coalesce(p_before, now() - make_interval(days => p_keep_days));
  v_ids    bigint[];
  v_doomed bigint;
  v_keep   bigint;
  v_days   bigint;
begin
  -- THREE DAYS IS THE FLOOR. v_band_price_history draws the monitor's chart
  -- from intra-day rows, and a window shorter than the chart's makes the
  -- chart sparse for no storage worth having.
  if p_keep_days < 3 then
    return jsonb_build_object('ok', false,
      'error', 'keep_days must be at least 3 - the price-history chart reads intra-day rows');
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object('ok', false,
      'error', 'p_expected_rows is required for a committed prune - it is the count '
               'verified by re-downloading the uploaded archive');
  end if;

  if not p_dry_run then
    lock table public.book_snapshots in share row exclusive mode;
  end if;

  -- ONCE, AND HELD. The count returned, the count compared against the
  -- archive, and the rows deleted are all this one array - so the set that
  -- was counted IS the set that is deleted, rather than two evaluations of a
  -- query that ought to agree. At the current size this is 54,222 bigints,
  -- about 430 kB; a temp table would be the other way to hold it, but
  -- plpgsql caches plans by OID and PostgREST reuses backends, so the second
  -- call in a pooled session would fail on a dropped relation.
  select array_agg(s.snapshot_id)
    into v_ids
    from public.v_prunable_book_redundancy s
   where s.observed_at < v_before;

  v_doomed := coalesce(array_length(v_ids, 1), 0);

  if p_expected_rows is not null and v_doomed <> p_expected_rows then
    return jsonb_build_object('ok', false,
      'error', format('archive row count mismatch: verified %s rows but prune would delete %s',
                      p_expected_rows, v_doomed),
      'expected_rows', p_expected_rows, 'would_delete', v_doomed);
  end if;

  if v_doomed = 0 then
    return jsonb_build_object('ok', true, 'deleted', 0,
      'note', format('no intra-day redundancy older than %s', v_before));
  end if;

  select count(*) into v_keep from public.book_snapshots;
  -- The number that says the guard works: band-days still represented after
  -- this runs. It must not change, because every band-day keeps its closing
  -- book - and if it ever does, the backtest window just moved. It is counted
  -- on both sides rather than asserted, which is the entire point of it.
  select count(distinct (band_id, (observed_at at time zone 'UTC')::date))
    into v_days from public.book_snapshots;

  if p_dry_run then
    return jsonb_build_object('ok', true, 'dry_run', true,
      'would_delete', v_doomed, 'rows_now', v_keep, 'band_days', v_days,
      'older_than', v_before, 'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete');
  end if;

  perform set_config('arbdesk.archiving', 'book_snapshots', true);
  delete from public.book_snapshots s
   where s.snapshot_id = any(v_ids);
  perform set_config('arbdesk.archiving', '', true);

  return jsonb_build_object('ok', true, 'deleted', v_doomed,
    'rows_now', (select count(*) from public.book_snapshots),
    'band_days_before', v_days,
    'band_days_after', (select count(distinct (band_id, (observed_at at time zone 'UTC')::date))
                          from public.book_snapshots),
    'older_than', v_before, 'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.book_snapshots')),
    'note', 'ad4_reclaim_book_snapshots returns the space on Monday');
end;
$fn$;

comment on function public.prune_book_redundancy(integer, boolean, timestamptz, bigint) is
  'Remove book snapshots that are not their band-day''s closing book, and only after scripts/archive_observations.py has uploaded them to a Release and counted them back. Every band-day keeps a row, so min(observed_at) and the backtest window do not move.';

revoke all on function public.prune_book_redundancy(integer, boolean, timestamptz, bigint) from public;
grant execute on function public.prune_book_redundancy(integer, boolean, timestamptz, bigint) to service_role;
