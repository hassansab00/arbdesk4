-- Correlate dispatches explicitly; retry only the exact original startup failure.
CREATE OR REPLACE FUNCTION public.clock_tick(p_at timestamp with time zone DEFAULT now(), p_dry_run boolean DEFAULT false)
 RETURNS jsonb
 LANGUAGE plpgsql
 SECURITY DEFINER
 SET search_path TO 'public', 'pg_temp'
AS $function$
declare
  v_gate  jsonb;
  v_token text;
  v_req   bigint;
  v_dispatch_id uuid;
  v_body jsonb;
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
    v_dispatch_id := null;
    v_body := jsonb_build_object('ref', 'main') || case when r.inputs is null then '{}'::jsonb else jsonb_build_object('inputs', r.inputs) end;
    if r.file = any(array['tick.yml','pipeline_intraday.yml','pipeline_daily.yml','archive_observations.yml','forecasts.yml','observations.yml','paper_trade_log.yml','weather_model.yml']) then
      v_dispatch_id := gen_random_uuid();
      v_body := jsonb_build_object('ref','main','inputs',coalesce(r.inputs,'{}'::jsonb) || jsonb_build_object('clock_dispatch_id',v_dispatch_id));
    end if;
    if not p_dry_run then
      v_req := net.http_post(
        url := format('https://api.github.com/repos/hassansab00/arbdesk4/actions/workflows/%s/dispatches', r.file),
        body := v_body,
        headers := jsonb_build_object(
          'Authorization', 'Bearer ' || v_token,
          'Accept', 'application/vnd.github+json',
          'X-GitHub-Api-Version', '2022-11-28',
          'User-Agent', 'arbdesk4-clock',
          'Content-Type', 'application/json'),
        timeout_milliseconds := 10000);
    end if;
    v_sent := v_sent || jsonb_build_object('file', r.file, 'request_id', v_req, 'clock_dispatch_id', v_dispatch_id);
  end loop;

  if p_dry_run then
    return jsonb_build_object('run', true, 'dispatched', v_sent, 'dry_run', true);
  end if;
  perform public.log_ingest('P6.1_clock', 'ok', jsonb_array_length(v_sent),
    jsonb_build_object('hour_utc', extract(hour from p_at at time zone 'UTC')::int,
                       'dispatched', v_sent, 'clock', 'supabase', 'dry_run', p_dry_run));
  return jsonb_build_object('run', true, 'dispatched', v_sent, 'dry_run', p_dry_run);
end $function$;

revoke all on function public.clock_tick(timestamptz,boolean) from public,anon,authenticated;
grant execute on function public.clock_tick(timestamptz,boolean) to service_role;

create table if not exists arbdesk_private.clock_startup_checks (
  dispatch_log_id bigint not null,
  file text not null,
  dispatch_id uuid not null unique,
  dispatched_at timestamptz not null,
  request_id bigint,
  state text not null default 'checking',
  run_id bigint,
  checked_at timestamptz,
  primary key(dispatch_log_id,file)
);
alter table arbdesk_private.clock_startup_checks enable row level security;
revoke all on arbdesk_private.clock_startup_checks from public,anon,authenticated;
grant select on arbdesk_private.clock_startup_checks to service_role;
create unique index if not exists clock_startup_one_retry_per_run
  on arbdesk_private.clock_startup_checks(run_id) where run_id is not null;

create or replace function arbdesk_private.recover_clock_startups()
returns jsonb language plpgsql security definer set search_path = '' as $$
declare
  v_token text;
  v_headers jsonb;
  v_response record;
  v_item jsonb;
  v_run jsonb;
  v_matches integer;
  v_request bigint;
  v_submitted integer := 0;
  v_enqueued integer := 0;
  r record;
begin
  if (select value #>> '{P6.1_clock,mode}' from public.settings
      where key='workflow_schedules') is distinct from 'auto' then
    return jsonb_build_object('state','clock_paused','submitted',0);
  end if;
  if not pg_try_advisory_xact_lock(hashtextextended('ad4_clock_startup_recovery',0)) then
    return jsonb_build_object('state','already_running','submitted',0);
  end if;
  select decrypted_secret into v_token from vault.decrypted_secrets
    where name='github_actions_token';
  if v_token is null then
    return jsonb_build_object('state','missing_token','submitted',0);
  end if;
  v_headers := jsonb_build_object('Authorization','Bearer '||v_token,
    'Accept','application/vnd.github+json','X-GitHub-Api-Version','2022-11-28',
    'User-Agent','arbdesk4-clock-startup','Content-Type','application/json');

  for r in select * from arbdesk_private.clock_startup_checks
    where state in ('checking','retry_submitted')
    order by dispatched_at limit 20 for update skip locked
  loop
    begin
      if r.dispatched_at < now()-interval '30 minutes' then
        update arbdesk_private.clock_startup_checks set state='held_expired',checked_at=now()
          where dispatch_id=r.dispatch_id;
        continue;
      end if;
      select * into v_response from net._http_response where id=r.request_id;
      if not found then continue; end if;
      if r.state='retry_submitted' then
        update arbdesk_private.clock_startup_checks
          set state=case when v_response.status_code=201 and not coalesce(v_response.timed_out,false)
            then 'retry_accepted_not_completion' else 'held_retry_refused' end,checked_at=now()
          where dispatch_id=r.dispatch_id;
        continue;
      end if;
      if v_response.status_code is distinct from 200 or coalesce(v_response.timed_out,false) then
        update arbdesk_private.clock_startup_checks set state='held_read_refused',checked_at=now()
          where dispatch_id=r.dispatch_id;
        continue;
      end if;
      v_item := v_response.content::jsonb;
      if jsonb_typeof(v_item->'workflow_runs') is distinct from 'array'
        or jsonb_array_length(v_item->'workflow_runs') > 5 then
        raise exception 'invalid workflow response';
      end if;
      select count(*), (jsonb_agg(x))->0 into v_matches,v_run
        from jsonb_array_elements(v_item->'workflow_runs') x
        where x->>'head_branch'='main' and x->>'event'='workflow_dispatch'
          and x->>'display_title'='AD4 clock '||r.dispatch_id::text
          and (x->>'created_at')::timestamptz between
            r.dispatched_at-interval '5 seconds' and r.dispatched_at+interval '15 minutes';
      if v_matches<>1 then
        update arbdesk_private.clock_startup_checks set state='held_ambiguous_run',checked_at=now()
          where dispatch_id=r.dispatch_id;
        continue;
      end if;
      if v_run->>'status'='completed' and v_run->>'conclusion'='startup_failure'
        and v_run->>'run_attempt'='1' and (v_run->>'id') ~ '^[0-9]+$'
        and exists(select 1 from public.clock_due(r.dispatched_at) d where d.file=r.file) then
        -- A subtransaction also rolls back pg_net's queued request on conflict.
        v_request := net.http_post(
          url:='https://api.github.com/repos/hassansab00/arbdesk4/actions/runs/'||(v_run->>'id')||'/rerun',
          body:='{}'::jsonb,headers:=v_headers,timeout_milliseconds:=10000);
        update arbdesk_private.clock_startup_checks set state='retry_submitted',
          run_id=(v_run->>'id')::bigint,request_id=v_request,checked_at=now()
          where dispatch_id=r.dispatch_id;
        v_submitted := v_submitted+1;
      else
        update arbdesk_private.clock_startup_checks set state='checked_no_retry',checked_at=now()
          where dispatch_id=r.dispatch_id;
      end if;
    exception when others then
      update arbdesk_private.clock_startup_checks set state='held_invalid_response',checked_at=now()
        where dispatch_id=r.dispatch_id;
    end;
  end loop;

  -- Only new explicitly correlated dispatches. Preserve older logs unchanged.
  if pg_total_relation_size('arbdesk_private.clock_startup_checks') < 16777216 then
    for r in select l.log_id,l.logged_at,d->>'file' as file,
        (d->>'clock_dispatch_id')::uuid as dispatch_id
      from public.ingest_log l
      cross join lateral jsonb_array_elements(l.detail->'dispatched') d
      where l.job='P6.1_clock' and l.status='ok' and l.detail->>'clock'='supabase'
        and l.detail->>'dry_run'='false'
        and l.logged_at between now()-interval '15 minutes' and now()-interval '2 minutes'
        and d->>'file'=any(array['tick.yml','pipeline_intraday.yml','pipeline_daily.yml',
          'archive_observations.yml','forecasts.yml','observations.yml','paper_trade_log.yml','weather_model.yml'])
        and (d->>'clock_dispatch_id') ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$'
        and not exists(select 1 from arbdesk_private.clock_startup_checks c
          where c.dispatch_id=(d->>'clock_dispatch_id')::uuid)
        and exists(select 1 from public.clock_due(l.logged_at) s where s.file=d->>'file')
      order by l.logged_at,l.log_id limit 8
    loop
      v_request := net.http_get(
        url:=format('https://api.github.com/repos/hassansab00/arbdesk4/actions/workflows/%s/runs?event=workflow_dispatch&branch=main&per_page=5',r.file),
        headers:=v_headers,timeout_milliseconds:=10000);
      insert into arbdesk_private.clock_startup_checks
        (dispatch_log_id,file,dispatch_id,dispatched_at,request_id)
        values(r.log_id,r.file,r.dispatch_id,r.logged_at,v_request);
      v_enqueued := v_enqueued+1;
    end loop;
  end if;
  return jsonb_build_object('state','checked','submitted',v_submitted,'checks_enqueued',v_enqueued);
end $$;
revoke all on function arbdesk_private.recover_clock_startups() from public,anon,authenticated,service_role;
comment on table arbdesk_private.clock_startup_checks is
  'Private bounded clock receipts. No credentials or API response bodies. Retry acceptance is not completion.';
do $$ begin
  if exists(select 1 from pg_extension where extname='pg_cron')
    and exists(select 1 from pg_extension where extname='pg_net')
    and exists(select 1 from pg_extension where extname='supabase_vault') then
    perform cron.schedule('ad4_clock_startup_recovery','41,46,51 * * * *',
      'set statement_timeout=''10s''; select arbdesk_private.recover_clock_startups();');
  end if;
end $$;
