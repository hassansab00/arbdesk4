-- ===========================================================================
-- AN ARCHIVED TRADE KEEPS ITS STRATEGY (9 Oct; review of the board PR #351).
--
-- prune_exported_paper_trades deletes closed paper trades 30 days after they
-- close, once the daily export has them in web/public/paper-trades, and
-- writes one append-only trades_archived row per desk with the count and the
-- realised P&L, so the desk's books still balance. That kept the money and
-- lost the record by strategy: v_strategy_board reads an engine strategy's
-- all-time trades, wins, net and verdict from its own shadow ledger's
-- paper_trades, so from the first prune of an engine trade (s12_no's closed
-- 30 Sep 08:36Z, so the export run of 31 Oct) those numbers would have
-- fallen back.
--
-- One key is added to that row: by_strategy, {strategy_id: {trades, won,
-- realized_pnl}} over the same trades. Nothing else in the function changes;
-- the live body equals sql/ad4_67's with comments stripped (md5 8e94aad8...,
-- checked 9 Oct). No trades_archived row exists yet (0, 9 Oct), so no old
-- row lacks the key. The statement is sql/ad4_67's, verbatim.
--
-- Its body deletes, so the Supabase tool cannot apply it: Hassan runs it.
-- Re-runnable.
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
  v_lineage bigint;
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
           'exported_to',       'web/public/paper-trades',
           -- PER STRATEGY (9 Oct), so a record kept per strategy outlives its
           -- rows the way the desk's cash does: v_strategy_board reads an
           -- engine strategy's all-time trades, wins and net from its own
           -- ledger, and adds these back once the rows are in the repository.
           -- Won is net_pnl > 0, as the board counts it. A trade with no
           -- strategy_id is in the totals above and in no entry here.
           'by_strategy', (
             select jsonb_object_agg(x.strategy_id, jsonb_build_object(
                      'trades', x.n, 'won', x.won, 'realized_pnl', x.pnl))
               from (select t2.strategy_id, count(*) as n,
                            count(*) filter (where t2.net_pnl > 0) as won,
                            coalesce(sum(t2.net_pnl), 0) as pnl
                       from public.paper_trades t2
                      where t2.account_id = t.account_id
                        and t2.closed_at is not null and t2.closed_at < v_before
                        and t2.trade_id = any(p_trade_ids)
                        and t2.strategy_id is not null
                      group by t2.strategy_id) x)),
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

  -- AND THE LINEAGE STAYS TOO - WHICH IS WHY THIS FUNCTION COULD NOT DELETE
  -- ANYTHING AT ALL.
  --
  -- ledger.trade_id references paper_trades with no ON DELETE action, and
  -- every paper trade the engine has ever written has ledger rows behind it -
  -- signal, decision, order, fill, exit. So the delete below raised
  --
  --   23503: update or delete on table "paper_trades" violates foreign key
  --   constraint "ledger_trade_id_fkey" on table "ledger"
  --
  -- for every trade, always. Verified against the live database in a
  -- rolled-back transaction on 2026-09-20: 70 of 70 trades referenced by 135
  -- ledger rows, 0 deletable. The function has never had an eligible row to
  -- delete - it keeps thirty days and the desk is younger than that - so the
  -- first time it mattered would have been the first time it ran for real.
  --
  -- ON DELETE CASCADE WOULD BE THE WRONG FIX: the ledger is the decision that
  -- produced the trade, and it is the thing the desk is judged on. So the
  -- LINK is moved into data and only the constraint is released. The ledger
  -- row keeps everything it had, plus the id of the trade it belongs to,
  -- which is the key the exported file is written under - so the join still
  -- exists, it just goes through web/public/paper-trades instead of a foreign
  -- key. Nothing is deleted from the ledger and nothing about the decision
  -- changes; ledger has no append-only trigger and trade_id is nullable, so
  -- this needs no schema change at all.
  update public.ledger
     set detail = coalesce(detail, '{}'::jsonb)
                  || jsonb_build_object('archived_trade_id', trade_id,
                                        'archived_to', 'web/public/paper-trades',
                                        'archived_at', now()),
         trade_id = null
   where trade_id in (select t.trade_id from public.paper_trades t
                       where t.closed_at is not null and t.closed_at < v_before
                         and t.trade_id = any(p_trade_ids));
  get diagnostics v_lineage = row_count;

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
    'orphans_deleted', v_orphans,
    'lineage_rows_detached', v_lineage
  );
end;
$function$;

-- SECURITY DEFINER and it deletes rows: service_role only. A new function is
-- executable by PUBLIC unless revoked (plan v2 P1.1).
revoke execute on function public.prune_exported_paper_trades(integer, uuid[], boolean) from public, anon, authenticated;
grant execute on function public.prune_exported_paper_trades(integer, uuid[], boolean) to service_role;

comment on function public.prune_exported_paper_trades(integer, uuid[], boolean) is
  'Delete closed paper trades older than the cutoff, restricted to ids the caller has verified are present in the repository export. Open trades are never eligible. Before deleting, writes one append-only trades_archived activity row per desk carrying the realised P&L it is about to remove, so the desk''s books still balance once the rows live only in the repository.';
