-- ===========================================================================
-- EACH MODEL'S PAST FORECASTS GO TO THE REPOSITORY (plan v2 P1.6 phase 2,
-- step 4, 29 Sep)
--
-- Hassan, 29 Sep: "Do phase 2 as planned" and "proceed pase 2 step 4".
--
-- 1. v_forecast_models_export: weather_forecast_models with its four-column
--    primary key joined into one text key, so the archive can keyset-page its
--    export. Service role only.
-- 2. prune_forecast_models: rows dated before the cutoff (at least 30 days
--    back) leave Postgres only after scripts/archive_observations.py has
--    committed them to data/archive/forecast_models and read them back, only
--    when the count matches exactly, and never a row observed since
--    yesterday's UTC midnight (the repo mirror copies those after the prune).
-- 3. request_reclaim: weather_forecast_models joins the allow-list (appended,
--    so the other tables keep their slots).
-- 4. A weekly backstop reclaim, Monday 06:50 (sql/ad4_66).
--
-- The bodies are the ones in sql/ad4_95_prune_forecast_models.sql and
-- sql/ad4_66_reclaim_archived_tables.sql. Nothing is pruned by this: the
-- archive's window for the table is 90 days (60 under storage pressure) and
-- its oldest row is dated 20 Aug. Re-runnable.
-- ===========================================================================

create or replace view public.v_forecast_models_export
with (security_invoker = true) as
select f.city_key || '|' || f.model || '|'
         || to_char(f.run_at at time zone 'UTC', 'YYYY-MM-DD"T"HH24:MI:SS.US') || '|' || f.for_date::text as model_key,
       f.*
  from public.weather_forecast_models f;

comment on view public.v_forecast_models_export is
  'weather_forecast_models with one unique, totally ordered text key (city_key|model|run_at|for_date, run_at in UTC to the microsecond) for the archive''s keyset-paged export. Service role only (plan v2 P1.6 phase 2).';

revoke all on public.v_forecast_models_export from public, anon, authenticated;
grant select on public.v_forecast_models_export to service_role;


create or replace function public.prune_forecast_models(
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
  -- the prune has run.
  v_unmirrored timestamptz := (date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC';
  v_doomed bigint;
  v_keep   bigint;
  v_young  bigint;
begin
  -- THIRTY DAYS, as weather_forecasts: station correction fits on 45 days and
  -- scores on 30 more, and below the keep it reads the archive, not this.
  if p_keep_days < 30 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 30 - the model forecasts are fitted over weeks'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune - it is the count '
               'read back from the committed archive file'
    );
  end if;

  -- A late write for an old date must become a count mismatch, not slip past
  -- the verified file.
  if not p_dry_run then
    lock table public.weather_forecast_models in share row exclusive mode;
  end if;

  select count(*), count(*) filter (where observed_at >= v_unmirrored)
    into v_doomed, v_young
    from public.weather_forecast_models where for_date < v_before;

  -- Never a row the mirror has not had yet, whatever the window.
  if v_young > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format('%s of the %s rows dated before %s were observed since %s and are not in '
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
                              'note', format('nothing dated before %s', v_before));
  end if;

  select count(*) into v_keep
    from public.weather_forecast_models where for_date >= v_before;

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

  delete from public.weather_forecast_models where for_date < v_before;

  return jsonb_build_object(
    'ok', true,
    'deleted', v_doomed,
    'kept', v_keep,
    'older_than', v_before,
    'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.weather_forecast_models')),
    'note', 'request_reclaim returns the space the same night'
  );
end;
$function$;

-- SECURITY DEFINER and deletes rows: service_role only (plan v2 P1.1).
revoke execute on function public.prune_forecast_models(integer, boolean, date, bigint) from public, anon, authenticated;
grant execute on function public.prune_forecast_models(integer, boolean, date, bigint) to service_role;

comment on function public.prune_forecast_models(integer, boolean, date, bigint) is
  'Delete weather_forecast_models rows dated before the cutoff (at least 30 days back), only when the caller''s count read back from the committed archive file matches exactly and none was observed since yesterday''s UTC midnight (the repo mirror copies those after the prune).';


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
    'weather_forecast_models'];
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
    perform cron.schedule('ad4_reclaim_weather_forecast_models', '50 6 * * 1',
                          'VACUUM (FULL, ANALYZE) public.weather_forecast_models');
  end if;
end $$;
