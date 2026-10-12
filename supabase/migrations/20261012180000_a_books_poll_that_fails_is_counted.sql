-- ===========================================================================
-- A BOOKS POLL THAT FAILS IS COUNTED (Codex on #389; WXPredict WP5).
--
-- 20261012170000_books_the_minute_reports_arrive.sql asks for the focus
-- cities' same-day books each minute the reports are asked. Two ways a minute
-- could go missing and the day still read 'ok':
--
--   1. Asking for the books raised (a schema change, pg_net without
--      http_post): the poll caught it and returned null, and no row said the
--      minute was not asked. Now the minute's reports poll keeps the reason in
--      books_error, and focus_obs_day counts those minutes as not_asked
--      beside the books polls. Nothing to ask (no open market of the day)
--      stays null in both, as before.
--   2. The venue answered 200 with fewer books than were asked, or none: the
--      poll read as answered. Now each books poll records how many books it
--      asked for (n_asked), and an answer with fewer is a failed poll ('answer
--      short: k of n books'); the books it did carry are still kept.
--
-- focus_obs_day: books 'partial' when the failed, unanswered and not-asked
-- minutes are more than 5% of the minutes that should have asked, or none
-- asked at all.
--
-- Re-runnable. Service role only. Nothing is deleted. No new schedule.
-- ===========================================================================

alter table public.focus_obs_polls add column if not exists n_asked int;
alter table public.focus_obs_polls add column if not exists books_error text;

comment on column public.focus_obs_polls.n_asked is
  'clob_books polls: how many books the request asked for. An answer with fewer is a failed poll (Codex on #389).';
comment on column public.focus_obs_polls.books_error is
  'awc polls: why this minute''s books were not asked, when asking them raised (Codex on #389). Null when they were asked or there was nothing to ask.';

-- ASK FOR THE BOOKS, as before, recording how many; no pg_net POST is an
-- error now, so the minute is counted as not asked.
create or replace function public.focus_books_ask(p_ids text[])
returns bigint language plpgsql security definer set search_path = public, pg_temp as $fb$
declare
  v_tokens jsonb;
  v_req    bigint;
begin
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'net' and p.proname = 'http_post') then
    raise exception 'focus_books_ask: pg_net has no http_post';
  end if;
  select jsonb_agg(jsonb_build_object('token_id', b.token_yes) order by b.token_yes) into v_tokens
    from public.focus_obs_stations s
    join public.markets m on m.city_key = s.city_key
                         and m.resolution_date = (now() at time zone s.timezone)::date
    join public.bands b on b.market_id = m.market_id
   where s.icao = any (p_ids) and coalesce(m.closed, false) = false and b.token_yes is not null;
  if v_tokens is null then
    return null;
  end if;
  execute 'select net.http_post(url := $1, body := $2, headers := $3, timeout_milliseconds := 10000)'
     into v_req
    using 'https://clob.polymarket.com/books', v_tokens, '{"Content-Type": "application/json"}'::jsonb;
  insert into public.focus_obs_polls (request_id, source, stations, n_asked)
  values (v_req, 'clob_books', p_ids, jsonb_array_length(v_tokens));
  return v_req;
end;
$fb$;

-- READ EVERY ANSWER BACK, as before; a books answer carrying fewer books than
-- were asked is a failed poll.
create or replace function public.focus_obs_read()
returns int language plpgsql security definer set search_path = public, pg_temp as $fo$
declare
  r      record;
  v_rep  jsonb;
  v_rows jsonb;
  v_band text;
  v_n    int;
  v_new  int;
  v_all  int := 0;
  v_err  text;
begin
  if to_regclass('net._http_response') is null then
    return 0;
  end if;
  for r in
    execute 'select p.request_id, p.source, p.n_asked, h.status_code, h.content, h.timed_out, h.error_msg, h.created
               from public.focus_obs_polls p join net._http_response h on h.id = p.request_id
              where p.read_at is null order by p.request_id'
  loop
    v_n := 0; v_new := 0; v_err := r.error_msg;
    if r.status_code = 200 and r.content is not null and r.source = 'clob_books' then
      begin
        v_rows := '[]'::jsonb;
        for v_rep in select value from jsonb_array_elements(r.content::jsonb) loop
          select b.band_id::text into v_band from public.bands b where b.token_yes = v_rep ->> 'asset_id' limit 1;
          continue when v_band is null;
          v_n := v_n + 1;
          v_rows := v_rows || jsonb_build_array(jsonb_build_object('key', v_band, 'payload', public.focus_book_top(v_rep)));
        end loop;
        if v_n > 0 then
          v_new := (public.live_capture_put('focus_book', r.created, v_rows) ->> 'new')::int;
        end if;
      exception when others then
        v_n := 0; v_new := 0;                    -- nothing of a half-read answer is kept
        v_err := coalesce(v_err || '; ', '') || 'unreadable answer: ' || sqlerrm;
      end;
    elsif r.status_code = 200 and r.content is not null then
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
        v_n := 0; v_new := 0;                    -- the block's inserts are rolled back with it
        v_err := coalesce(v_err || '; ', '') || 'unreadable answer: ' || sqlerrm;
      end;
    end if;
    -- a 200 short of the books asked (none at all included) is a failed poll;
    -- the books it carried are kept (Codex on #389)
    if r.source = 'clob_books' and r.status_code = 200 and v_err is null
       and r.n_asked is not null and v_n < r.n_asked then
      v_err := format('answer short: %s of %s books', v_n, r.n_asked);
    end if;
    update public.focus_obs_polls
       set answered_at = r.created, status_code = r.status_code, timed_out = r.timed_out,
           error = v_err, n_reports = v_n, n_new = v_new, read_at = now()
     where request_id = r.request_id;
    if r.source <> 'clob_books' then
      v_all := v_all + v_new;
    end if;
  end loop;
  return v_all;
end;
$fo$;

-- ASK EACH MINUTE, as before; a failure to ask the books stops nothing and is
-- kept on the minute's reports poll.
create or replace function public.focus_obs_poll(p_force boolean default false)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $fo$
declare
  v_new   int := public.focus_obs_read();
  v_mode  text;
  v_ids   text[];
  v_req   bigint;
  v_books bigint;
  v_berr  text;
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
  -- the books ask after the reports, and a failure to ask them stops nothing
  begin
    v_books := public.focus_books_ask(v_ids);
  exception when others then
    v_books := null;
    v_berr := left(sqlerrm, 500);
    update public.focus_obs_polls set books_error = v_berr where request_id = v_req;
  end;
  return jsonb_build_object('asked', true, 'read_new', v_new, 'request_id', v_req, 'stations', v_ids,
                            'books_request_id', v_books, 'books_error', v_berr);
end;
$fo$;

-- One row a UTC day in ingest_log, as before; the books block also counts the
-- minutes whose books were not asked and the short answers.
create or replace function public.focus_obs_day(p_day date default (now() at time zone 'utc')::date)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $fo$
declare
  v_from    timestamptz := p_day::timestamp at time zone 'utc';
  v_to      timestamptz := (p_day + 1)::timestamp at time zone 'utc';
  v_polls   jsonb;
  v_books   jsonb;
  v_reps    jsonb;
  v_status  text;
  v_detail  jsonb;
  v_mode    text;
  v_should  int;
begin
  perform public.focus_obs_read();
  select jsonb_build_object(
           'asked', count(*),
           'answered_200', count(*) filter (where status_code = 200),
           -- an answer that could not be read is a failed poll whatever its
           -- status: a 200 carrying no readable reports stores nothing
           -- (Codex on #380)
           'failed', count(*) filter (where read_at is not null
                                        and (status_code is distinct from 200 or error is not null)),
           'unanswered', count(*) filter (where read_at is null),
           'reports_seen', coalesce(sum(n_reports), 0),
           'new_reports', coalesce(sum(n_new), 0),
           'longest_gap_min', round(coalesce(max(gap) / 60.0, 0)::numeric, 1))
    into v_polls
    from (select *, extract(epoch from asked_at - lag(asked_at) over (order by asked_at)) as gap
            from public.focus_obs_polls
           where asked_at >= v_from and asked_at < v_to and source <> 'clob_books') p;
  select jsonb_build_object(
           'asked', count(*),
           'answered_200', count(*) filter (where status_code = 200),
           'failed', count(*) filter (where read_at is not null
                                        and (status_code is distinct from 200 or error is not null)),
           'short', count(*) filter (where error like 'answer short:%'),
           'unanswered', count(*) filter (where read_at is null),
           'not_asked', (select count(*) from public.focus_obs_polls a
                          where a.asked_at >= v_from and a.asked_at < v_to
                            and a.source <> 'clob_books' and a.books_error is not null),
           'books_seen', coalesce(sum(n_reports), 0),
           'books_stored', coalesce(sum(n_new), 0))
    into v_books
    from public.focus_obs_polls
   where asked_at >= v_from and asked_at < v_to and source = 'clob_books';
  v_should := (v_books ->> 'asked')::int + (v_books ->> 'not_asked')::int;
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
    when v_should = 0
         or (v_books ->> 'failed')::int + (v_books ->> 'unanswered')::int + (v_books ->> 'not_asked')::int
            > 0.05 * v_should then 'partial'
    else 'ok' end;
  v_detail := jsonb_build_object('day', p_day, 'polls', v_polls, 'books', v_books, 'stations', v_reps,
                                 'mode', coalesce(v_mode, 'auto'));
  perform public.log_ingest('P2.11_focus_obs', v_status, (v_polls ->> 'new_reports')::int, v_detail);
  return v_detail || jsonb_build_object('status', v_status);
end;
$fo$;

revoke all on function public.focus_books_ask(text[]) from public, anon, authenticated;
revoke all on function public.focus_obs_read() from public, anon, authenticated;
revoke all on function public.focus_obs_poll(boolean) from public, anon, authenticated;
revoke all on function public.focus_obs_day(date) from public, anon, authenticated;
grant execute on function public.focus_obs_poll(boolean) to service_role;
grant execute on function public.focus_obs_day(date) to service_role;

notify pgrst, 'reload schema';
