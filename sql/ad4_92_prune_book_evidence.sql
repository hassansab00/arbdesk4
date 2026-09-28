-- ===========================================================================
-- ad4_92_prune_book_evidence.sql - BOOK PROOF GOES TO THE REPOSITORY
-- (plan v2 P1.6 phase 1, step 2, 28 Sep)
--
-- Safe to run any time. Creates one function. Deletes nothing by itself -
-- p_dry_run defaults to true and the caller must ask twice.
--
-- Hassan, 28 Sep: "YES APPROVED as long as we dont lose any collected data".
--
-- paper_book_evidence holds the order book behind every paper fill: the whole
-- raw book and the market's metadata, ~6.4 KB a row, ~340 rows a day, 13 MB
-- (2,100 rows since 15 Sep, measured 28 Sep). Nothing archived it.
--
-- WHO READS IT, and for how long (checked 28 Sep, code and live database):
--   complete_paper_order      the order's own snapshot, and only if it is no
--                             older than the order's max_book_age_seconds
--                             (default 120, never more than 900)
--   queue_automatic_paper_exit  proof no older than 120 s
--   v_data_freshness          the newest row
-- No foreign key points at it; paper_orders carry the snapshot_id (a sha256
-- of the book itself) in their result, and scripts/paper_worker.py reads
-- those orders, not this table. So a proof a day old is read by nothing - it
-- is evidence at rest, and it rests in the repository after this.
--
-- The table is append-only (arbdesk_private.immutable_record): this claims
-- the one exemption that trigger allows, a DELETE from an archive naming this
-- exact table in the transaction-local arbdesk.archiving, after every other
-- guard, around the delete only - as prune_research_captures does.
--
-- Run:
--   select prune_book_evidence(1);                         -- dry run
--   select prune_book_evidence(1, false, <cutoff>, <rows>); -- count-bound
-- scripts/archive_observations.py --table book_evidence calls both halves,
-- the second only after the file is committed and read back from GitHub.
-- ===========================================================================

create or replace function public.prune_book_evidence(
  p_keep_days     integer,
  p_dry_run       boolean     default true,
  p_before        timestamptz default null,
  p_expected_rows bigint      default null
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $function$
declare
  -- A cutoff may be older than the floor, never newer (the rule every prune
  -- here follows since plan v2 P1.1).
  v_before timestamptz := least(coalesce(p_before, now() - make_interval(days => p_keep_days)),
                                now() - make_interval(days => p_keep_days));
  v_doomed bigint;
  v_keep   bigint;
begin
  -- ONE DAY. The longest any reader looks back is 900 seconds.
  if p_keep_days < 1 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 1 - an order may still be completing on this morning''s book'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune - it is the count '
               'read back from the committed archive file'
    );
  end if;

  if not p_dry_run then
    lock table public.paper_book_evidence in share row exclusive mode;
  end if;

  select count(*) into v_doomed
    from public.paper_book_evidence where captured_at < v_before;

  if p_expected_rows is not null and v_doomed <> p_expected_rows then
    return jsonb_build_object(
      'ok', false,
      'error', format('archive row count mismatch: verified %s rows but prune would delete %s',
                      p_expected_rows, v_doomed),
      'expected_rows', p_expected_rows,
      'would_delete', v_doomed
    );
  end if;

  if v_doomed = 0 then
    return jsonb_build_object('ok', true, 'deleted', 0,
                              'note', format('nothing older than %s', v_before));
  end if;

  select count(*) into v_keep
    from public.paper_book_evidence where captured_at >= v_before;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true, 'dry_run', true,
      'would_delete', v_doomed,
      'would_keep', v_keep,
      'older_than', v_before,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  -- Claimed here, and only here; is_local, so it ends with the transaction.
  perform set_config('arbdesk.archiving', 'paper_book_evidence', true);
  delete from public.paper_book_evidence where captured_at < v_before;
  perform set_config('arbdesk.archiving', '', true);

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'kept', v_keep,
    'older_than', v_before,
    'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.paper_book_evidence')),
    'note', 'request_reclaim returns the space the same night'
  );
end;
$function$;

-- SECURITY DEFINER and deletes rows: service_role only (plan v2 P1.1).
revoke execute on function public.prune_book_evidence(integer, boolean, timestamptz, bigint) from public, anon, authenticated;
grant execute on function public.prune_book_evidence(integer, boolean, timestamptz, bigint) to service_role;

comment on function public.prune_book_evidence(integer, boolean, timestamptz, bigint) is
  'Delete paper_book_evidence rows older than the cutoff (at least a day; no reader looks back more than 900 s), only when the caller''s count read back from the committed archive file matches exactly, by claiming the append-only guard''s single-table archive exemption around the delete.';
