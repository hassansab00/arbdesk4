-- ===========================================================================
-- ad4_prune_live_capture.sql - THE LIVE RECORD AND ITS WAY OUT (WXPredict
-- build WP3, phase 2.2/2.3; Hassan, 11 Oct: "go ahead with live capture").
--
-- public.live_capture keeps every input the hourly tick had, as it arrived:
-- station reports with every field the training reads ('report'), the hourly
-- `_previous_day1` forecast ('hourly'), and every open bucket's book
-- ('books'); from 12 Oct also best_match at `_previous_day2` ('hourly2') and
-- the seven models' temperature at `_previous_day1` and `_previous_day2`
-- ('models1', 'models2'). scripts/live_capture.py writes it through
-- live_capture_put; the migrations 20261011210000_the_live_record.sql and
-- 20261012150000_the_live_record_keeps_the_evening_forecasts.sql say why each
-- kind is kept and how.
--
-- WHO READS IT: the nightly mirror (scripts/mirror_to_repo.py, append by
-- captured_at), the archive (scripts/archive_observations.py, dataset
-- live_capture) and the WXPredict live reader and parity test, which read the
-- repository's copies. No view, page or engine reads it.
--
-- WHAT IS KEPT: two days. prune_live_capture deletes rows captured before the
-- cutoff only after the archive has committed them to data/archive/live_capture
-- and read the count back, never a row captured since yesterday's UTC midnight
-- (the mirror copies a day after it ends), and exactly the count or nothing.
-- request_reclaim (sql/ad4_66) returns the space the same night.
--
-- Re-runnable. Service role only. Installed after ad4_66.
-- ===========================================================================

create table if not exists public.live_capture (
  capture_id   bigserial primary key,
  kind         text not null,
  key          text not null,
  captured_at  timestamptz not null,
  payload      jsonb not null,
  written_at   timestamptz not null default now()
);
create index if not exists live_capture_newest on public.live_capture (kind, key, captured_at desc, capture_id desc);
create index if not exists live_capture_captured_at on public.live_capture (captured_at);
-- The kinds it keeps, set here so a table made before 12 Oct widens too.
alter table public.live_capture drop constraint if exists live_capture_kind_check;
alter table public.live_capture add constraint live_capture_kind_check
  check (kind in ('report', 'hourly', 'hourly2', 'models1', 'models2', 'books'));

comment on table public.live_capture is
  'WXPredict WP3 (11 Oct): every live input the hourly tick had, as it arrived (captured_at): station reports with every training field, the hourly _previous_day1 forecast, every open bucket''s book; from 12 Oct also best_match at _previous_day2 and the seven models'' temperature at both leads (WP3b). A report or forecast value is stored once per change. Two days here; older rows in data/archive/live_capture.';

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
  if p_kind is null or p_kind not in ('report', 'hourly', 'hourly2', 'models1', 'models2', 'books') then
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
