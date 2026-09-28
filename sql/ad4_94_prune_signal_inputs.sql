-- ===========================================================================
-- ad4_94_prune_signal_inputs.sql - A WEEK-OLD SIGNAL'S DECISION INPUTS GO TO
-- THE REPOSITORY (plan v2 P1.6 phase 1, step 4, 28 Sep)
--
-- Safe to run any time. Creates one view and one function. Changes nothing by
-- itself - p_dry_run defaults to true and the caller must ask twice.
--
-- Hassan, 28 Sep: "YES APPROVED as long as we dont lose any collected data".
--
-- THE ROWS STAY. ledger, paper_orders, paper_trade_plans and paper_trades all
-- hold foreign keys to signals, and the strategy board reads every settled
-- signal. What goes is one key of the payload, decision_inputs: each band's
-- full decision evidence as the engine saw it. In signals older than 7 days
-- it is 40 MB of the payloads' 42 MB (uncompressed, measured 28 Sep 21:58Z);
-- the payloads of those 5,797 signals take 16 MB on disk.
--
-- WHO READS decision_inputs (checked 28 Sep, the code and every live view and
-- function):
--   scripts/paper_plans.py            signals fired in the last 15 minutes
--   arbdesk_private.decision_snapshot on a paper BUY fill; the longest any of
--                                     the 203 orders ever lived after its
--                                     signal is 16 minutes
-- and nothing else. Every other payload key stays, so these are unchanged:
--   v_signal_mark (the board)  payload ? 'band_ids', ? 'basket_group', -> 'band_ids'
--   scripts/databank.py        payload -> 'decision_snapshot', last 7 days
-- The repo mirror copied every signal with its whole payload the night after
-- it fired; this adds the archive's own verified file.
--
-- The stripped payload carries "decision_inputs_in_repo": true, so a reader
-- can tell an archived key from one that was never written, and
-- v_signal_inputs_export stops listing the row: nothing is exported twice.
--
-- A backfill is not research (20260923110000): the strip switches research
-- capture off for its own transaction, or preserve_research_output would copy
-- every stripped signal into research_captures.
--
-- Run:
--   select prune_signal_inputs(7);                          -- dry run
--   select prune_signal_inputs(7, false, <cutoff>, <rows>); -- count-bound
-- scripts/archive_observations.py --table signal_inputs calls both halves,
-- the second only after the file is committed and read back from GitHub.
-- ===========================================================================

create or replace view public.v_signal_inputs_export
with (security_invoker = true) as
select s.signal_id,
       s.fired_at,
       s.strategy_id,
       s.payload -> 'decision_inputs' as decision_inputs
  from public.signals s
 where s.payload ? 'decision_inputs';

comment on view public.v_signal_inputs_export is
  'Each signal''s decision_inputs while it is still in the payload, for the archive''s export; a row leaves the view once prune_signal_inputs has stripped it. Service role only (plan v2 P1.6 phase 1).';

revoke all on public.v_signal_inputs_export from public, anon, authenticated;
grant select on public.v_signal_inputs_export to service_role;


create or replace function public.prune_signal_inputs(
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
  -- A cutoff may be older than the floor, never newer (plan v2 P1.1).
  v_before timestamptz := least(coalesce(p_before, now() - make_interval(days => p_keep_days)),
                                now() - make_interval(days => p_keep_days));
  v_doomed bigint;
  v_keep   bigint;
  v_done   bigint;
begin
  -- SEVEN DAYS, the plan's window. The readers need 16 minutes; the databank
  -- banks the last 7 days, from keys that stay.
  if p_keep_days < 7 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 7 - the databank banks the last 7 days of signals'
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
    lock table public.signals in share row exclusive mode;
  end if;

  select count(*) into v_doomed
    from public.signals where fired_at < v_before and payload ? 'decision_inputs';

  if p_expected_rows is not null and v_doomed <> p_expected_rows then
    return jsonb_build_object(
      'ok', false,
      'error', format('archive row count mismatch: verified %s rows but prune would strip %s',
                      p_expected_rows, v_doomed),
      'expected_rows', p_expected_rows,
      'would_delete', v_doomed
    );
  end if;

  if v_doomed = 0 then
    return jsonb_build_object('ok', true, 'deleted', 0,
                              'note', format('no decision_inputs older than %s', v_before));
  end if;

  select count(*) into v_keep
    from public.signals where fired_at >= v_before and payload ? 'decision_inputs';

  if p_dry_run then
    return jsonb_build_object(
      'ok', true, 'dry_run', true,
      'would_delete', v_doomed,
      'would_keep', v_keep,
      'older_than', v_before,
      'expected_rows', p_expected_rows,
      'note', 'strips payload.decision_inputs only; the rows stay. Call again with p_dry_run => false'
    );
  end if;

  -- Not research: no capture of the stripped rows. is_local, so it ends with
  -- the transaction whatever happens.
  perform set_config('arbdesk.skip_capture', 'on', true);
  update public.signals
     set payload = (payload - 'decision_inputs') || jsonb_build_object('decision_inputs_in_repo', true)
   where fired_at < v_before and payload ? 'decision_inputs';
  get diagnostics v_done = row_count;
  perform set_config('arbdesk.skip_capture', '', true);

  -- 'deleted' is the name the archive reads its count back under: here it is
  -- the payload keys removed, not rows.
  return jsonb_build_object(
    'ok', true,
    'deleted', v_done,
    'stripped', v_done,
    'kept', v_keep,
    'older_than', v_before,
    'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.signals')),
    'note', 'decision_inputs stripped, rows kept; request_reclaim returns the space the same night'
  );
end;
$function$;

-- SECURITY DEFINER and rewrites rows: service_role only (plan v2 P1.1).
revoke execute on function public.prune_signal_inputs(integer, boolean, timestamptz, bigint) from public, anon, authenticated;
grant execute on function public.prune_signal_inputs(integer, boolean, timestamptz, bigint) to service_role;

comment on function public.prune_signal_inputs(integer, boolean, timestamptz, bigint) is
  'Strip payload.decision_inputs from signals older than the cutoff (at least 7 days; its readers need 16 minutes), marking the payload decision_inputs_in_repo, only when the caller''s count read back from the committed archive file matches exactly. The rows and every other payload key stay; research capture is off for the strip.';
