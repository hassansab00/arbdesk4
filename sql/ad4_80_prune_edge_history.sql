-- ===========================================================================
-- ad4_80_prune_edge_history.sql - 121,644 SUPERSEDED PRICINGS NOBODY READS.
--
-- edges is the fourth-largest table on the desk - 136,184 rows, 38 MB, growing
-- 2.36 MB a day - and the largest one the archive has never covered.
--
-- IT IS AN APPEND-ONLY LOG, NOT A STATE TABLE. edge_engine writes one row per
-- band, per side, per intraday run, and the run happens every four hours. A
-- band that has been on the board for a fortnight therefore carries about
-- eighty rows of which exactly one is current.
--
--     136,184 rows        14,540 are the newest for their band and side
--                        121,644 have been superseded
--
-- WHO ACTUALLY READS IT, enumerated rather than assumed, because reading the
-- wrong set is what broke the resolution archive for three days. Nothing in
-- Python or the web app touches the table: scripts/edge_engine.py is the only
-- writer and there is no reader. Every consumer goes through a view:
--
--   v_latest_edge         distinct on (band_id, side) order by computed_at
--                         desc - the newest row per band and side
--   v_opportunities       built on v_latest_edge
--   ad4_40 live_edges     v_latest_edge, bounded to twelve hours
--   phase1c readiness     max(computed_at) per band, and a twelve-hour filter
--
-- Every one of those is satisfied by the newest row per band and side. The
-- backward-looking findings read fact_band_outcome and band_probabilities,
-- which freeze what the desk showed at the time and are not touched here - so
-- pruning superseded pricings cannot move a settled comparison.
--
-- WHAT IS KEPT, therefore:
--
--   * the newest row of every band and side, whatever its age, which is what
--     v_latest_edge reads and what every page is built on
--   * every row inside p_keep_days at full resolution, which covers the
--     twelve-hour freshness windows many times over
--
-- AND NOTHING LEAVES WITHOUT BEING ARCHIVED FIRST. The view below is what
-- scripts/archive_observations.py exports and what this function deletes -
-- the same set, by construction, which is the contract that makes the count
-- check meaningful.
--
-- RUN ORDER: after sql/ad4_phase2.sql, which creates edges and v_latest_edge.
-- Re-runnable.
-- ===========================================================================

create or replace view v_prunable_edge_history as
with ranked as (
  select e.edge_id,
         e.band_id,
         e.side,
         e.computed_at,
         -- Newest first within the band AND SIDE, because YES and NO are
         -- priced separately and v_latest_edge keeps one of each. Ranking by
         -- band alone would offer up the newest NO row of every band.
         row_number() over (partition by e.band_id, e.side
                            order by e.computed_at desc) as rn
    from public.edges e
)
select s.*
  from public.edges s
  join ranked r on r.edge_id = s.edge_id
 where r.rn > 1;

comment on view v_prunable_edge_history is
  'Edge rows that are not the newest pricing of their band and side. v_latest_edge takes exactly one row per band and side, every page is built on it, and nothing reads the table directly - so nothing here is read by a live caller. Age is applied by the caller.';

grant select on v_prunable_edge_history to service_role;


create or replace function public.prune_edge_history(
  p_keep_days     integer     default 14,
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
  v_pairs  bigint;
begin
  -- TWO DAYS IS THE FLOOR. The longest window any live reader asks for is the
  -- twelve hours the readiness views and ad4_40 use; two days is four times
  -- that, and going under it would start deleting rows a page is still
  -- allowed to ask for.
  if p_keep_days < 2 then
    return jsonb_build_object('ok', false,
      'error', 'keep_days must be at least 2 - the readiness views read a twelve-hour window');
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object('ok', false,
      'error', 'p_expected_rows is required for a committed prune - it is the count '
               'verified by re-downloading the uploaded archive');
  end if;

  if not p_dry_run then
    lock table public.edges in share row exclusive mode;
  end if;

  select count(*) into v_doomed
    from public.v_prunable_edge_history where computed_at < v_before;

  if p_expected_rows is not null and v_doomed <> p_expected_rows then
    return jsonb_build_object('ok', false,
      'error', format('archive row count mismatch: verified %s rows but prune would delete %s',
                      p_expected_rows, v_doomed),
      'expected_rows', p_expected_rows, 'would_delete', v_doomed);
  end if;

  if v_doomed = 0 then
    return jsonb_build_object('ok', true, 'deleted', 0,
      'note', format('no superseded pricings older than %s', v_before));
  end if;

  -- The number that says the guard works: band-sides still priced after this
  -- runs. It must not change, because every band and side keeps its newest
  -- row - and if it ever does, a page just lost a price.
  select count(*) into v_pairs from public.v_latest_edge;

  if p_dry_run then
    return jsonb_build_object('ok', true, 'dry_run', true,
      'would_delete', v_doomed, 'rows_now', (select count(*) from public.edges),
      'band_sides_priced', v_pairs, 'older_than', v_before,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete');
  end if;

  perform set_config('arbdesk.archiving', 'edges', true);
  delete from public.edges e
   where e.computed_at < v_before
     and exists (select 1 from public.v_prunable_edge_history p
                  where p.edge_id = e.edge_id);
  perform set_config('arbdesk.archiving', '', true);

  return jsonb_build_object('ok', true, 'deleted', v_doomed,
    'rows_now', (select count(*) from public.edges),
    'band_sides_priced_before', v_pairs,
    'band_sides_priced_after', (select count(*) from public.v_latest_edge),
    'older_than', v_before, 'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.edges')),
    'note', 'ad4_reclaim_edges returns the space on Monday');
end;
$fn$;

comment on function public.prune_edge_history(integer, boolean, timestamptz, bigint) is
  'Remove edge rows that are not the newest pricing of their band and side, and only after scripts/archive_observations.py has uploaded them to a Release and counted them back. v_latest_edge keeps exactly one row per band and side, so the count it returns cannot change.';

revoke all on function public.prune_edge_history(integer, boolean, timestamptz, bigint) from public;
grant execute on function public.prune_edge_history(integer, boolean, timestamptz, bigint) to service_role;
