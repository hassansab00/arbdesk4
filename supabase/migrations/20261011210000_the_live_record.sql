-- ===========================================================================
-- THE LIVE RECORD (WXPredict build WP3, phase 2.2/2.3; Hassan, 11 Oct: "go
-- ahead with live capture").
--
-- The training table knows when each input was knowable by RULE (a report
-- 20 minutes after its reading, an hourly forecast 17 hours before its hour,
-- the market at its hourly point). Nothing records what the live system
-- actually had at a decision, so nothing can show the rules hold live (the
-- parity test, phase 2.3) or judge a model on the inputs it would really have
-- had. This keeps, from the hourly tick, every input as it arrived, with the
-- time it arrived:
--
--   kind 'report'  every station report the tick's IEM read returned, all the
--                  fields the training reads (tools/wxpredict/fetch_obs.py
--                  FIELDS and the METAR text), key '<IEM id>|<valid UTC>'.
--                  The tick asks IEM once, as before; the answer is shared.
--   kind 'hourly'  Open-Meteo's best_match `_previous_day1` hourly values for
--                  every active city, the seven variables the training reads
--                  (honest_record.HEATING), for every UTC hour from yesterday
--                  to 17 h ahead, key '<city>|<hour UTC>'. Measured 11 Oct
--                  18:15Z: for an hour with no run from a day before, the API
--                  answers with the current forecast (Jeddah 13 Oct: 24 of 24
--                  hours equal to it), so what was served when is the record.
--   kind 'books'   every open bucket's YES book at the tick, one row a tick,
--                  key 'all': {"buckets": n, "complete": bool, "failed":
--                  [band ids whose request failed], "no_book": [band ids
--                  answered without a book], "tops": {band_id: [best bid,
--                  best ask, bid size, ask size, bid USD within 5c, ask USD
--                  within 5c, last trade, book time (s), min order size, tick
--                  size]}}. Every open bucket is in exactly one of tops,
--                  failed and no_book (Codex on #381).
--
-- captured_at is when the answer reached us. A 'report' or 'hourly' row is
-- kept only when it differs from the newest row of its key, so each value is
-- stored once with the first time it was seen, and a changed value is a new
-- row beside the old one (live_capture_put). A 'books' row is kept every tick.
--
-- KEPT TWO DAYS. scripts/archive_observations.py exports the older rows to
-- data/archive/live_capture, reads them back from the commit, and only then
-- prune_live_capture deletes exactly that count (never a row captured since
-- yesterday's UTC midnight, which the nightly mirror has not copied yet);
-- request_reclaim returns the space the same night, ad4_reclaim_live_capture
-- (03:05) is the backstop. The nightly mirror copies every row too.
--
-- The switch: settings.workflow_schedules.P2.12_live_capture (the Workflows
-- page). Off or manual stores nothing; the tick's own work is unchanged.
--
-- The bodies of prune_live_capture and request_reclaim are the ones in
-- sql/ad4_prune_live_capture.sql and sql/ad4_66_reclaim_archived_tables.sql.
-- Re-runnable. Service role only.
-- ===========================================================================

create table if not exists public.live_capture (
  capture_id   bigserial primary key,
  kind         text not null check (kind in ('report', 'hourly', 'books')),
  key          text not null,
  captured_at  timestamptz not null,
  payload      jsonb not null,
  written_at   timestamptz not null default now()
);
create index if not exists live_capture_newest on public.live_capture (kind, key, captured_at desc, capture_id desc);
create index if not exists live_capture_captured_at on public.live_capture (captured_at);

comment on table public.live_capture is
  'WXPredict WP3 (11 Oct): every live input the hourly tick had, as it arrived (captured_at): station reports with every training field, the hourly _previous_day1 forecast, every open bucket''s book. A report or hourly value is stored once per change. Two days here; older rows in data/archive/live_capture.';

alter table public.live_capture enable row level security;
revoke all on public.live_capture from public, anon, authenticated;
grant select on public.live_capture to service_role;
revoke all on sequence public.live_capture_capture_id_seq from public, anon, authenticated;

-- Its switch on the Workflows page. every_minutes 0: the tick sets the
-- cadence and it costs no n8n execution. Only added when missing, so a mode
-- set on the page survives a re-run.
do $lc$ begin
  update public.settings set value = value || jsonb_build_object('P2.12_live_capture',
    jsonb_build_object('mode', 'auto', 'every_minutes', 0))
   where key = 'workflow_schedules' and not (value ? 'P2.12_live_capture');
end $lc$;


-- THE WATCHDOG: the archive logs one archive_<dataset> row a night, as every
-- dataset's does. P2.12_live_capture itself joins job_sla after its first
-- days, as R46 and WP4 did.
insert into public.job_sla (job, cadence_minutes, cadence, scheduled_by) values
  ('archive_live_capture', 1440, 'daily at 02 UTC', 'archive_observations.yml, dispatched by the clock')
on conflict (job) do update set cadence_minutes = excluded.cadence_minutes, cadence = excluded.cadence,
                                scheduled_by = excluded.scheduled_by;

-- REGISTERED, NOT YET DUE. The watchdog counts a job with an SLA and no ok row
-- as overdue, and no live_capture row is two days old before 13 Oct, so the
-- first archive with anything to do is 13 Oct 02:36Z. One row says so, in its
-- own words, written only while the job has no row at all: the archive's own
-- rows replace it from its first run.
do $lc$ begin
  if to_regprocedure('public.log_ingest(text,text,integer,jsonb)') is not null
     and not exists (select 1 from public.ingest_log where job = 'archive_live_capture') then
    perform public.log_ingest('archive_live_capture', 'ok', 0, jsonb_build_object(
      'registered', true, 'by', '20261011210000_the_live_record.sql',
      'note', 'a new dataset: no row is two days old before its first archive run (13 Oct 02:36Z)'));
  end if;
end $lc$;

-- FRESHNESS: the browser's v_data_freshness gets a live_capture row (the
-- tick writes a books row every hour; stale past 2 h), as
-- sql/ad4_39_freshness.sql lists it. The view is assembled from
-- data_freshness_spec, so it is rebuilt here by that file's own generator,
-- CREATE OR REPLACE so its grants and v_data_health stay bound. Rehearsed
-- 11 Oct ~21:10Z in one REPEATABLE READ snapshot, rolled back: every one of
-- the 68 rows identical both ways (EXCEPT ALL), one row added, anon reads
-- all 69. Skipped where the spec or its helpers are absent (the migration
-- test harness).
do $fr$ begin
  if to_regclass('public.data_freshness_spec') is not null then
    insert into public.data_freshness_spec (table_name, ts_column, fresh_hours, layer, plain_english) values
      ('live_capture', 'captured_at', 2, 'databank', 'What the live system had at each hourly tick, as it arrived: station reports, the hourly forecast and every open bucket''s book. Kept two days; older rows are in the repository.')
    on conflict (table_name) do nothing;
  end if;
end $fr$;

do $ad4$
declare
  r     record;
  parts text[] := '{}';
  sql   text;
begin
  if to_regclass('public.data_freshness_spec') is null
     or to_regprocedure('public.ad4_rowcount_expr(text)') is null then
    return;
  end if;
  for r in select * from data_freshness_spec order by table_name loop
    if to_regclass('public.' || r.table_name) is null then
      -- ABSENT is a real answer, and a more useful one than a missing row.
      --
      -- rows_estimated is null::boolean and sits between rows and newest
      -- because UNION ALL matches by POSITION, not by name, and this branch
      -- has to line up with the other two. It did not, and it took the whole
      -- view down with it: one absent table out of forty-eight, and the
      -- `create view` at the bottom of this block failed with "each UNION
      -- query must have the same number of columns". No view, so the browser
      -- got an error instead of freshness rows and every page printed
      -- "Freshness tracking is not installed - run sql/ad4_39_freshness.sql"
      -- - a file that could not, in fact, be run. This is the branch that
      -- only fires on a database missing a table, so it survived every
      -- install where nothing was missing, which is the only kind of install
      -- that never needed it.
      parts := parts || format(
        $q$select %L::text as table_name, %L::text as layer, %L::text as plain_english,
                  null::bigint as rows, null::boolean as rows_estimated,
                  null::timestamptz as newest,
                  null::numeric as age_hours, %s::numeric as fresh_hours,
                  'absent'::text as state$q$,
        r.table_name, r.layer, r.plain_english, coalesce(r.fresh_hours::text, 'null'));
    elsif r.ts_column is null or r.ts_column = '-' then
      parts := parts || format(
        $q$select %L::text as table_name, %L::text as layer, %L::text as plain_english,
                  %s as rows, %s as rows_estimated,
                  null::timestamptz as newest, null::numeric as age_hours,
                  %s::numeric as fresh_hours,
                  (case when not exists (select 1 from public.%I) then 'empty' else 'ok' end)::text as state$q$,
        r.table_name, r.layer, r.plain_english,
        ad4_rowcount_expr(r.table_name), ad4_rowcount_is_estimate(r.table_name),
        coalesce(r.fresh_hours::text, 'null'), r.table_name);
    else
      parts := parts || format(
        $q$select %L::text as table_name, %L::text as layer, %L::text as plain_english,
                  %s as rows, %s as rows_estimated,
                  t.newest,
                  round(extract(epoch from (now() - t.newest)) / 3600.0, 1) as age_hours,
                  %s::numeric as fresh_hours,
                  (case when not exists (select 1 from public.%I) then 'empty'
                        when t.newest is null then 'ok'
                        when %s is null then 'ok'
                        when extract(epoch from (now() - t.newest)) / 3600.0 > %s then 'stale'
                        else 'ok' end)::text as state
             from (select max(%I) newest from public.%I) t$q$,
        r.table_name, r.layer, r.plain_english,
        ad4_rowcount_expr(r.table_name), ad4_rowcount_is_estimate(r.table_name),
        coalesce(r.fresh_hours::text, 'null'),
        r.table_name,
        coalesce(r.fresh_hours::text, 'null'),
        coalesce(r.fresh_hours::text, '1e9'),
        r.ts_column, r.table_name);
    end if;
  end loop;

  sql := 'create or replace view v_data_freshness as ' || array_to_string(parts, E'\nunion all\n');
  execute sql;
end
$ad4$;

-- One batch of one kind: p_rows is [{"key": ..., "payload": ...}]. A report
-- or hourly row whose payload equals its key's newest stored payload is not
-- stored again; a key repeated inside the batch is stored once per distinct
-- payload. Returns {"rows": asked, "new": stored}.
create or replace function public.live_capture_put(p_kind text, p_captured_at timestamptz, p_rows jsonb)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $lc$
declare
  v_rows int;
  v_new  int;
begin
  if p_kind is null or p_kind not in ('report', 'hourly', 'books') then
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
                                now() - make_interval(days => p_keep_days));
  -- Tonight's mirror copies every row captured since this instant, after the
  -- prune has run.
  v_unmirrored timestamptz := (date_trunc('day', now() at time zone 'UTC') - interval '1 day') at time zone 'UTC';
  v_doomed bigint;
  v_young  bigint;
  v_gone   bigint;
begin
  -- TWO DAYS IS THE FLOOR: two days back is always before yesterday's UTC
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
  'Delete the live_capture rows captured before the cutoff (at least 2 days back), only when the caller''s count read back from the committed archive file matches exactly and none was captured since yesterday''s UTC midnight (WXPredict WP3).';


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
    'derived_city_correlation', 'derived_model_forecast',
    -- appended (11 Oct, WXPredict WP3)
    'live_capture'];
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
    perform cron.schedule('ad4_reclaim_live_capture', '5 3 * * *',
                          'VACUUM (FULL, ANALYZE) public.live_capture');
  end if;
end $$;

notify pgrst, 'reload schema';
