-- ===========================================================================
-- TWO KEEPS NO READER NEEDS (WXPredict build 2.A, group A; Hassan, 6 Oct: "no
-- i dont wanna subscribe, we need to offload te data recurrently to te repo").
--
-- The project is on Supabase's Free plan, over its 500 MB database limit
-- (574.7 MiB on 6 Oct; docs/WXPREDICT_BUILD.md section 3.8). These two cuts
-- leave every reader its window; each row leaves only through the archive
-- (export, verify, commit, then this prune), as before:
--
--   decisions           floor 14 -> 2 days   (archive keep 30 -> 3)
--   band_probabilities  floor 30 -> 18 days  (archive keep 30 -> 18)
--
-- Measured 6 Oct ~13:45Z: 33,056 decisions older than 2 days (11.1 MB by
-- their share of the table) and 24,785 offered probability rows of markets
-- dated more than 18 days back (7.0 MB).
--
-- The functions are the ones live (20260925090000, 20260929140000), with only
-- the floor and its words changed; sql/ad4_96 says the same.
-- ===========================================================================

create or replace function public.prune_decisions(
  p_keep_days     integer     default 3,
  p_dry_run       boolean     default true,
  p_before        timestamptz default null,
  p_expected_rows bigint      default null
) returns jsonb
language plpgsql security definer set search_path = '' as $$
declare
  v_before timestamptz := coalesce(p_before, now() - make_interval(days => p_keep_days));
  v_doomed bigint;
begin
  -- 2 DAYS IS THE FLOOR (WXPredict build 2.A, group A; Hassan, 6 Oct:
  -- offload to the repository). The longest reader of a stored decision needs
  -- one day: engine_replay_live.py replays yesterday's, the paper-desk API
  -- reads 24 h, and the order and exit functions read the decision they are
  -- handed, minutes old. Every decision is in data/archive/decisions first.
  if p_keep_days < 2 then
    return jsonb_build_object('ok', false, 'error', 'keep_days must be at least 2');
  end if;
  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object('ok', false,
      'error', 'p_expected_rows is required for a committed prune - it is the count verified by re-downloading the uploaded archive');
  end if;
  if not p_dry_run then
    lock table public.decisions in share row exclusive mode;
  end if;
  -- A plain age predicate, which the export's REST filter says identically.
  select count(*) into v_doomed from public.decisions where decided_at < v_before;
  if p_expected_rows is not null and v_doomed <> p_expected_rows then
    return jsonb_build_object('ok', false,
      'error', format('archive row count mismatch: verified %s rows but prune would delete %s', p_expected_rows, v_doomed),
      'expected_rows', p_expected_rows, 'would_delete', v_doomed);
  end if;
  if v_doomed = 0 then
    return jsonb_build_object('ok', true, 'deleted', 0, 'note', format('no decisions older than %s', v_before));
  end if;
  if p_dry_run then
    return jsonb_build_object('ok', true, 'dry_run', true, 'would_delete', v_doomed,
      'rows_now', (select count(*) from public.decisions), 'older_than', v_before,
      'note', 'call again with p_dry_run => false to actually delete');
  end if;
  perform set_config('arbdesk.archiving', 'decisions', true);
  delete from public.decisions where decided_at < v_before;
  perform set_config('arbdesk.archiving', '', true);
  return jsonb_build_object('ok', true, 'deleted', v_doomed, 'older_than', v_before);
end $$;
revoke all on function public.prune_decisions(integer, boolean, timestamptz, bigint) from public, anon, authenticated;
grant execute on function public.prune_decisions(integer, boolean, timestamptz, bigint) to service_role;

create or replace function public.prune_band_probabilities(
  p_keep_days     integer,
  p_dry_run       boolean default true,
  p_before        date    default null,
  p_expected_rows bigint  default null
)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $function$
declare
  -- A cutoff may be older than the floor, never newer (plan v2 P1.1).
  v_before date := least(coalesce(p_before, current_date - p_keep_days), current_date - p_keep_days);
  -- Tonight's mirror exports every row computed since this instant, after
  -- the prune has run.
  v_unmirrored timestamptz := (date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC';
  v_doomed bigint;
  v_young  bigint;
  v_rows   bigint;
  v_bands  bigint;
  v_gone   bigint;
begin
  -- EIGHTEEN DAYS (WXPredict build 2.A, group A; Hassan, 6 Oct: offload to
  -- the repository). The window is the market's resolution_date.
  -- station_width_score reads every price of the markets resolved in the last
  -- 14 days (LOOKBACK_DAYS), priced from 3 days before (PRICING_LOOKBACK_DAYS):
  -- markets dated before current_date - 18 are four days past the oldest it
  -- reads. The analytics page reads open markets; everything older is read
  -- one row at a time, and those rows are never offered.
  if p_keep_days < 18 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 18 - station_width_score reads every price of the markets of the last 14 days'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune - it is the count '
               'read back from the committed archive file'
    );
  end if;

  -- A late price for an old band must become a count mismatch, not slip past
  -- the verified file.
  if not p_dry_run then
    lock table public.band_probabilities in share row exclusive mode;
  end if;

  select count(*), count(*) filter (where computed_at >= v_unmirrored)
    into v_doomed, v_young
    from public.v_prunable_band_probabilities where resolution_date < v_before;

  -- Never a row the mirror has not had yet, whatever the window.
  if v_young > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format('%s of the %s rows for markets dated before %s were computed since %s and are '
                      'not in the repo mirror yet - nothing deleted', v_young, v_doomed, v_before, v_unmirrored),
      'would_delete', v_doomed,
      'not_yet_mirrored', v_young
    );
  end if;

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
                              'note', format('no unread prices for markets dated before %s', v_before));
  end if;

  -- The number that says the guard works: bands still priced. Every band
  -- keeps its newest row, so it cannot change.
  select count(*), count(distinct band_id) into v_rows, v_bands from public.band_probabilities;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true, 'dry_run', true,
      'would_delete', v_doomed,
      'rows_now', v_rows,
      'bands_priced', v_bands,
      'older_than', v_before,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.band_probabilities p
   where p.prob_id in (select v.prob_id from public.v_prunable_band_probabilities v
                        where v.resolution_date < v_before);
  -- The lock holds the table, not the facts and edges that mark a row: one
  -- written between the count and the delete changes the set. Then nothing
  -- is deleted, rather than something other than the verified file.
  get diagnostics v_gone = row_count;
  if v_gone <> v_doomed then
    raise exception 'prune_band_probabilities: counted % rows but the delete took % - rolled back, nothing deleted',
      v_doomed, v_gone;
  end if;

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'rows_now', (select count(*) from public.band_probabilities),
    'bands_priced_before', v_bands,
    'bands_priced_after', (select count(distinct band_id) from public.band_probabilities),
    'older_than', v_before,
    'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.band_probabilities')),
    'note', 'request_reclaim returns the space the same night'
  );
end;
$function$;

-- SECURITY DEFINER and deletes rows: service_role only (plan v2 P1.1).
revoke execute on function public.prune_band_probabilities(integer, boolean, date, bigint) from public, anon, authenticated;
grant execute on function public.prune_band_probabilities(integer, boolean, date, bigint) to service_role;

comment on function public.prune_band_probabilities(integer, boolean, date, bigint) is
  'Delete the band_probabilities rows v_prunable_band_probabilities offers for markets dated before the cutoff (at least 18 days back), only when the caller''s count read back from the committed archive file matches exactly and none was computed since yesterday''s UTC midnight (the repo mirror copies those after the prune). Every band keeps its newest row, so the bands priced cannot change.';
