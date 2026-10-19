-- Long refreshes run in Postgres, not inside an HTTP gateway request.
-- The existing half-hour refresh and its serialization lock are unchanged.
-- A requested job removes its own schedule; immutable ingest receipts remain.
create schema if not exists arbdesk_private;

create or replace function public.page_cache_refresh_status(p_request_id uuid)
returns jsonb language plpgsql security definer
set search_path = public, pg_temp as $$
declare answer jsonb;
begin
  select jsonb_build_object('request_id',p_request_id,'status',l.status,
    'detail',l.detail,'logged_at',l.logged_at) into answer
  from public.ingest_log l where l.job='page_cache_request:'||p_request_id::text
  order by l.log_id desc limit 1;
  return coalesce(answer,jsonb_build_object('request_id',p_request_id,'status','missing'));
end $$;

create or replace function arbdesk_private.run_page_cache_request(p_request_id uuid)
returns void language plpgsql security definer
set search_path = public, pg_temp as $$
declare
  v_job_name text := 'ad4_page_cache_'||p_request_id::text;
  v_receipt_job text := 'page_cache_request:'||p_request_id::text;
  receipt jsonb;
  result jsonb;
  attempts integer;
  requested timestamptz;
begin
  receipt := public.page_cache_refresh_status(p_request_id);
  if receipt->>'status' in ('ok','error','missing') then
    perform cron.unschedule(jobid) from cron.job where jobname=v_job_name;
    return;
  end if;
  select min(logged_at),count(*) filter(where status='running')
    into requested,attempts from public.ingest_log where ingest_log.job=v_receipt_job;
  if requested < clock_timestamp()-interval '10 minutes' or attempts>=3 then
    perform public.log_ingest(v_receipt_job,'error',0,jsonb_build_object('error','refresh request expired or contention limit reached'));
    perform cron.unschedule(jobid) from cron.job where jobname=v_job_name;
    return;
  end if;
  perform public.log_ingest(v_receipt_job,'running',0,jsonb_build_object('attempt',attempts+1));
  begin
    result := public.refresh_page_cache();
    if result is null or jsonb_typeof(result)<>'object' then
      raise exception 'Page cache refresh returned no result';
    end if;
    if result ? 'skipped' then
      perform public.log_ingest(v_receipt_job,'waiting',0,result);
      return; -- Retry this same request on the next minute, at most three times.
    end if;
    perform public.log_ingest(v_receipt_job,'ok',0,jsonb_build_object('ms',result,'completed_at',clock_timestamp()));
  exception when query_canceled then
    perform public.log_ingest(v_receipt_job,'error',0,jsonb_build_object('error',sqlerrm,'sqlstate',sqlstate));
  when others then
    perform public.log_ingest(v_receipt_job,'error',0,jsonb_build_object('error',sqlerrm,'sqlstate',sqlstate));
  end;
  perform cron.unschedule(jobid) from cron.job where jobname=v_job_name;
end $$;

create or replace function public.request_page_cache_refresh(p_request_id uuid)
returns jsonb language plpgsql security definer
set search_path = public, pg_temp as $$
declare receipt jsonb;
begin
  if p_request_id is null then raise exception 'A refresh request ID is required'; end if;
  perform pg_advisory_xact_lock(hashtextextended('page-cache-request:'||p_request_id::text,0));
  receipt := public.page_cache_refresh_status(p_request_id);
  if receipt->>'status'<>'missing' then return receipt; end if;
  perform public.log_ingest('page_cache_request:'||p_request_id::text,'queued',0,'{}'::jsonb);
  perform cron.schedule('ad4_page_cache_'||p_request_id::text,'* * * * *',
    format('set statement_timeout = ''180s''; select arbdesk_private.run_page_cache_request(%L::uuid);',p_request_id));
  return public.page_cache_refresh_status(p_request_id);
end $$;

revoke all on function public.request_page_cache_refresh(uuid),public.page_cache_refresh_status(uuid),
  arbdesk_private.run_page_cache_request(uuid) from public,anon,authenticated;
grant execute on function public.request_page_cache_refresh(uuid),public.page_cache_refresh_status(uuid) to service_role;
notify pgrst,'reload schema';
