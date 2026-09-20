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
  v_orphans bigint;
  v_desks   bigint;
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

  -- THE ROW GOES TO THE REPOSITORY. THE MONEY STAYS HERE.
  --
  -- net_pnl on a closed trade is realised P&L that was already paid into the
  -- desk's cash when it closed. `cash = starting_cash - open basis + realised`
  -- therefore needs that number for as long as the cash exists - which is
  -- forever - while the row itself only needs to live until it is safely in
  -- the export. Deleting it moved one side of the identity and not the other,
  -- so thirty days after the first trade closed, every desk's books would
  -- have gone permanently out of balance and stayed that way.
  --
  -- So the total is written into paper_activity first, in this same
  -- transaction, before anything is deleted. paper_activity is append-only -
  -- immutable_record() refuses UPDATE, DELETE and TRUNCATE - so the summary
  -- cannot be lost the way the rows it replaces can be.
  -- v_paper_desk_integrity adds it back into realised P&L.
  --
  -- cash_delta is zero on purpose: this event moves no money. The money moved
  -- when the trade closed, and the ledger already carries that.
  insert into public.paper_activity (account_id, event_type, payload, cash_delta)
  select t.account_id, 'trades_archived',
         jsonb_build_object(
           'trades',            count(*),
           'realized_pnl',      coalesce(sum(t.net_pnl), 0),
           'trades_without_pnl', count(*) filter (where t.net_pnl is null),
           'older_than',        v_before,
           'oldest_closed_at',  min(t.closed_at),
           'newest_closed_at',  max(t.closed_at),
           'exported_to',       'web/public/paper-trades'),
         0
    from public.paper_trades t
   where t.closed_at is not null and t.closed_at < v_before
     and t.trade_id = any(p_trade_ids)
     and t.account_id is not null
   group by t.account_id;
  get diagnostics v_desks = row_count;

  -- Rows log_paper_trade wrote before the multi-desk engine existed have no
  -- account_id, so there is no desk whose books they belong to and no
  -- activity row to write. They are counted rather than passed over in
  -- silence.
  select count(*) into v_orphans
    from public.paper_trades
   where closed_at is not null and closed_at < v_before
     and trade_id = any(p_trade_ids)
     and account_id is null;

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
    'unexported', v_eligible - v_deleted,
    'desks_credited', v_desks,
    'orphans_deleted', v_orphans
  );
end;
$function$;

grant execute on function public.prune_exported_paper_trades(integer, uuid[], boolean) to service_role;

comment on function public.prune_exported_paper_trades(integer, uuid[], boolean) is
  'Delete closed paper trades older than the cutoff, restricted to ids the caller has verified are present in the repository export. Open trades are never eligible. Before deleting, writes one append-only trades_archived activity row per desk carrying the realised P&L it is about to remove, so the desk''s books still balance once the rows live only in the repository.';
