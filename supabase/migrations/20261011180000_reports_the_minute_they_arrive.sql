-- ===========================================================================
-- REPORTS THE MINUTE THEY ARRIVE (WXPredict build WP4, the focused capture;
-- Hassan, 11 Oct: "yes add the poller").
--
-- The station reports reach the platform once an hour: the tick reads IEM at
-- :36 UTC, a median 36 minutes after the reading (p90 66; the 10-11 Oct
-- mirror of weather_observations). Whether a report is worth anything to a
-- same-day trade depends on WHEN it can be known, and nothing records that.
-- This polls Aviation Weather's METAR feed every minute while a pilot
-- station's day is warming (10:00-17:00 local) and keeps every distinct report
-- with three times:
--
--   obs_time            the reading's own time (the METAR's)
--   source_receipt_at   when Aviation Weather received it (its receiptTime)
--   first_seen_at       when OUR poll's answer first carried it (pg_net's
--                       response time; the poll is asked each minute)
--
-- Pilots: Jeddah (OEJN) and Istanbul (LTFM), both settled on NOAA's
-- timeseries of the same station, in whole C. Exploratory, not a claim that
-- they pay best (the Codex action plan of 11 Oct, section 4). Measured
-- 11 Oct 17:2xZ: one request answers both stations; LTFM reports at :20 and
-- :50, OEJN at :00; the receipt follows the reading by 22 s (OEJN 17:00Z) to
-- 5 min 10 s (LTFM 17:20Z).
--
--   focus_obs_stations  which stations, which local hours. A row, not code,
--                       so the pilot can widen without a migration.
--   focus_obs_polls     every question asked and how it was answered: the
--                       record of coverage (a missed or failed minute shows).
--   focus_obs_reports   every distinct report, once: (icao, source, md5 of
--                       the raw text). A correction (COR) has different text,
--                       so it is a new row beside the original, never in its
--                       place; the raw text and the whole answer are kept.
--
--   focus_obs_poll()    each minute (pg_cron, 07-14 UTC): reads the answers
--                       already back, then asks for the stations whose local
--                       window is open. Off or manual on the Workflows page
--                       (settings.workflow_schedules.P2.11_focus_obs) asks
--                       nothing. Silent: it writes no ingest_log row a minute.
--   focus_obs_day()     23:55 UTC: one ingest_log row (P2.11_focus_obs) for
--                       the UTC day - polls asked, answered, failed, reports,
--                       new reports, and per station the minutes from the
--                       reading and from the source's receipt to ours. It
--                       joins job_sla after its first days, as R46 did.
--
-- Cost: about 480 runs a day in cron.job_run_details (7,091 rows since
-- 15 Sep before this); 2 stations x ~3 reports an hour x 7 hours. Nothing is
-- pruned; the nightly mirror copies both tables (append, by their time).
--
-- Re-runnable. On a database without pg_cron or pg_net (the migration test
-- harness) the tables and functions are created and nothing is scheduled.
-- Service role only.
-- ===========================================================================

create table if not exists public.focus_obs_stations (
  icao        text primary key,
  city_key    text not null,
  timezone    text not null,
  local_from  smallint not null default 10 check (local_from between 0 and 23),
  local_to    smallint not null default 17 check (local_to between 1 and 24),
  active      boolean not null default true,
  note        text,
  updated_at  timestamptz not null default now(),
  check (local_to > local_from)
);

insert into public.focus_obs_stations (icao, city_key, timezone, local_from, local_to, note) values
  ('OEJN', 'jeddah', 'Asia/Riyadh', 10, 17, 'WP4 pilot, primary (11 Oct)'),
  ('LTFM', 'istanbul', 'Europe/Istanbul', 10, 17, 'WP4 pilot, comparison (11 Oct)')
on conflict (icao) do nothing;

create table if not exists public.focus_obs_polls (
  request_id   bigint primary key,
  source       text not null default 'awc',
  stations     text[] not null,
  asked_at     timestamptz not null default now(),
  answered_at  timestamptz,
  status_code  int,
  timed_out    boolean,
  error        text,
  n_reports    int,
  n_new        int,
  read_at      timestamptz
);

create table if not exists public.focus_obs_reports (
  report_id          bigserial primary key,
  icao               text not null,
  source             text not null,
  obs_time           timestamptz not null,
  report_time        timestamptz,
  source_receipt_at  timestamptz,
  first_seen_at      timestamptz not null,
  first_request_id   bigint not null,
  metar_type         text,
  raw_ob             text not null,
  raw_md5            text not null,
  temp_c             numeric,
  dewp_c             numeric,
  payload            jsonb not null,
  unique (icao, source, raw_md5)
);
create index if not exists focus_obs_reports_station_time on public.focus_obs_reports (icao, obs_time);

comment on table public.focus_obs_reports is
  'WP4 (11 Oct): every distinct METAR of a focus station, once, with the reading time, the source''s receipt time and our first receipt (first_seen_at). A correction is a new row beside the original.';

-- Reads every answer pg_net has back for a poll not yet read. One answer that
-- will not parse is recorded on its poll and does not stop the others.
create or replace function public.focus_obs_read()
returns int language plpgsql security definer set search_path = public, pg_temp as $fo$
declare
  r      record;
  v_rep  jsonb;
  v_n    int;
  v_new  int;
  v_all  int := 0;
  v_err  text;
begin
  if to_regclass('net._http_response') is null then
    return 0;
  end if;
  for r in
    execute 'select p.request_id, p.source, h.status_code, h.content, h.timed_out, h.error_msg, h.created
               from public.focus_obs_polls p join net._http_response h on h.id = p.request_id
              where p.read_at is null order by p.request_id'
  loop
    v_n := 0; v_new := 0; v_err := r.error_msg;
    if r.status_code = 200 and r.content is not null then
      begin
        for v_rep in select value from jsonb_array_elements(r.content::jsonb) loop
          continue when v_rep ->> 'rawOb' is null or v_rep ->> 'icaoId' is null;
          v_n := v_n + 1;
          insert into public.focus_obs_reports
            (icao, source, obs_time, report_time, source_receipt_at, first_seen_at, first_request_id,
             metar_type, raw_ob, raw_md5, temp_c, dewp_c, payload)
          values
            (v_rep ->> 'icaoId', r.source, to_timestamp((v_rep ->> 'obsTime')::double precision),
             (v_rep ->> 'reportTime')::timestamptz, (v_rep ->> 'receiptTime')::timestamptz, r.created,
             r.request_id, v_rep ->> 'metarType', v_rep ->> 'rawOb', md5(v_rep ->> 'rawOb'),
             (v_rep ->> 'temp')::numeric, (v_rep ->> 'dewp')::numeric, v_rep)
          on conflict (icao, source, raw_md5) do nothing;
          if found then
            v_new := v_new + 1;
          end if;
        end loop;
      exception when others then
        v_err := coalesce(v_err || '; ', '') || 'unreadable answer: ' || sqlerrm;
      end;
    end if;
    update public.focus_obs_polls
       set answered_at = r.created, status_code = r.status_code, timed_out = r.timed_out,
           error = v_err, n_reports = v_n, n_new = v_new, read_at = now()
     where request_id = r.request_id;
    v_all := v_all + v_new;
  end loop;
  return v_all;
end;
$fo$;

create or replace function public.focus_obs_poll(p_force boolean default false)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $fo$
declare
  v_new  int := public.focus_obs_read();
  v_mode text;
  v_ids  text[];
  v_req  bigint;
begin
  select coalesce(value -> 'P2.11_focus_obs' ->> 'mode', 'auto') into v_mode
    from public.settings where key = 'workflow_schedules';
  if not p_force and coalesce(v_mode, 'auto') in ('off', 'manual') then
    return jsonb_build_object('asked', false, 'read_new', v_new, 'reason', 'switched ' || v_mode);
  end if;
  select array_agg(s.icao order by s.icao) into v_ids
    from public.focus_obs_stations s
   where s.active
     and (p_force or (extract(hour from now() at time zone s.timezone) >= s.local_from
                      and extract(hour from now() at time zone s.timezone) < s.local_to));
  if v_ids is null then
    return jsonb_build_object('asked', false, 'read_new', v_new, 'reason', 'no window open');
  end if;
  if to_regprocedure('net.http_get(text,jsonb,jsonb,integer)') is null then
    return jsonb_build_object('asked', false, 'read_new', v_new, 'reason', 'no pg_net');
  end if;
  execute 'select net.http_get(url := $1, timeout_milliseconds := 10000)'
     into v_req
    using 'https://aviationweather.gov/api/data/metar?format=json&hours=3&ids=' || array_to_string(v_ids, ',');
  insert into public.focus_obs_polls (request_id, stations) values (v_req, v_ids);
  return jsonb_build_object('asked', true, 'read_new', v_new, 'request_id', v_req, 'stations', v_ids);
end;
$fo$;

-- One row a UTC day in ingest_log: what was asked, what came back, and how
-- late each report reached us.
create or replace function public.focus_obs_day(p_day date default (now() at time zone 'utc')::date)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $fo$
declare
  v_from    timestamptz := p_day::timestamp at time zone 'utc';
  v_to      timestamptz := (p_day + 1)::timestamp at time zone 'utc';
  v_polls   jsonb;
  v_reps    jsonb;
  v_status  text;
  v_detail  jsonb;
  v_mode    text;
begin
  perform public.focus_obs_read();
  select jsonb_build_object(
           'asked', count(*),
           'answered_200', count(*) filter (where status_code = 200),
           'failed', count(*) filter (where read_at is not null and status_code is distinct from 200),
           'unanswered', count(*) filter (where read_at is null),
           'reports_seen', coalesce(sum(n_reports), 0),
           'new_reports', coalesce(sum(n_new), 0),
           'longest_gap_min', round(coalesce(max(gap) / 60.0, 0)::numeric, 1))
    into v_polls
    from (select *, extract(epoch from asked_at - lag(asked_at) over (order by asked_at)) as gap
            from public.focus_obs_polls where asked_at >= v_from and asked_at < v_to) p;
  select coalesce(jsonb_object_agg(icao, x), '{}'::jsonb) into v_reps
    from (select icao, jsonb_build_object(
                   'new_reports', count(*),
                   'corrections', count(*) filter (where raw_ob ~ '\mCOR\M'),
                   'median_min_from_reading', round((percentile_cont(0.5) within group
                       (order by extract(epoch from first_seen_at - obs_time)) / 60.0)::numeric, 1),
                   'median_min_from_source_receipt', round((percentile_cont(0.5) within group
                       (order by extract(epoch from first_seen_at - source_receipt_at)) / 60.0)::numeric, 1)) x
            from public.focus_obs_reports
           where first_seen_at >= v_from and first_seen_at < v_to
           group by icao) s;
  select coalesce(value -> 'P2.11_focus_obs' ->> 'mode', 'auto') into v_mode
    from public.settings where key = 'workflow_schedules';
  v_status := case
    when coalesce(v_mode, 'auto') in ('off', 'manual') then 'skipped'
    when (v_polls ->> 'asked')::int = 0 then 'error'
    when (v_polls ->> 'failed')::int + (v_polls ->> 'unanswered')::int
         > 0.05 * (v_polls ->> 'asked')::int then 'partial'
    else 'ok' end;
  v_detail := jsonb_build_object('day', p_day, 'polls', v_polls, 'stations', v_reps, 'mode', coalesce(v_mode, 'auto'));
  perform public.log_ingest('P2.11_focus_obs', v_status, (v_polls ->> 'new_reports')::int, v_detail);
  return v_detail || jsonb_build_object('status', v_status);
end;
$fo$;

alter table public.focus_obs_stations enable row level security;
alter table public.focus_obs_polls enable row level security;
alter table public.focus_obs_reports enable row level security;
revoke all on public.focus_obs_stations, public.focus_obs_polls, public.focus_obs_reports
  from public, anon, authenticated;
grant select on public.focus_obs_stations, public.focus_obs_polls, public.focus_obs_reports to service_role;
revoke all on function public.focus_obs_read() from public, anon, authenticated;
revoke all on function public.focus_obs_poll(boolean) from public, anon, authenticated;
revoke all on function public.focus_obs_day(date) from public, anon, authenticated;
grant execute on function public.focus_obs_poll(boolean) to service_role;
grant execute on function public.focus_obs_day(date) to service_role;

do $fo$
begin
  if exists (select 1 from pg_extension where extname = 'pg_cron')
     and exists (select 1 from pg_extension where extname = 'pg_net') then
    perform cron.schedule('ad4_focus_obs', '* 7-14 * * *', 'select public.focus_obs_poll()');
    perform cron.schedule('ad4_focus_obs_day', '55 23 * * *', 'select public.focus_obs_day()');
  end if;
end;
$fo$;

notify pgrst, 'reload schema';
