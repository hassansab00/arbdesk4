-- ===========================================================================
-- ad4_69_prune_research_captures.sql - THE FASTEST-GROWING TABLE
--
-- THE PROBLEM
-- -----------
-- research_captures reached 92 MB in FIVE DAYS - 78,291 rows, about 18 MB a
-- day - on a 500 MB tier with 46 MB left. At that rate the database is full
-- inside three days.
--
-- It is not duplication. payload_hash already dedupes and all 78,291 payloads
-- are distinct: it is one capture per row of each source relation, six times a
-- day, and band_probabilities alone accounts for 30,914 of them. The evidence
-- is real. It just does not have to sit in Postgres to be evidence.
--
-- So it joins the three tables already archived to a GitHub Release by
-- scripts/archive_observations.py, and this is the prune half of that: the
-- rows leave Postgres only after they have been uploaded, re-downloaded and
-- counted back.
--
-- WHY THE WINDOW IS FOUR DAYS AND NOT NINETY
-- ------------------------------------------
-- The other three archives exist so a model has history to train on, and
-- ninety days still leaves them months. This table has no row older than five
-- days, so a ninety-day window - or thirty, or even seven - archives exactly
-- nothing. The floor here is 2 rather than prune_trades' 30 for the same
-- reason: a floor longer than the history makes the function unusable.
--
-- WHAT MAKES IT SAFE
-- ------------------
-- Nothing derived survives this one. There is no feature cache and no presence
-- rollup to check against, as there is for observations and trades - the
-- capture IS the artefact. So the guards are:
--
--   1 p_expected_rows. The caller must pass the count it verified by
--     re-downloading the uploaded asset, and this refuses if the database
--     disagrees. That is what makes a partial export impossible to turn into
--     a wider delete.
--   2 NO ROW A MANIFEST HAS HASHED. scripts/data_integrity.py fingerprints
--     this table up to a cutoff and stores the digest in
--     proprietary_data_manifests. Deleting a row inside a manifest's range
--     would leave a published hash nobody can ever reproduce, which is worse
--     than being full. There are no manifests today - the script has never
--     been run - so this guard costs nothing now and is the whole ballgame
--     the day it is.
--
-- RUN ORDER: after ad4_00_preflight.sql. Re-runnable.
-- ===========================================================================

create or replace function public.prune_research_captures(
  p_keep_days     integer,
  p_dry_run       boolean     default true,
  p_before        timestamptz default null,
  p_expected_rows bigint      default null
) returns jsonb
language plpgsql
security definer
set search_path to 'public', 'pg_temp'
as $fn$
declare
  v_before   timestamptz := coalesce(p_before, now() - make_interval(days => p_keep_days));
  v_doomed   bigint;
  v_keep     bigint;
  v_manifest bigint;
  v_covered  timestamptz;
begin
  -- Two days, not thirty. A floor longer than the table's own history would
  -- make this function permanently refuse; see the header.
  if p_keep_days < 2 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 2 - a capture written this morning is still being read'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune - it is the count '
               'verified by re-downloading the uploaded archive'
    );
  end if;

  if not p_dry_run then
    lock table public.research_captures in share row exclusive mode;
  end if;

  select count(*) into v_doomed
    from public.research_captures where captured_at < v_before;

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
    return jsonb_build_object(
      'ok', true, 'deleted', 0,
      'note', format('nothing older than %s', v_before)
    );
  end if;

  -- THE GUARD. A stored manifest is a published sha256 over this table up to
  -- a cutoff. Delete a row inside that range and the digest can never be
  -- reproduced - the evidence chain is broken silently, and the break is only
  -- discovered by whoever tries to verify it.
  select count(*), max((scope->>'cutoff')::timestamptz)
    into v_manifest, v_covered
    from public.proprietary_data_manifests
   where dataset = 'research'
     and (scope->>'cutoff')::timestamptz > (
       select min(captured_at) from public.research_captures where captured_at < v_before
     );

  if coalesce(v_manifest, 0) > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        '%s integrity manifest(s) hash this table through %s, which covers rows this prune '
        'would delete. Their sha256 could never be reproduced again. Archive to a cutoff '
        'at or after the newest manifest, or retire those manifests first.',
        v_manifest, v_covered),
      'manifests_covering_range', v_manifest,
      'manifest_cutoff', v_covered,
      'would_delete', v_doomed
    );
  end if;

  select count(*) into v_keep
    from public.research_captures where captured_at >= v_before;

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

  delete from public.research_captures where captured_at < v_before;

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'kept', v_keep,
    'older_than', v_before,
    'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.research_captures')),
    'note', 'run VACUUM FULL research_captures to return the space to the OS'
  );
end;
$fn$;

comment on function public.prune_research_captures(integer, boolean, timestamptz, bigint) is
  'Delete research captures older than a cutoff, but only after scripts/archive_observations.py has uploaded them to a Release and counted them back, and never a row a stored integrity manifest has already hashed.';

revoke all on function public.prune_research_captures(integer, boolean, timestamptz, bigint) from public;
grant execute on function public.prune_research_captures(integer, boolean, timestamptz, bigint) to service_role;
