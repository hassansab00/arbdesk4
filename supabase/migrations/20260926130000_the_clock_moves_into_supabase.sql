-- ===========================================================================
-- THE CLOCK MOVES INTO SUPABASE (plan v2 P6.2)
--
-- n8n's P6.1_clock started every scheduled GitHub workflow at :36 each hour:
-- 24 executions a day of Hassan's n8n allowance (measured 25-26 Sep: 24 of
-- the 61 AD4 executions a day), for something the database can do itself.
-- Hassan stored a fine-grained GitHub token (Actions: read and write, this
-- repository only) in Vault as github_actions_token on 26 Sep; a read of the
-- tick workflow through pg_net with it answered 200.
--
--   public.clock_schedule   the schedule, one row per workflow: the hours
--                           (UTC) it starts at, the weekdays if limited, the
--                           inputs it is dispatched with. The same table the
--                           n8n clock carried as its CLOCK literal; the JSON
--                           below is what tests/test_github_actions.py counts
--                           against the Actions minute budget (Rule 7).
--   public.clock_tick()     at :36 (pg_cron): asks should_run('P6.1_clock'),
--                           the Workflows page's switch, then dispatches every
--                           workflow due this hour through pg_net and logs
--                           the request ids as P6.1_clock.
--   public.clock_check()    at :39: reads GitHub's answers to those requests.
--                           A dispatch GitHub accepts answers 204; anything
--                           else is logged as P6.1_clock_check 'error' with
--                           GitHub's message. pg_net is asynchronous, so the
--                           tick cannot know the answer when it logs.
--
-- The token never leaves Vault: it is read inside the function, which runs as
-- its owner and which no browser role may execute.
--
-- Re-runnable. On a database without pg_net, pg_cron or Vault (the migration
-- test harness) the functions are created and nothing is scheduled.
-- ===========================================================================

create table if not exists public.clock_schedule (
  file          text primary key,
  hours_utc     int[],          -- null = every hour
  weekdays_utc  int[],          -- 0 = Sunday; null = every day
  inputs        jsonb,          -- workflow_dispatch inputs, as strings
  updated_at    timestamptz not null default now()
);

comment on table public.clock_schedule is
  'Plan v2 P6.2: which GitHub workflow public.clock_tick() starts at :36 of which UTC hour. The one clock: no workflow keeps a GitHub cron. tests/test_github_actions.py counts it against the Actions minute budget.';

alter table public.clock_schedule enable row level security;
revoke all on public.clock_schedule from public, anon, authenticated;
grant select on public.clock_schedule to service_role;

-- THE CLOCK. The same entries, in the same shape, as the n8n clock's CLOCK
-- table it replaces; tests/test_github_actions.py parses this literal.
insert into public.clock_schedule (file, hours_utc, weekdays_utc, inputs)
select c.file,
       case when c.hours_utc = '"*"'::jsonb then null
            else array(select jsonb_array_elements_text(c.hours_utc)::int) end,
       case when c.weekdays_utc is null then null
            else array(select jsonb_array_elements_text(c.weekdays_utc)::int) end,
       c.inputs
  from jsonb_to_recordset(
'[{"file": "tick.yml", "hours_utc": "*"}, {"file": "pipeline_intraday.yml", "hours_utc": [0, 4, 8, 12, 16, 20]}, {"file": "archive_observations.yml", "hours_utc": [2], "inputs": {"commit": "true", "table": "all"}}, {"file": "forecasts.yml", "hours_utc": [3]}, {"file": "pipeline_daily.yml", "hours_utc": [4]}, {"file": "observations.yml", "hours_utc": [5]}, {"file": "paper_trade_log.yml", "hours_utc": [5]}, {"file": "weather_model.yml", "hours_utc": [8], "weekdays_utc": [1]}]'::jsonb
  ) as c(file text, hours_utc jsonb, weekdays_utc jsonb, inputs jsonb)
on conflict (file) do update set
  hours_utc = excluded.hours_utc, weekdays_utc = excluded.weekdays_utc,
  inputs = excluded.inputs, updated_at = now();

create or replace function public.clock_due(p_at timestamptz)
returns table (file text, inputs jsonb)
language sql
stable
set search_path = public, pg_temp
as $$
  select s.file, s.inputs
    from public.clock_schedule s
   where (s.hours_utc is null or extract(hour from p_at at time zone 'UTC')::int = any (s.hours_utc))
     and (s.weekdays_utc is null or extract(dow from p_at at time zone 'UTC')::int = any (s.weekdays_utc))
   order by s.file
$$;

create or replace function public.clock_tick(p_at timestamptz default now(), p_dry_run boolean default false)
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_gate  jsonb;
  v_token text;
  v_req   bigint;
  v_sent  jsonb := '[]'::jsonb;
  r       record;
begin
  v_gate := public.should_run('P6.1_clock', 'schedule');
  if not coalesce((v_gate ->> 'run')::boolean, true) then
    if not p_dry_run then
      perform public.log_ingest('P6.1_clock', 'skipped', 0,
        jsonb_build_object('reason', v_gate ->> 'reason', 'clock', 'supabase'));
    end if;
    return v_gate;
  end if;

  if not p_dry_run then
    select s.decrypted_secret into v_token from vault.decrypted_secrets s where s.name = 'github_actions_token';
    if v_token is null then
      perform public.log_ingest('P6.1_clock', 'error', 0,
        jsonb_build_object('reason', 'vault secret github_actions_token is missing', 'clock', 'supabase'));
      return jsonb_build_object('run', false, 'reason', 'no token');
    end if;
  end if;

  for r in select * from public.clock_due(p_at) loop
    v_req := null;
    if not p_dry_run then
      v_req := net.http_post(
        url := format('https://api.github.com/repos/hassansab00/arbdesk4/actions/workflows/%s/dispatches', r.file),
        body := jsonb_build_object('ref', 'main')
                || case when r.inputs is null then '{}'::jsonb else jsonb_build_object('inputs', r.inputs) end,
        headers := jsonb_build_object(
          'Authorization', 'Bearer ' || v_token,
          'Accept', 'application/vnd.github+json',
          'X-GitHub-Api-Version', '2022-11-28',
          'User-Agent', 'arbdesk4-clock',
          'Content-Type', 'application/json'),
        timeout_milliseconds := 10000);
    end if;
    v_sent := v_sent || jsonb_build_object('file', r.file, 'request_id', v_req);
  end loop;

  -- A dry run logs nothing: should_run gates on the last P6.1_clock run, so a
  -- rehearsal logged at :15 would make the real :36 run skip.
  if p_dry_run then
    return jsonb_build_object('run', true, 'dispatched', v_sent, 'dry_run', true);
  end if;
  perform public.log_ingest('P6.1_clock', 'ok', jsonb_array_length(v_sent),
    jsonb_build_object('hour_utc', extract(hour from p_at at time zone 'UTC')::int,
                       'dispatched', v_sent, 'clock', 'supabase', 'dry_run', p_dry_run));
  return jsonb_build_object('run', true, 'dispatched', v_sent, 'dry_run', p_dry_run);
end $$;

comment on function public.clock_tick(timestamptz, boolean) is
  'Plan v2 P6.2: dispatches every GitHub workflow due this UTC hour (public.clock_schedule) through pg_net with the Vault token, if the Workflows page lets P6.1_clock run. pg_cron at :36. Logs P6.1_clock with the request ids; clock_check() reads GitHub''s answers.';

create or replace function public.clock_check()
returns jsonb
language plpgsql
security definer
set search_path = public, pg_temp
as $$
declare
  v_last    jsonb;
  v_refused jsonb := '[]'::jsonb;
  v_waiting int := 0;
  r         record;
begin
  select detail into v_last from public.ingest_log
   where job = 'P6.1_clock' and status = 'ok' and detail ->> 'clock' = 'supabase'
     and coalesce((detail ->> 'dry_run')::boolean, false) = false
     and logged_at > now() - interval '30 minutes'
   order by logged_at desc limit 1;
  if v_last is null then
    return jsonb_build_object('checked', 0);
  end if;
  for r in
    select d ->> 'file' as file, (d ->> 'request_id')::bigint as id
      from jsonb_array_elements(v_last -> 'dispatched') d
  loop
    if not exists (select 1 from net._http_response h where h.id = r.id) then
      v_waiting := v_waiting + 1;
    elsif exists (select 1 from net._http_response h
                   where h.id = r.id and (h.status_code is distinct from 204 or h.timed_out)) then
      v_refused := v_refused || (select jsonb_build_object('file', r.file, 'status', h.status_code,
                                        'timed_out', h.timed_out, 'error', h.error_msg,
                                        'message', left(h.content, 200))
                                   from net._http_response h where h.id = r.id);
    end if;
  end loop;
  perform public.log_ingest('P6.1_clock_check',
    case when jsonb_array_length(v_refused) > 0 or v_waiting > 0 then 'error' else 'ok' end,
    jsonb_array_length(v_last -> 'dispatched') - jsonb_array_length(v_refused) - v_waiting,
    jsonb_build_object('refused', v_refused, 'no_answer', v_waiting,
                       'hour_utc', v_last -> 'hour_utc'));
  return jsonb_build_object('refused', v_refused, 'no_answer', v_waiting);
end $$;

comment on function public.clock_check() is
  'Plan v2 P6.2: reads GitHub''s answers to the last clock_tick() dispatches (pg_net is asynchronous). 204 is accepted; anything else, or no answer, logs P6.1_clock_check as error. pg_cron at :39.';

revoke all on function public.clock_due(timestamptz) from public, anon, authenticated;
revoke all on function public.clock_tick(timestamptz, boolean) from public, anon, authenticated;
revoke all on function public.clock_check() from public, anon, authenticated;
grant execute on function public.clock_due(timestamptz) to service_role;
grant execute on function public.clock_tick(timestamptz, boolean) to service_role;
grant execute on function public.clock_check() to service_role;

do $$
begin
  if exists (select 1 from pg_namespace where nspname = 'cron')
     and exists (select 1 from pg_namespace where nspname = 'net')
     and exists (select 1 from pg_namespace where nspname = 'vault') then
    perform cron.schedule('ad4_clock', '36 * * * *', 'select public.clock_tick()');
    perform cron.schedule('ad4_clock_check', '39 * * * *', 'select public.clock_check()');
  end if;
end $$;
