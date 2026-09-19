-- ===========================================================================
-- ad4_74_prune_resolution_evidence.sql - THE FASTEST-GROWING TABLE NOW
--
-- THE MEASUREMENT
-- ---------------
-- paper_resolution_evidence went from 31 MB to 75 MB in three days: 11,123
-- rows at about 7 KB each, because every row carries the FULL Gamma and CLOB
-- JSON payloads that prove a band's winner. At roughly 2,200 rows a day that
-- is 15 MB a day, and on its own it fills a 500 MB tier inside a month.
--
-- It is also the most valuable table on the desk. Those payloads are what
-- make an outcome admissible - the whole point of Phase 2A was that a
-- partially observed running maximum is not a settlement and only the venue
-- is. Nothing here deletes a proof that is still doing work.
--
-- WHAT IS AND IS NOT STILL DOING WORK
-- -----------------------------------
-- v_venue_band_resolution reads proof_id, winning_token, captured_at and the
-- three identity columns. It does NOT read gamma or clob. The payloads are
-- consulted once, by verify(), at the moment the proof is captured; after
-- that they are evidence at rest.
--
-- And the outcome they prove is frozen elsewhere. scripts/databank.py writes
-- fact_band_outcome from this view, and that row is immutable. So once a
-- band's outcome is in fact_band_outcome, the desk's answer no longer depends
-- on this row being present - only its provenance does, and provenance is
-- exactly what a Release asset preserves.
--
-- THE RULE, and all four parts hold at once:
--
--   1 THE OUTCOME IS ALREADY FROZEN in fact_band_outcome for that band. This
--     is the one that matters. A proof for a band nobody has banked yet is
--     the only copy of that answer and is never touched, however old.
--   2 OLDER THAN keep_days, floor 3. A settlement captured this morning is
--     still being read by the next databank run.
--   3 THE ARCHIVE COUNTED IT BACK. p_expected_rows is the count
--     scripts/archive_observations.py verified by re-downloading the asset it
--     uploaded, and this refuses if the database disagrees - which is what
--     makes a partial export impossible to turn into a wider delete.
--   4 NO STORED MANIFEST HAS HASHED IT. Deleting a row inside a published
--     sha256's range leaves a digest nobody can reproduce.
--
-- Measured 2026-09-19: 9,318 of 11,123 rows have a frozen outcome, 3,429 of
-- those are also more than three days old. Steady state leaves roughly the
-- last three days of evidence plus everything not yet banked.
--
-- RUN ORDER: after ad4_70_archive_exemption.sql, whose DELETE-only,
-- single-table, transaction-local exemption this claims. Re-runnable.
-- ===========================================================================

-- --------------------------------------------------------------------------
-- WHAT THE ARCHIVE READS AND THE PRUNE DELETES - the same set, by
-- construction.
--
-- The count contract in rule 3 only works if the rows uploaded are exactly
-- the rows removed. PostgREST cannot express "and its band's outcome is
-- frozen", so the predicate lives here and both halves read it: the archive
-- selects from this view, the prune deletes from the table using the same
-- test. Two hand-written predicates would drift, and the drift would show up
-- as a refused prune at best and a wider delete at worst.
-- --------------------------------------------------------------------------
create or replace view v_prunable_resolution_evidence as
select e.proof_id, e.condition_id, e.token_yes, e.token_no,
       e.winning_token, e.captured_at, e.gamma, e.clob, e.source_urls
from paper_resolution_evidence e
where exists (
  select 1
    from bands b
    join fact_band_outcome f on f.band_id = b.band_id
   where b.condition_id = e.condition_id
     and b.token_yes    = e.token_yes
     and b.token_no     = e.token_no);

comment on view v_prunable_resolution_evidence is
  'Resolution proofs whose band outcome is already frozen in fact_band_outcome, and therefore archivable. A proof for a band nobody has banked is the only copy of that answer and is not here at any age.';

grant select on v_prunable_resolution_evidence to service_role;


create or replace function public.prune_resolution_evidence(
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
  v_unfrozen bigint;
  v_manifest bigint;
  v_covered  timestamptz;
begin
  if p_keep_days < 3 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 3 - a settlement captured this morning is '
               'still being read by the next databank run');
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune - it is the count '
               'verified by re-downloading the uploaded archive');
  end if;

  if not p_dry_run then
    lock table public.paper_resolution_evidence in share row exclusive mode;
  end if;

  select count(*) into v_doomed
    from public.v_prunable_resolution_evidence where captured_at < v_before;

  if p_expected_rows is not null and v_doomed <> p_expected_rows then
    return jsonb_build_object(
      'ok', false,
      'error', format('archive row count mismatch: verified %s rows but prune would delete %s',
                      p_expected_rows, v_doomed),
      'expected_rows', p_expected_rows, 'would_delete', v_doomed);
  end if;

  if v_doomed = 0 then
    return jsonb_build_object('ok', true, 'deleted', 0,
      'note', format('nothing both frozen and older than %s', v_before));
  end if;

  select count(*), max((scope->>'cutoff')::timestamptz)
    into v_manifest, v_covered
    from public.proprietary_data_manifests
   where dataset = 'resolution'
     and (scope->>'cutoff')::timestamptz > (
       select min(captured_at) from public.v_prunable_resolution_evidence
        where captured_at < v_before);

  if coalesce(v_manifest, 0) > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format(
        '%s integrity manifest(s) hash this table through %s, which covers rows this prune '
        'would delete. Their sha256 could never be reproduced again.', v_manifest, v_covered),
      'manifests_covering_range', v_manifest, 'manifest_cutoff', v_covered,
      'would_delete', v_doomed);
  end if;

  select count(*) into v_keep from public.paper_resolution_evidence
   where captured_at >= v_before;
  -- Named separately because it is the number that says the guard is working:
  -- old proofs the desk has NOT banked an outcome for, which stay.
  select count(*) into v_unfrozen from public.paper_resolution_evidence e
   where e.captured_at < v_before
     and not exists (select 1 from public.v_prunable_resolution_evidence p
                      where p.proof_id = e.proof_id);

  if p_dry_run then
    return jsonb_build_object('ok', true, 'dry_run', true,
      'would_delete', v_doomed, 'would_keep', v_keep,
      'old_but_not_yet_banked', v_unfrozen,
      'older_than', v_before, 'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete');
  end if;

  -- Claimed here and given straight back. set_config's third argument is
  -- is_local: Postgres resets it at commit or rollback, so it cannot survive
  -- into the next statement on a pooled connection even if the delete throws.
  perform set_config('arbdesk.archiving', 'paper_resolution_evidence', true);
  delete from public.paper_resolution_evidence e
   where e.captured_at < v_before
     and exists (select 1 from public.v_prunable_resolution_evidence p
                  where p.proof_id = e.proof_id);
  perform set_config('arbdesk.archiving', '', true);

  return jsonb_build_object('ok', true, 'deleted', v_doomed, 'kept', v_keep,
    'old_but_not_yet_banked', v_unfrozen,
    'older_than', v_before, 'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.paper_resolution_evidence')),
    'note', 'run VACUUM FULL paper_resolution_evidence to return the space to the OS');
end;
$fn$;

comment on function public.prune_resolution_evidence(integer, boolean, timestamptz, bigint) is
  'Delete venue resolution proofs, but only ones whose band outcome is already frozen in fact_band_outcome, only after scripts/archive_observations.py has uploaded them to a Release and counted them back, never a row a stored integrity manifest has hashed, and only by claiming the transaction-local, DELETE-only, single-table exemption from the append-only guard.';

revoke all on function public.prune_resolution_evidence(integer, boolean, timestamptz, bigint) from public;
grant execute on function public.prune_resolution_evidence(integer, boolean, timestamptz, bigint) to service_role;
