-- ===========================================================================
-- BOOK PROOF GOES TO THE REPOSITORY (plan v2 P1.6 phase 1, step 2, 28 Sep)
--
-- Hassan, 28 Sep: "YES APPROVED as long as we dont lose any collected data".
--
-- 1. prune_book_evidence: paper_book_evidence older than a day leaves
--    Postgres only after scripts/archive_observations.py has committed it to
--    data/archive/book_evidence and read it back, and only when the count
--    matches exactly. Its readers take proof no older than 900 s. The table is
--    append-only; the function claims the single-table archive exemption.
-- 2. request_reclaim: paper_book_evidence joins the allow-list (appended, so
--    the other tables keep their slots), so the space returns the same night.
-- 3. A daily backstop reclaim at 03:25 (sql/ad4_66).
--
-- The bodies are the ones in sql/ad4_92_prune_book_evidence.sql and
-- sql/ad4_66_reclaim_archived_tables.sql. Re-runnable.
-- ===========================================================================

create or replace function public.prune_book_evidence(
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
  -- A cutoff may be older than the floor, never newer (the rule every prune
  -- here follows since plan v2 P1.1).
  v_before timestamptz := least(coalesce(p_before, now() - make_interval(days => p_keep_days)),
                                now() - make_interval(days => p_keep_days));
  v_doomed bigint;
  v_keep   bigint;
begin
  -- ONE DAY. The longest any reader looks back is 900 seconds.
  if p_keep_days < 1 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 1 - an order may still be completing on this morning''s book'
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
    lock table public.paper_book_evidence in share row exclusive mode;
  end if;

  select count(*) into v_doomed
    from public.paper_book_evidence where captured_at < v_before;

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
                              'note', format('nothing older than %s', v_before));
  end if;

  select count(*) into v_keep
    from public.paper_book_evidence where captured_at >= v_before;

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

  -- Claimed here, and only here; is_local, so it ends with the transaction.
  perform set_config('arbdesk.archiving', 'paper_book_evidence', true);
  delete from public.paper_book_evidence where captured_at < v_before;
  perform set_config('arbdesk.archiving', '', true);

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'kept', v_keep,
    'older_than', v_before,
    'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.paper_book_evidence')),
    'note', 'request_reclaim returns the space the same night'
  );
end;
$function$;

-- SECURITY DEFINER and deletes rows: service_role only (plan v2 P1.1).
revoke execute on function public.prune_book_evidence(integer, boolean, timestamptz, bigint) from public, anon, authenticated;
grant execute on function public.prune_book_evidence(integer, boolean, timestamptz, bigint) to service_role;

comment on function public.prune_book_evidence(integer, boolean, timestamptz, bigint) is
  'Delete paper_book_evidence rows older than the cutoff (at least a day; no reader looks back more than 900 s), only when the caller''s count read back from the committed archive file matches exactly, by claiming the append-only guard''s single-table archive exemption around the delete.';


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
    'paper_book_evidence'];
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
    perform cron.schedule('ad4_reclaim_paper_book_evidence', '25 3 * * *',
                          'VACUUM (FULL, ANALYZE) public.paper_book_evidence');
  end if;
end $$;
