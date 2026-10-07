-- ===========================================================================
-- A WEEK-OLD FORECAST'S PAYLOAD GOES TO THE REPOSITORY; THE ROW AND ITS
-- NUMBERS STAY (WXPredict build 2.A; Hassan, 7 Oct: "proceed to the next
-- storage cut", as long as no proprietary data is lost).
--
-- Measured 7 Oct 14:59Z:
--   weather_forecasts.variables          13.59 MiB of 24.08 MiB of row data;
--                                        53,364 rows dated over 7 days back
--                                        carry 8.07 MiB of it
--   derived_model_forecast.contributions 10.47 of 14.90 MiB; 11,665 rows
--     and .inputs                        dated over 7 days back carry 3.62
--                                        MiB; never pruned, ~1,600 rows a day
-- Nothing opens variables (v_forecast_issued passes the column through;
-- every reader of it selects named columns); contributions and inputs are
-- read by v_model_forecast_current only, from yesterday on.
-- sql/ad4_prune_forecast_payloads.sql lists every reader.
--
-- 1. v_forecast_variables_export, prune_forecast_variables: the payload of
--    rows dated at least 7 days back, exported by the table's natural key,
--    then the row marked {"variables_in_repo": true}; the exact verified
--    count or nothing; never a row the mirror has not had.
-- 2. v_model_payloads_export, prune_model_payloads: the same for
--    derived_model_forecast, contributions null and inputs
--    {"payload_in_repo": true}. Only where the table exists (it is created
--    by sql/ad4_25, which the contracts' fixture does not apply).
-- 3. request_reclaim takes derived_model_forecast (appended); a weekly
--    backstop reclaim, Monday 07:15 (sql/ad4_66).
--
-- The bodies are the ones in sql/ad4_prune_forecast_payloads.sql and
-- sql/ad4_66_reclaim_archived_tables.sql. Nothing is stripped by this
-- migration; the archive's next run does it. Re-runnable.
-- ===========================================================================

create or replace view public.v_forecast_variables_export
with (security_invoker = true) as
select f.forecast_id, f.city_key, f.model, f.run_at, f.observed_at, f.for_date, f.source, f.variables
  from public.weather_forecasts f
 where f.variables is not null
   and not (f.variables ? 'variables_in_repo');

comment on view public.v_forecast_variables_export is
  'Each forecast''s variables payload while it is still on the row, for the archive''s export; a row leaves the view once prune_forecast_variables has moved it to the repository. Service role only (WXPredict build 2.A).';

revoke all on public.v_forecast_variables_export from public, anon, authenticated;
grant select on public.v_forecast_variables_export to service_role;


create or replace function public.prune_forecast_variables(
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
  -- Tonight's mirror exports every row observed since this instant, after
  -- the strip has run.
  v_unmirrored timestamptz := (date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC';
  v_doomed bigint;
  v_young  bigint;
  v_keep   bigint;
  v_done   bigint;
begin
  -- SEVEN DAYS: nothing opens the payload, and a week keeps the newest
  -- forecasts whole for anyone looking at them by hand.
  if p_keep_days is null or p_keep_days < 7 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 7 - the newest week of forecasts keeps its payload'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune - it is the count '
               'read back from the committed archive file'
    );
  end if;

  -- An ingest landing between the count and the strip must become a count
  -- mismatch, not slip past the file.
  if not p_dry_run then
    lock table public.weather_forecasts in share row exclusive mode;
  end if;

  select count(*), count(*) filter (where observed_at >= v_unmirrored)
    into v_doomed, v_young
    from public.v_forecast_variables_export where for_date < v_before;

  -- Never a payload the mirror has not had, whatever the window.
  if v_young > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format('%s of the %s payloads dated before %s were observed since %s and are not in '
                      'the repo mirror yet - nothing stripped', v_young, v_doomed, v_before, v_unmirrored),
      'would_delete', v_doomed,
      'not_yet_mirrored', v_young
    );
  end if;

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
                              'note', format('no forecast payloads dated before %s', v_before));
  end if;

  select count(*) into v_keep from public.v_forecast_variables_export where for_date >= v_before;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true, 'dry_run', true,
      'would_delete', v_doomed,
      'would_keep', v_keep,
      'older_than', v_before,
      'expected_rows', p_expected_rows,
      'note', 'moves variables to the repository; the rows and their numbers stay. Call again with p_dry_run => false'
    );
  end if;

  update public.weather_forecasts f
     set variables = jsonb_build_object('variables_in_repo', true)
   where f.for_date < v_before
     and exists (select 1 from public.v_forecast_variables_export x where x.forecast_id = f.forecast_id);
  get diagnostics v_done = row_count;
  -- Exactly the payloads counted and verified in the file, or none at all.
  if v_done <> v_doomed then
    raise exception 'prune_forecast_variables: counted % payloads, the strip took % - nothing stripped', v_doomed, v_done;
  end if;

  -- 'deleted' is the name the archive reads its count back under: here it is
  -- payloads moved, not rows.
  return jsonb_build_object(
    'ok', true,
    'deleted', v_done,
    'stripped', v_done,
    'kept', v_keep,
    'older_than', v_before,
    'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.weather_forecasts')),
    'note', 'variables moved to the repository, rows kept; request_reclaim returns the space the same night'
  );
end;
$function$;

-- SECURITY DEFINER and rewrites rows: service_role only (plan v2 P1.1).
revoke execute on function public.prune_forecast_variables(integer, boolean, date, bigint) from public, anon, authenticated;
grant execute on function public.prune_forecast_variables(integer, boolean, date, bigint) to service_role;

comment on function public.prune_forecast_variables(integer, boolean, date, bigint) is
  'Move weather_forecasts.variables of rows dated before the cutoff (at least 7 days back) to the repository, marking the row {"variables_in_repo": true}, only when the caller''s count read back from the committed archive file matches exactly and none was observed since yesterday''s UTC midnight. The rows and their numbers stay (WXPredict build 2.A).';


do $models$
begin
  if to_regclass('public.derived_model_forecast') is null then
    raise notice '2.A: derived_model_forecast is not installed here (sql/ad4_25); no payload export';
    return;
  end if;
  execute $v$
    create or replace view public.v_model_payloads_export
    with (security_invoker = true) as
    select f.city_key || '|' || f.for_date::text || '|'
             || to_char(f.run_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') as payload_key,
           f.city_key, f.for_date, f.run_at, f.predicted_at, f.contributions, f.inputs
      from public.derived_model_forecast f
     where (f.contributions is not null or f.inputs is not null)
       and not coalesce(f.inputs ? 'payload_in_repo', false)
  $v$;
  comment on view public.v_model_payloads_export is
    'Each model forecast''s contributions and inputs while they are still on the row, with the primary key joined into one text key (city_key|for_date|run_at, UTC to the microsecond) for the archive''s keyset-paged export; a row leaves the view once prune_model_payloads has moved them to the repository. Service role only (WXPredict build 2.A).';
  revoke all on public.v_model_payloads_export from public, anon, authenticated;
  grant select on public.v_model_payloads_export to service_role;
end
$models$;


create or replace function public.prune_model_payloads(
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
  -- Tonight's mirror exports every row predicted since this instant, after
  -- the strip has run.
  v_unmirrored timestamptz := (date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC';
  v_doomed bigint;
  v_young  bigint;
  v_keep   bigint;
  v_done   bigint;
begin
  -- SEVEN DAYS: the one reader of the payloads, v_model_forecast_current,
  -- reads yesterday on.
  if p_keep_days is null or p_keep_days < 7 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 7 - v_model_forecast_current reads the payloads from yesterday on'
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
    lock table public.derived_model_forecast in share row exclusive mode;
  end if;

  select count(*), count(*) filter (where predicted_at >= v_unmirrored)
    into v_doomed, v_young
    from public.v_model_payloads_export where for_date < v_before;

  -- Never a payload the mirror has not had, whatever the window.
  if v_young > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format('%s of the %s payloads dated before %s were predicted since %s and are not in '
                      'the repo mirror yet - nothing stripped', v_young, v_doomed, v_before, v_unmirrored),
      'would_delete', v_doomed,
      'not_yet_mirrored', v_young
    );
  end if;

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
                              'note', format('no model forecast payloads dated before %s', v_before));
  end if;

  select count(*) into v_keep from public.v_model_payloads_export where for_date >= v_before;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true, 'dry_run', true,
      'would_delete', v_doomed,
      'would_keep', v_keep,
      'older_than', v_before,
      'expected_rows', p_expected_rows,
      'note', 'moves contributions and inputs to the repository; the rows and their numbers stay. Call again with p_dry_run => false'
    );
  end if;

  update public.derived_model_forecast f
     set contributions = null,
         inputs = jsonb_build_object('payload_in_repo', true)
   where f.for_date < v_before
     and exists (select 1 from public.v_model_payloads_export x
                  where x.city_key = f.city_key and x.for_date = f.for_date and x.run_at = f.run_at);
  get diagnostics v_done = row_count;
  -- Exactly the payloads counted and verified in the file, or none at all.
  if v_done <> v_doomed then
    raise exception 'prune_model_payloads: counted % payloads, the strip took % - nothing stripped', v_doomed, v_done;
  end if;

  return jsonb_build_object(
    'ok', true,
    'deleted', v_done,
    'stripped', v_done,
    'kept', v_keep,
    'older_than', v_before,
    'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.derived_model_forecast')),
    'note', 'contributions and inputs moved to the repository, rows kept; request_reclaim returns the space the same night'
  );
end;
$function$;

-- SECURITY DEFINER and rewrites rows: service_role only (plan v2 P1.1).
revoke execute on function public.prune_model_payloads(integer, boolean, date, bigint) from public, anon, authenticated;
grant execute on function public.prune_model_payloads(integer, boolean, date, bigint) to service_role;

comment on function public.prune_model_payloads(integer, boolean, date, bigint) is
  'Move derived_model_forecast.contributions and inputs of rows dated before the cutoff (at least 7 days back) to the repository, marking inputs {"payload_in_repo": true}, only when the caller''s count read back from the committed archive file matches exactly and none was predicted since yesterday''s UTC midnight. The rows and their numbers stay (WXPredict build 2.A).';


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
    'derived_city_correlation', 'derived_model_forecast'];
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
  if exists (select 1 from pg_namespace where nspname = 'cron')
     and to_regclass('public.derived_model_forecast') is not null then
    perform cron.schedule('ad4_reclaim_derived_model_forecast', '15 7 * * 1',
                          'VACUUM (FULL, ANALYZE) public.derived_model_forecast');
  end if;
end $$;
