-- ===========================================================================
-- ad4_70_archive_exemption.sql - THE ARCHIVE COULD NEVER DELETE ANYTHING
--
-- WHAT HAPPENED
-- -------------
-- ad4_69 added prune_research_captures so the archive could move cold
-- captures to a GitHub Release and free the space. It was written, tested
-- against its own guards, and shipped without checking whether anything else
-- already refused the delete. Something did:
--
--   CREATE TRIGGER research_immutable BEFORE DELETE OR UPDATE
--     ON public.research_captures FOR EACH ROW
--     EXECUTE FUNCTION arbdesk_private.immutable_record()
--
-- arbdesk_private.immutable_record() raises unconditionally. So every nightly
-- run since has done the whole job - exported 78,291 rows, gzipped them to
-- 11.6 MB, uploaded to the Release, re-downloaded and verified the count -
-- and then died on the prune with
--
--   prune_research_captures -> HTTP 400: {"message":"Append-only record;
--   write a linked correction instead"}
--
-- a red workflow every night, and a database that kept growing: 643 MB
-- against a 500 MB tier, 143 MB over, with research_captures at 137 MB and
-- 103,836 rows. The archive was not broken in any way it could report; it was
-- forbidden by a rule nobody had reconciled it with.
--
-- WHY AN EXEMPTION RATHER THAN DROPPING THE TRIGGER
-- -------------------------------------------------
-- The guard is right. Fifteen tables carry it - the outcome facts, the venue
-- evidence, the manifests, the paper activity log - and it is the thing that
-- makes them admissible: nobody can quietly edit a number the desk traded on.
-- Dropping it to let one job delete would trade a real safeguard for disk.
--
-- But an archive is not an edit. The rows are uploaded, re-downloaded and
-- counted before anything is deleted, and they remain readable forever in the
-- Release. That is a MOVE, and it is the one operation the guard should
-- permit - narrowly, and only to the code that did the verifying.
--
-- HOW NARROW
-- ----------
--   DELETE only.       An UPDATE is still an edit to evidence and stays
--                      forbidden on all fifteen tables. TRUNCATE takes rows
--                      nobody counted and stays forbidden too.
--   ONE TABLE AT A TIME. The GUC holds the table NAME and must match
--                      tg_table_name, so an exemption obtained for
--                      research_captures does not unlock fact_band_outcome.
--   TRANSACTION-LOCAL. set_config(..., true) is reset at commit or rollback,
--                      so it cannot leak into the next statement, the next
--                      request on a pooled connection, or anything else.
--   SERVICE ROLE ONLY. Only prune_research_captures sets it, it is SECURITY
--                      DEFINER, and execute is granted to service_role alone.
--
-- RUN ORDER: after ad4_69_prune_research_captures.sql. Re-runnable.
-- ===========================================================================

create or replace function arbdesk_private.immutable_record()
returns trigger
language plpgsql
set search_path to ''
as $fn$
begin
  -- THE ONE EXEMPTION, and it is a move rather than an edit: an archive that
  -- has already uploaded these rows and counted them back. See the header for
  -- why this is four separate narrowings rather than a flag.
  if tg_op = 'DELETE'
     and current_setting('arbdesk.archiving', true) = tg_table_name then
    return old;
  end if;

  raise exception 'Append-only record; write a linked correction instead';
end
$fn$;

comment on function arbdesk_private.immutable_record() is
  'Refuses every UPDATE, DELETE and TRUNCATE on the append-only evidence tables, except a DELETE from an archive that has named this exact table in the transaction-local arbdesk.archiving setting - which only prune_research_captures does, and only after the rows are verified in a Release.';


-- --------------------------------------------------------------------------
-- The prune claims that exemption, and gives it straight back.
--
-- Everything else about the function is unchanged: it still refuses a window
-- under two days, still requires p_expected_rows to match what the archive
-- verified, and still refuses to delete a row a stored integrity manifest has
-- hashed. The exemption is claimed AFTER all three, immediately around the
-- delete, so a failed guard never reaches a transaction that could delete.
-- --------------------------------------------------------------------------
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

  -- Claimed here, and only here. The third argument is is_local: Postgres
  -- resets it at commit or rollback, so it cannot survive into the next
  -- statement on a pooled connection even if the delete throws.
  perform set_config('arbdesk.archiving', 'research_captures', true);
  delete from public.research_captures where captured_at < v_before;
  perform set_config('arbdesk.archiving', '', true);

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
  'Delete research captures older than a cutoff, but only after scripts/archive_observations.py has uploaded them to a Release and counted them back, never a row a stored integrity manifest has already hashed, and only by claiming a transaction-local, DELETE-only, single-table exemption from the append-only guard.';

revoke all on function public.prune_research_captures(integer, boolean, timestamptz, bigint) from public;
grant execute on function public.prune_research_captures(integer, boolean, timestamptz, bigint) to service_role;
