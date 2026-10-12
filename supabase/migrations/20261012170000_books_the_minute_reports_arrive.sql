-- ===========================================================================
-- BOOKS THE MINUTE REPORTS ARRIVE (WXPredict WP5, the same-day YES pilot's
-- market record; the Codex action plan of 11 Oct, sections 4 and 5).
--
-- The focus poller (20261011180000_reports_the_minute_they_arrive.sql, WP4)
-- reads Jeddah's and Istanbul's reports a minute after the reading, but the
-- books were read only by the hourly tick at :36 (live_capture 'books'), up to
-- an hour from any report. The pilot's question is whether a new report moves
-- a bucket's chance of being the day's maximum more than its executable YES
-- ask, so the ask, its depth and their path around each report must be on
-- record at the minute. This asks for the focus cities' same-day books in the
-- same minute as the reports, every minute of the window, and keeps each
-- book once per change in the live record:
--
--   kind 'focus_book'  key the bucket's band_id; payload the book reduced:
--                      best bid and ask (found by price: the venue lists asks
--                      worst first, 12 Oct), up to FOCUS_BOOK_LEVELS levels
--                      each side from the touch ([price, size], sizes summed
--                      per price), the last trade, the book's own time
--                      (seconds; the venue's last update, not our request:
--                      15:52:45Z in a 15:56:03Z answer, 12 Oct), the minimum
--                      order and the tick. captured_at is when pg_net had the
--                      answer.
--
-- A book unchanged since the last minute is not stored again
-- (live_capture_put), so a minute's absence means "the same as before", and
-- the poll row says the minute was asked. The live record's lifecycle is the
-- book's: two days in Postgres, the nightly mirror and the archive.
--
-- Measured before (12 Oct 15:56Z, through pg_net): one request answers all
-- 22 same-day books of the two cities (11 each) in 0.11 s with 45,723 bytes and
-- 1,219 levels (81 at most in one book); 20 of the 22 had no bid. Kept raw
-- every minute that would be about 19 MB a day; reduced and stored on change
-- it is not measured yet (the first day's rows say).
--
-- Polls are recorded in focus_obs_polls with source 'clob_books' beside the
-- reports' 'awc', and focus_obs_day reports the two apart. The Workflows-page
-- switch P2.11_focus_obs stops both. No new schedule: the same pg_cron minute.
--
-- Re-runnable. Service role only. Nothing is deleted.
-- ===========================================================================

-- THE LIVE RECORD TAKES THE NEW KIND (definitions as in
-- sql/ad4_prune_live_capture.sql).
alter table public.live_capture drop constraint if exists live_capture_kind_check;
alter table public.live_capture add constraint live_capture_kind_check
  check (kind in ('report', 'hourly', 'hourly2', 'models1', 'models2', 'books', 'focus_book'));

comment on table public.live_capture is
  'WXPredict WP3 (11 Oct): every live input the hourly tick had, as it arrived (captured_at): station reports with every training field, the hourly _previous_day1 forecast, every open bucket''s book; from 12 Oct also best_match at _previous_day2 and the seven models'' temperature at both leads (WP3b), and the focus cities'' same-day books every minute of their window (focus_book, WP5). A report, forecast value or focus book is stored once per change. Two days here; older rows in data/archive/live_capture.';

create or replace function public.live_capture_put(p_kind text, p_captured_at timestamptz, p_rows jsonb)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $lc$
declare
  v_rows int;
  v_new  int;
begin
  if p_kind is null or p_kind not in ('report', 'hourly', 'hourly2', 'models1', 'models2', 'books', 'focus_book') then
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


-- ONE BOOK, REDUCED. Levels with a size above 0, sizes summed per price, the
-- touch found by price, FOCUS_BOOK_LEVELS (10) each side from it.
create or replace function public.focus_book_top(p_book jsonb)
returns jsonb language sql immutable set search_path = public, pg_temp as $fb$
  with a as (
    select (l ->> 'price')::numeric p, sum((l ->> 'size')::numeric) s
      from jsonb_array_elements(coalesce(p_book -> 'asks', '[]'::jsonb)) l
     where (l ->> 'size')::numeric > 0
     group by 1
  ), b as (
    select (l ->> 'price')::numeric p, sum((l ->> 'size')::numeric) s
      from jsonb_array_elements(coalesce(p_book -> 'bids', '[]'::jsonb)) l
     where (l ->> 'size')::numeric > 0
     group by 1
  )
  select jsonb_build_object(
    'bid',  (select max(p) from b),
    'ask',  (select min(p) from a),
    'bids', coalesce((select jsonb_agg(jsonb_build_array(p, s) order by p desc)
                        from (select p, s from b order by p desc limit 10) x), '[]'::jsonb),
    'asks', coalesce((select jsonb_agg(jsonb_build_array(p, s) order by p)
                        from (select p, s from a order by p limit 10) x), '[]'::jsonb),
    'last', nullif(p_book ->> 'last_trade_price', '')::numeric,
    'ts',   floor(nullif(p_book ->> 'timestamp', '')::numeric / 1000)::bigint,
    'min',  nullif(p_book ->> 'min_order_size', '')::numeric,
    'tick', nullif(p_book ->> 'tick_size', '')::numeric)
$fb$;

-- ASK FOR THE BOOKS of the given stations' cities' open markets of their own
-- local today, in one request; the request is recorded as a poll of source
-- 'clob_books'. Null when there is nothing to ask or no pg_net.
create or replace function public.focus_books_ask(p_ids text[])
returns bigint language plpgsql security definer set search_path = public, pg_temp as $fb$
declare
  v_tokens jsonb;
  v_req    bigint;
begin
  if not exists (select 1 from pg_proc p join pg_namespace n on n.oid = p.pronamespace
                  where n.nspname = 'net' and p.proname = 'http_post') then
    return null;
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
  insert into public.focus_obs_polls (request_id, source, stations) values (v_req, 'clob_books', p_ids);
  return v_req;
end;
$fb$;

-- READ EVERY ANSWER BACK: reports as before (source 'awc'), books into the
-- live record (source 'clob_books'). One answer that will not parse is
-- recorded on its poll and does not stop the others.
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
    execute 'select p.request_id, p.source, h.status_code, h.content, h.timed_out, h.error_msg, h.created
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

-- ASK EACH MINUTE: the reports, then the same stations' books.
create or replace function public.focus_obs_poll(p_force boolean default false)
returns jsonb language plpgsql security definer set search_path = public, pg_temp as $fo$
declare
  v_new   int := public.focus_obs_read();
  v_mode  text;
  v_ids   text[];
  v_req   bigint;
  v_books bigint;
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
  end;
  return jsonb_build_object('asked', true, 'read_new', v_new, 'request_id', v_req, 'stations', v_ids,
                            'books_request_id', v_books);
end;
$fo$;

-- One row a UTC day in ingest_log: the reports' polls as before, the books'
-- polls beside them.
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
           'unanswered', count(*) filter (where read_at is null),
           'books_seen', coalesce(sum(n_reports), 0),
           'books_stored', coalesce(sum(n_new), 0))
    into v_books
    from public.focus_obs_polls
   where asked_at >= v_from and asked_at < v_to and source = 'clob_books';
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
    when (v_books ->> 'asked')::int = 0
         or (v_books ->> 'failed')::int + (v_books ->> 'unanswered')::int
            > 0.05 * (v_books ->> 'asked')::int then 'partial'
    else 'ok' end;
  v_detail := jsonb_build_object('day', p_day, 'polls', v_polls, 'books', v_books, 'stations', v_reps,
                                 'mode', coalesce(v_mode, 'auto'));
  perform public.log_ingest('P2.11_focus_obs', v_status, (v_polls ->> 'new_reports')::int, v_detail);
  return v_detail || jsonb_build_object('status', v_status);
end;
$fo$;

revoke all on function public.focus_book_top(jsonb) from public, anon, authenticated;
revoke all on function public.focus_books_ask(text[]) from public, anon, authenticated;
revoke all on function public.focus_obs_read() from public, anon, authenticated;
revoke all on function public.focus_obs_poll(boolean) from public, anon, authenticated;
revoke all on function public.focus_obs_day(date) from public, anon, authenticated;
grant execute on function public.focus_obs_poll(boolean) to service_role;
grant execute on function public.focus_obs_day(date) to service_role;

notify pgrst, 'reload schema';
