-- ===========================================================================
-- ad4_prune_city_correlation.sql - EVERY CORRELATION BUT EACH PAIR'S NEWEST
-- GOES TO THE REPOSITORY (WXPredict build 2.A, group B; Hassan, 7 Oct:
-- "proceed with the correlation cuts").
--
-- derived_city_correlation is an append-only log: recompute_correlation
-- (sql/ad4_capacity_correlation.sql, run by capacity.py in pipeline_daily at
-- about 05:00 UTC) inserts one row per city pair every day and never removes
-- the last one. Measured 7 Oct 11:57Z: 63,640 rows, 1,375 pairs, 8,944 kB.
-- Every row but each pair's newest has been superseded.
--
-- WHO READS IT, enumerated (grep of scripts/, web/, n8n/ and sql/, and every
-- live function and view whose body names it, 7 Oct):
--
--   signal_engine._context        the rows of the newest computation - each
--                                 one its pair's newest
--   signal_engine._correlations   the newest row per pair (its docstring:
--                                 reading them all would average this
--                                 month's correlation with another season's)
--   v_data_freshness,
--   v_synthesis_inventory         max(computed_at) and a row count
--   ad4_verify                    that the table exists
--   calc_recommendation           the 20 newest rows of the cities' pairs.
--                                 Nothing calls it: its page was removed,
--                                 and ad4_38 and 20260923130000 took its
--                                 anon and authenticated grants. It is not
--                                 changed here.
--
-- WHAT IS KEPT: each pair's newest row, whatever its age, and every row
-- inside p_keep_days. So the newest computation and the newest per pair
-- cannot change; the prune checks the pair count and rolls back if it moved.
--
-- NOTHING LEAVES WITHOUT BEING IN THE REPOSITORY FIRST. The view below is
-- what scripts/archive_observations.py exports to data/archive/correlation and
-- what this function deletes - the same set, by construction - and it
-- deletes only the exact count read back from the committed file. The mirror
-- (scripts/mirror_to_repo.py) copies the table by computed_at a whole UTC
-- day a night after the prune; two days is the floor, so a row can leave
-- only once the mirror has it too.
--
-- RUN ORDER: after sql/ad4_phase2.sql, which creates the table. Re-runnable.
-- ===========================================================================

create or replace view public.v_prunable_city_correlation
with (security_invoker = true) as
select f.city_a || '|' || f.city_b || '|'
         || to_char(f.computed_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') as correlation_key,
       f.*
  from public.derived_city_correlation f
 where exists (select 1 from public.derived_city_correlation n
                where n.city_a = f.city_a
                  and n.city_b = f.city_b
                  and n.computed_at > f.computed_at);

comment on view public.v_prunable_city_correlation is
  'derived_city_correlation rows a newer row of the same pair has superseded, with the primary key joined into one text key (city_a|city_b|computed_at, UTC to the microsecond) for the archive''s keyset-paged export. Each pair''s newest row is never here, so every reader keeps what it reads. Age is applied by the caller. Service role only (WXPredict build 2.A).';

revoke all on public.v_prunable_city_correlation from public, anon, authenticated;
grant select on public.v_prunable_city_correlation to service_role;


create or replace function public.prune_city_correlation(
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
  -- Tonight's mirror exports every row computed since this instant, after
  -- the prune has run.
  v_unmirrored timestamptz := (date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC';
  v_doomed bigint;
  v_young  bigint;
  v_gone   bigint;
  v_pairs  bigint;
  v_after  bigint;
begin
  -- TWO DAYS IS THE FLOOR. The mirror copies the rows computed before this
  -- UTC midnight, after the prune; two days back is always before yesterday's
  -- midnight, so a row cannot leave before the mirror has it.
  if p_keep_days is null or p_keep_days < 2 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 2 - the repo mirror copies a day after it ends'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune - it is the count '
               'read back from the committed archive file'
    );
  end if;

  -- A recompute landing between the count and the delete would supersede
  -- more rows; it must become a count mismatch, not slip past the file.
  if not p_dry_run then
    lock table public.derived_city_correlation in share row exclusive mode;
  end if;

  select count(*), count(*) filter (where computed_at >= v_unmirrored)
    into v_doomed, v_young
    from public.v_prunable_city_correlation where computed_at < v_before;

  -- Never a row the mirror has not had, whatever the window. The two-day
  -- floor keeps every cutoff before yesterday's midnight today; this is what
  -- would still hold if the floor were lowered.
  if v_young > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format('%s of the %s superseded rows before %s were computed since %s and are not in '
                      'the repo mirror yet - nothing deleted', v_young, v_doomed, v_before, v_unmirrored),
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
                              'note', format('no superseded correlations older than %s', v_before));
  end if;

  -- The number that says the guard works: every pair keeps its newest row.
  select count(*) into v_pairs
    from (select distinct city_a, city_b from public.derived_city_correlation) p;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true, 'dry_run', true,
      'would_delete', v_doomed,
      'rows_now', (select count(*) from public.derived_city_correlation),
      'pairs', v_pairs,
      'older_than', v_before,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.derived_city_correlation c
   where c.computed_at < v_before
     and exists (select 1 from public.v_prunable_city_correlation p
                  where p.city_a = c.city_a
                    and p.city_b = c.city_b
                    and p.computed_at = c.computed_at);
  get diagnostics v_gone = row_count;
  -- Exactly the rows counted and verified in the file, or none at all.
  if v_gone <> v_doomed then
    raise exception 'prune_city_correlation: counted % rows, the delete took % - nothing deleted', v_doomed, v_gone;
  end if;

  select count(*) into v_after
    from (select distinct city_a, city_b from public.derived_city_correlation) p;
  if v_after <> v_pairs then
    raise exception 'prune_city_correlation: % pairs before, % after - nothing deleted', v_pairs, v_after;
  end if;

  return jsonb_build_object(
    'ok', true,
    'deleted', v_gone,
    'rows_now', (select count(*) from public.derived_city_correlation),
    'pairs_before', v_pairs,
    'pairs_after', v_after,
    'older_than', v_before,
    'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.derived_city_correlation')),
    'note', 'request_reclaim returns the space the same night'
  );
end;
$function$;

-- SECURITY DEFINER and deletes rows: service_role only (plan v2 P1.1).
revoke execute on function public.prune_city_correlation(integer, boolean, timestamptz, bigint) from public, anon, authenticated;
grant execute on function public.prune_city_correlation(integer, boolean, timestamptz, bigint) to service_role;

comment on function public.prune_city_correlation(integer, boolean, timestamptz, bigint) is
  'Delete the derived_city_correlation rows v_prunable_city_correlation offers (a newer row of the same pair exists) computed before the cutoff (at least 2 days back), only when the caller''s count read back from the committed archive file matches exactly. Every pair keeps its newest row; the pair count is checked and the delete rolled back if it moved (WXPredict build 2.A).';
