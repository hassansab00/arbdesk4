-- ===========================================================================
-- ad4_79_prune_book_redundancy.sql - THE 87% OF THE BOOK NOBODY READS TWICE.
--
-- book_snapshots is the largest table on the desk - 204,322 rows, 78 MB,
-- growing 7,038 rows and 2.76 MB a day - and it is the ONLY large table the
-- archive has never covered. ad4_prune_dead_book_detail already strips the
-- LADDERS hourly; the rows themselves have never been touched.
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
-- for that reader, 177,809 of 204,322 rows are intra-day duplicates: 87%.
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
-- check meaningful. The rows go to a GitHub Release, are re-downloaded and
-- counted, and only then removed. Reading a different set on each side is
-- exactly the bug that broke the resolution archive for three days.
--
-- RUN ORDER: after ad4_00_preflight.sql and ad4_prune_dead_book_detail.sql.
-- Re-runnable.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- WHAT THE ARCHIVE READS AND THE PRUNE DELETES - one definition, both halves.
-- --------------------------------------------------------------------------
create or replace view v_prunable_book_redundancy as
with ranked as (
  select b.snapshot_id,
         b.band_id,
         b.observed_at,
         -- Newest first WITHIN the band-day: rank 1 is that day's closing
         -- book, which is the one the backtest would be handed for any as_of
         -- at or after it.
         row_number() over (partition by b.band_id, (b.observed_at at time zone 'UTC')::date
                            order by b.observed_at desc) as rn_in_day,
         -- And newest first across the whole band, because v_latest_book takes
         -- the newest snapshot per band regardless of age.
         row_number() over (partition by b.band_id
                            order by b.observed_at desc) as rn_in_band
    from public.book_snapshots b
)
select s.*
  from public.book_snapshots s
  join ranked r on r.snapshot_id = s.snapshot_id
 where r.rn_in_day  > 1      -- keep each band-day's closing book
   and r.rn_in_band > 1;     -- and never the newest snapshot of a band

comment on view v_prunable_book_redundancy is
  'Intra-day book snapshots that are not the closing book of their band-day and not the newest snapshot of their band. The backtest reads one book per band per as_of and v_latest_book reads the newest per band, so nothing here is read by either. Age is applied by the caller.';

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
as $fn$
declare
  v_before timestamptz := coalesce(p_before, now() - make_interval(days => p_keep_days));
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

  select count(*) into v_doomed
    from public.v_prunable_book_redundancy where observed_at < v_before;

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
  -- book - and if it ever does, the backtest window just moved.
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
   where s.observed_at < v_before
     and exists (select 1 from public.v_prunable_book_redundancy p
                  where p.snapshot_id = s.snapshot_id);
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
  'Remove intra-day book snapshots that are neither their band-day''s closing book nor their band''s newest, and only after scripts/archive_observations.py has uploaded them to a Release and counted them back. Every band-day keeps a row, so min(observed_at) and the backtest window do not move.';

revoke all on function public.prune_book_redundancy(integer, boolean, timestamptz, bigint) from public;
grant execute on function public.prune_book_redundancy(integer, boolean, timestamptz, bigint) to service_role;
