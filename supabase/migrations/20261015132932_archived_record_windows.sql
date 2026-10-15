-- Preserve exact first-decision feature inputs; narrow archived hot windows.
-- Service-role RPCs retain their existing grants and exact-count guards.
-- No capture or model payload is deleted by applying this migration.
alter table public.live_capture drop constraint if exists live_capture_kind_check;
alter table public.live_capture add constraint live_capture_kind_check
  check (kind in ('report', 'hourly', 'hourly2', 'models1', 'models2', 'books', 'focus_book', 'wx_features'));

comment on table public.live_capture is
  'WXPredict WP3 (11 Oct): every live input the hourly tick had, as it arrived (captured_at): station reports with every training field, the hourly _previous_day1 forecast, every open bucket''s book; from 12 Oct also best_match at _previous_day2 and the seven models'' temperature at both leads (WP3b), and the focus cities'' same-day books every minute of their window (focus_book, WP5). A report, forecast value or focus book is stored once per change. One mirrored UTC-day boundary here; older rows in data/archive/live_capture.';

-- One batch of one kind: p_rows is [{"key": ..., "payload": ...}]. A report
-- or forecast row whose payload equals its key's newest stored payload is not
-- stored again; a key repeated inside the batch is stored once per distinct
-- payload. Returns {"rows": asked, "new": stored}.
create or replace function public.live_capture_put(p_kind text, p_captured_at timestamptz, p_rows jsonb)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $lc$
declare
  v_rows int;
  v_new  int;
begin
  if p_kind is null or p_kind not in ('report', 'hourly', 'hourly2', 'models1', 'models2', 'books', 'focus_book', 'wx_features') then
    raise exception 'live_capture_put: % is not a kind the live record keeps', p_kind using errcode = '22023';
  end if;
  if p_captured_at is null or p_captured_at > now() + interval '5 minutes' then
    raise exception 'live_capture_put: captured_at % is missing or in the future', p_captured_at using errcode = '22023';
  end if;
  if p_rows is null or jsonb_typeof(p_rows) <> 'array' then
    raise exception 'live_capture_put: p_rows must be a JSON array' using errcode = '22023';
  end if;

  with r as (
    select distinct x ->> 'key' as key, x -> 'payload' as payload
      from jsonb_array_elements(p_rows) x
     where x ->> 'key' is not null and x -> 'payload' is not null
  ), ins as (
    insert into public.live_capture (kind, key, captured_at, payload)
    select p_kind, r.key, p_captured_at, r.payload
      from r
     where p_kind = 'books'
        or r.payload is distinct from (
             select l.payload from public.live_capture l
              where l.kind = p_kind and l.key = r.key
              order by l.captured_at desc, l.capture_id desc
              limit 1)
    returning 1
  )
  select (select count(*) from jsonb_array_elements(p_rows)), (select count(*) from ins)
    into v_rows, v_new;
  return jsonb_build_object('rows', v_rows, 'new', v_new);
end;
$lc$;

revoke all on function public.live_capture_put(text, timestamptz, jsonb) from public, anon, authenticated;
grant execute on function public.live_capture_put(text, timestamptz, jsonb) to service_role;


-- Narrow only records whose consumers read the repository or yesterday on.
-- No row is pruned here. Existing archive export/commit/read-back/count guards
-- remain mandatory. Live capture is capped at the mirrored UTC-day boundary.
create or replace function public.prune_live_capture(
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
                                now() - make_interval(days => p_keep_days),
                                (date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC');
  -- Tonight's mirror copies every row captured since this instant, after the
  -- prune has run.
  v_unmirrored timestamptz := (date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC';
  v_doomed bigint;
  v_young  bigint;
  v_gone   bigint;
begin
  -- One day, capped at yesterday's UTC midnight. The mirror must have that
  -- complete day, and the caller must prove the exact committed archive count.
  if p_keep_days is null or p_keep_days < 1 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 1 - the repo mirror copies a day after it ends'
    );
  end if;

  if not p_dry_run and p_expected_rows is null then
    return jsonb_build_object(
      'ok', false,
      'error', 'p_expected_rows is required for a committed prune - it is the count '
               'read back from the committed archive file'
    );
  end if;

  -- A tick writing between the count and the delete must not slip a row past
  -- the file: the count and the delete see the same rows.
  if not p_dry_run then
    lock table public.live_capture in share row exclusive mode;
  end if;

  select count(*), count(*) filter (where captured_at >= v_unmirrored)
    into v_doomed, v_young
    from public.live_capture where captured_at < v_before;

  if v_young > 0 then
    return jsonb_build_object(
      'ok', false,
      'error', format('%s of the %s rows before %s were captured since %s and are not in the repo '
                      'mirror yet - nothing deleted', v_young, v_doomed, v_before, v_unmirrored),
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
                              'note', format('no live capture older than %s', v_before));
  end if;

  if p_dry_run then
    return jsonb_build_object(
      'ok', true, 'dry_run', true,
      'would_delete', v_doomed,
      'rows_now', (select count(*) from public.live_capture),
      'older_than', v_before,
      'expected_rows', p_expected_rows,
      'note', 'call again with p_dry_run => false to actually delete'
    );
  end if;

  delete from public.live_capture where captured_at < v_before;
  get diagnostics v_gone = row_count;
  -- Exactly the rows counted and verified in the file, or none at all.
  if v_gone <> v_doomed then
    raise exception 'prune_live_capture: counted % rows, the delete took % - nothing deleted', v_doomed, v_gone;
  end if;

  return jsonb_build_object(
    'ok', true,
    'deleted', v_gone,
    'rows_now', (select count(*) from public.live_capture),
    'older_than', v_before,
    'expected_rows', p_expected_rows,
    'table_now', pg_size_pretty(pg_total_relation_size('public.live_capture')),
    'note', 'request_reclaim returns the space the same night'
  );
end;
$function$;

-- SECURITY DEFINER and deletes rows: service_role only (plan v2 P1.1).
revoke execute on function public.prune_live_capture(integer, boolean, timestamptz, bigint) from public, anon, authenticated;
grant execute on function public.prune_live_capture(integer, boolean, timestamptz, bigint) to service_role;

comment on function public.prune_live_capture(integer, boolean, timestamptz, bigint) is
  'Delete the live_capture rows captured before the cutoff (at least 1 day back, capped at yesterday UTC midnight), only when the caller''s count read back from the committed archive file matches exactly and none was captured since yesterday''s UTC midnight (WXPredict WP3).';

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
  -- TWO DAYS: the one reader of the payloads, v_model_forecast_current,
  -- reads yesterday on.
  if p_keep_days is null or p_keep_days < 2 then
    return jsonb_build_object(
      'ok', false,
      'error', 'keep_days must be at least 2 - v_model_forecast_current reads the payloads from yesterday on'
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
  'Move derived_model_forecast.contributions and inputs of rows dated before the cutoff (at least 2 days back) to the repository, marking inputs {"payload_in_repo": true}, only when the caller''s count read back from the committed archive file matches exactly and none was predicted since yesterday''s UTC midnight. The rows and their numbers stay (WXPredict build 2.A).';

-- The existing reclaim job follows the existing daily archive, off peak.
do $reclaim$ begin
  if to_regclass('cron.job') is not null then
    perform cron.schedule('ad4_reclaim_derived_model_forecast', '15 7 * * *',
                          'VACUUM (FULL, ANALYZE) public.derived_model_forecast');
  end if;
end $reclaim$;

-- The candidate records a decision even while waiting for independent data.
insert into public.job_sla(job, cadence_minutes, cadence, scheduled_by)
values ('P7.9_candidate_cycle', 1440, 'daily after pipeline_daily core', 'GitHub Actions pipeline_daily.yml')
on conflict (job) do update set cadence_minutes=excluded.cadence_minutes,
  cadence=excluded.cadence, scheduled_by=excluded.scheduled_by;
do $register$ begin
  if to_regprocedure('public.log_ingest(text,text,integer,jsonb)') is not null then
    if not exists(select 1 from public.ingest_log where job='P7.9_candidate_cycle') then
      perform public.log_ingest('P7.9_candidate_cycle', 'ok', 0,
        '{"registered":true,"note":"First scheduled candidate planning run is pending; no fit or promotion claimed."}'::jsonb);
    end if;
  end if;
end $register$;
notify pgrst, 'reload schema';
