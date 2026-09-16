-- ===========================================================================
-- ad4_67_prune_exported_paper_trades.sql
--
-- Safe to run any time. Creates one function. Deletes nothing by itself -
-- p_dry_run defaults to true and the caller must ask twice.
--
-- scripts/export_paper_trades.py writes every CLOSED paper trade into
-- web/public/paper-trades/YYYY-MM.jsonl, in the repository, where it is
-- versioned and free and survives the database. Postgres then only needs to
-- carry open positions and a short tail of recent closes.
--
-- WHY THE CALLER MUST NAME THE IDS. A date cutoff alone would delete whatever
-- happens to be older than it, including a trade whose export failed. The
-- script re-reads the files it just wrote and passes back the ids it can
-- actually see on disk; this function deletes the INTERSECTION of that list
-- and the cutoff, and nothing else. A file that failed to write therefore
-- cannot become a delete - the same contract prune_observations uses, in the
-- direction that matters.
--
-- An open trade is never eligible, at any age. closed_at is null while a
-- position is live and that row is still changing.
-- ===========================================================================

create or replace function public.prune_exported_paper_trades(
  p_keep_days integer,
  p_trade_ids uuid[],
  p_dry_run   boolean default true
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $function$
declare
  v_before timestamptz := now() - make_interval(days => p_keep_days);
  v_eligible bigint;
  v_offered bigint := coalesce(array_length(p_trade_ids, 1), 0);
  v_matched bigint;
  v_deleted bigint;
begin
  if p_keep_days < 1 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 1 - the page reads recent closes from Postgres'
    );
  end if;

  -- Everything the cutoff alone would have taken...
  select count(*) into v_eligible
    from public.paper_trades
   where closed_at is not null and closed_at < v_before;

  -- ...versus what the caller can prove is on disk.
  select count(*) into v_matched
    from public.paper_trades
   where closed_at is not null and closed_at < v_before
     and trade_id = any(p_trade_ids);

  if p_dry_run then
    return jsonb_build_object(
      'ok', true, 'dry_run', true,
      'older_than', v_before,
      'eligible', v_eligible,
      'offered', v_offered,
      'would_delete', v_matched,
      'unexported', v_eligible - v_matched,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.paper_trades
   where closed_at is not null and closed_at < v_before
     and trade_id = any(p_trade_ids);
  get diagnostics v_deleted = row_count;

  return jsonb_build_object(
    'ok', true,
    'deleted', v_deleted,
    'older_than', v_before,
    'eligible', v_eligible,
    'offered', v_offered,
    'unexported', v_eligible - v_deleted
  );
end;
$function$;

grant execute on function public.prune_exported_paper_trades(integer, uuid[], boolean) to service_role;

comment on function public.prune_exported_paper_trades(integer, uuid[], boolean) is
  'Delete closed paper trades older than the cutoff, restricted to ids the caller has verified are present in the repository export. Open trades are never eligible.';
