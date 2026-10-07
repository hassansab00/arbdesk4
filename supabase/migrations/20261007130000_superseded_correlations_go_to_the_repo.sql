-- ===========================================================================
-- EVERY CORRELATION BUT EACH PAIR'S NEWEST GOES TO THE REPOSITORY (WXPredict
-- build 2.A, group B; Hassan, 7 Oct: "proceed with the correlation cuts", on
-- condition that no proprietary data is lost).
--
-- derived_city_correlation is an append-only log: recompute_correlation adds
-- one row per city pair a day (1,326 a day since 24 Sep) and nothing removed
-- the old ones. Measured 7 Oct 11:57Z: 63,640 rows, 1,375 pairs, 8,944 kB.
-- Every reader takes each pair's newest row or the newest computation's rows
-- (which are each pair's newest); calc_recommendation, the one reader of
-- older rows, is called by nothing (sql/ad4_prune_city_correlation.sql lists
-- every reader).
--
-- 1. v_prunable_city_correlation: the rows a newer row of the same pair has
--    superseded, with the three-column key joined into one text key the
--    archive keyset-pages on. Service role only.
-- 2. prune_city_correlation: deletes those rows computed at least 2 days back,
--    only after scripts/archive_observations.py has committed them to
--    data/archive/correlation and read them back, only the exact count, and
--    rolls back if the number of pairs moves. The mirror (data/mirror) holds
--    every row computed before 7 Oct already: 62,314 rows in 14 files.
-- 3. request_reclaim: derived_city_correlation joins the allow-list
--    (appended, so every other table keeps its slot).
-- 4. A daily backstop reclaim at 03:10 (sql/ad4_66).
--
-- The bodies are the ones in sql/ad4_prune_city_correlation.sql and
-- sql/ad4_66_reclaim_archived_tables.sql. Nothing is deleted by this
-- migration; the archive's next run does it. Re-runnable.
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


create or replace function public.request_reclaim(p_table text)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_allowed constant text[] := array[
    'research_captures', 'paper_resolution_evidence', 'book_snapshots', 'edges',
    'weather_observations', 'weather_forecasts', 'trades_observed', 'decisions',
    -- appended (28 Sep), so every table above keeps its two-minute slot
    'paper_book_evidence', 'weather_forecast_features', 'signals',
    -- appended (29 Sep, P1.6 phase 2)
    'weather_forecast_models', 'band_probabilities',
    -- appended (7 Oct, WXPredict build 2.A)
    'derived_city_correlation'];
  v_now   timestamptz := now();
  v_hour  int := extract(hour from (v_now at time zone 'UTC'))::int;
  v_at    timestamptz;
  v_utc   timestamp;
  v_job   text;
  v_expr  text;
begin
  if p_table is null or not (p_table = any (v_allowed)) then
    raise exception 'request_reclaim: % is not a table the archive prunes', p_table
      using errcode = '22023';
  end if;

  if v_hour < 6 then
    v_at := v_now + make_interval(mins => 2 + 2 * (array_position(v_allowed, p_table) - 1));
  else
    v_at := ((date_trunc('day', v_now at time zone 'UTC') + interval '1 day' + interval '1 hour')
             at time zone 'UTC')
            + make_interval(mins => 2 * (array_position(v_allowed, p_table) - 1));
  end if;
  v_utc := v_at at time zone 'UTC';

  v_job  := 'ad4_reclaim_after_archive_' || p_table;
  v_expr := format('%s %s %s %s *',
                   extract(minute from v_utc)::int, extract(hour  from v_utc)::int,
                   extract(day    from v_utc)::int, extract(month from v_utc)::int);

  perform cron.schedule(v_job, v_expr,
                        format('VACUUM (FULL, ANALYZE) public.%I', p_table));

  return jsonb_build_object('ok', true, 'job', v_job, 'cron', v_expr, 'fires_at', v_at);
end $$;

comment on function public.request_reclaim(text) is
  'Schedules a VACUUM FULL of one archive-pruned table in the next quiet window (straight away inside 00:00-06:00 UTC, otherwise the next 01:00 UTC), staggered two minutes a table, so the space a prune frees comes back the same night without locking pages mid-day (plan v2 P6.5).';

revoke all on function public.request_reclaim(text) from public, anon, authenticated;
grant execute on function public.request_reclaim(text) to service_role;

-- The backstop reclaim (the same job sql/ad4_66 schedules).
do $$
begin
  if exists (select 1 from pg_namespace where nspname = 'cron') then
    perform cron.schedule('ad4_reclaim_derived_city_correlation', '10 3 * * *',
                          'VACUUM (FULL, ANALYZE) public.derived_city_correlation');
  end if;
end $$;
