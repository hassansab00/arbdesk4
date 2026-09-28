-- ===========================================================================
-- A WEEK-OLD SIGNAL'S DECISION INPUTS GO TO THE REPOSITORY (plan v2 P1.6
-- phase 1, step 4, 28 Sep)
--
-- Hassan, 28 Sep: "YES APPROVED as long as we dont lose any collected data".
--
-- 1. v_signal_inputs_export: each signal's decision_inputs while it is still
--    in the payload, for the archive's export. Service role only.
-- 2. prune_signal_inputs: strips payload.decision_inputs from signals 7 or
--    more days old, only after scripts/archive_observations.py has committed
--    them to data/archive/signal_inputs and read them back, and only when the
--    count matches exactly. The rows and every other payload key stay (the
--    board reads band_ids and basket_group, the databank decision_snapshot);
--    the payload is marked decision_inputs_in_repo; research capture is off
--    for the strip. The key's readers need 16 minutes.
-- 3. request_reclaim: signals joins the allow-list (appended, so the other
--    tables keep their slots).
-- 4. A daily backstop reclaim at 03:40 (sql/ad4_66).
--
-- The bodies are the ones in sql/ad4_94_prune_signal_inputs.sql and
-- sql/ad4_66_reclaim_archived_tables.sql. Re-runnable.
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
    'paper_book_evidence', 'weather_forecast_features', 'signals'];
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
    perform cron.schedule('ad4_reclaim_signals', '40 3 * * *',
                          'VACUUM (FULL, ANALYZE) public.signals');
  end if;
end $$;
